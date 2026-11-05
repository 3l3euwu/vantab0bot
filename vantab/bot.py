"""The Vantab bot itself: intents, lifecycle, error handling and shared helpers."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from . import __version__
from .ai import AIClient
from .automod import AutoModEngine
from .cache import MessageCache
from .checks import error_embed
from .config import Settings
from .database import Database, GuildSettings, utcnow
from .persona import Persona, load_persona
from .ui.tickets import TicketControlsView, TicketPanelView
from .ui.views import VerifyView
from .utils.embeds import Brand, base_embed

__all__ = ["Vantab"]

log = logging.getLogger("vantab")


def root_cause(error: BaseException) -> BaseException:
    """Peel ``CommandInvokeError`` / ``HybridCommandError`` wrappers."""
    seen: set[int] = set()
    while id(error) not in seen:
        seen.add(id(error))
        for attribute in ("original", "wrapped"):
            inner = getattr(error, attribute, None)
            if isinstance(inner, BaseException):
                error = inner
                break
        else:
            break
    return error


def is_expired_interaction(error: BaseException) -> bool:
    """True for the 404s Discord sends when we answered too late."""
    root = root_cause(error)
    if isinstance(root, discord.InteractionResponded):
        return True
    return isinstance(root, discord.NotFound) and getattr(root, "code", None) == 10062


INITIAL_COGS = (
    "vantab.cogs.ai",
    "vantab.cogs.moderation",
    "vantab.cogs.automod_config",
    "vantab.cogs.settings",
    "vantab.cogs.logging",
    "vantab.cogs.security",
    "vantab.cogs.tickets",
    "vantab.cogs.utility",
    "vantab.cogs.tasks",
)


class Vantab(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.guild_messages = True
        intents.dm_messages = True
        intents.moderation = True

        owner_ids = set(settings.owner_ids)
        super().__init__(
            command_prefix=self._prefix,
            intents=intents,
            help_command=None,
            case_insensitive=True,
            owner_ids=owner_ids or None,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=True, replied_user=False
            ),
            activity=discord.Activity(type=discord.ActivityType.watching, name="over the server"),
            status=discord.Status.online,
        )
        # discord.py leaves ``owner_ids`` as None when nobody configured it;
        # normalise it so membership tests never explode on a fresh install.
        self.owner_ids = frozenset(self.owner_ids or ())
        self.settings = settings
        self.db = Database(settings.database_path)
        self.session: aiohttp.ClientSession | None = None
        self.ai: AIClient | None = None
        self.automod = AutoModEngine(self)
        self.persona: Persona = load_persona(settings.persona_file or None)
        self.messages = MessageCache()
        self.started_at: datetime = utcnow()
        self._ready_once = False
        self._settings_cache: dict[int, tuple[float, GuildSettings]] = {}

    # --------------------------------------------------------------- lifecycle
    async def setup_hook(self) -> None:
        log.info("Connecting to database at %s", self.settings.database_path)
        await self.db.connect()

        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.settings.ai_timeout),
            headers={"User-Agent": "Vantab/1.0"},
        )
        self.ai = AIClient(self.settings, self.session)

        for extension in INITIAL_COGS:
            try:
                await self.load_extension(extension)
                log.debug("Loaded %s", extension)
            except Exception:
                log.exception("Failed to load extension %s", extension)

        # Persistent buttons (survive restarts).
        self.add_view(VerifyView(self))
        self.add_view(TicketPanelView(self))
        self.add_view(TicketControlsView(self))
        self.tree.on_error = self.on_app_command_error

        await self._sync_commands()

    async def _sync_commands(self) -> None:
        try:
            if self.settings.dev_guild_id:
                guild = discord.Object(id=self.settings.dev_guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("Synced %s slash commands to dev guild %s", len(synced), guild.id)
            else:
                synced = await self.tree.sync()
                log.info("Synced %s global slash commands", len(synced))
        except discord.HTTPException:
            log.exception("Slash command sync failed (will retry on next start)")

    async def close(self) -> None:
        log.info("Shutting down Vantab…")
        if not self._ready_once and self.ws is not None:
            log.warning(
                "Vantab never reached READY. Discord dropped the session before login completed — "
                "usual causes, in order: (1) the same bot token is running somewhere else (another "
                "terminal, service manager, host), (2) the token was reset, (3) too many IDENTIFYs "
                "in a short time. Run `python bot.py --check` for diagnostics."
            )
        if self.session is not None and not self.session.closed:
            await self.session.close()
        await self.db.close()
        await super().close()

    async def _run_automod(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if not message.content and not message.attachments:
            return
        try:
            await self.automod.process(message)
        except Exception:  # never let moderation take the bot down
            log.exception("Auto-mod failed on message %s", message.id)

    async def on_message(self, message: discord.Message) -> None:
        """Moderate every message, then let the command layer see it."""
        await self._run_automod(message)
        await super().on_message(message)

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        """Re-check edited messages: sneaking a banned word in must not work."""
        if before.content != after.content:
            await self._run_automod(after)

    async def on_ready(self) -> None:
        self._ready_once = True
        log.info(
            "Vantab %s · code loaded from %s", __version__, Path(__file__).resolve().parent.parent
        )
        log.info(
            "Logged in as %s (%s) · %s guilds · discord.py %s",
            self.user, getattr(self.user, "id", "?"), len(self.guilds), discord.__version__,
        )
        log.info(
            "AI: provider=%s model=%s endpoint=%s (%s)",
            self.settings.ai_provider,
            self.ai.model if self.ai is not None else self.settings.ai_model,
            self.settings.ai_base_url,
            f"credentials: {self.settings.credentials_file}" if self.settings.credentials_file else "environment",
        )
        if self.settings.ai_fallback_models:
            log.info("AI fallbacks: %s", " → ".join(self.settings.ai_fallback_models))
        for guild in self.guilds:
            try:
                await self.db.ensure_guild(guild.id)
            except Exception:
                log.exception("Could not register guild %s", guild.id)
        await self._refresh_presence()

    async def _refresh_presence(self) -> None:
        text = self.settings.status_text.replace("{guilds}", str(len(self.guilds)))
        text = text.replace("{{question}}", "{question}")
        activity_types = {
            "watching": discord.ActivityType.watching,
            "playing": discord.ActivityType.playing,
            "listening": discord.ActivityType.listening,
            "competing": discord.ActivityType.competing,
        }
        activity = discord.Activity(
            type=activity_types.get(self.settings.activity_type, discord.ActivityType.competing),
            name=text[:128] or "over the server",
        )
        try:
            await self.change_presence(activity=activity, status=discord.Status.online)
        except Exception:  # pragma: no cover
            log.debug("Could not update presence", exc_info=True)

    # ------------------------------------------------------------------ prefix
    async def _prefix(self, bot: Vantab, message: discord.Message) -> Iterable[str]:
        """Resolve the prefix for ``message``.

        discord.py calls ``command_prefix(bot, message)``; because this is a
        bound method both arguments are needed here (``bot`` is ``self``).
        """
        default = (self.settings.default_prefix or "~").strip() or "~"
        prefixes = {default, f"{default} "}
        if message.guild is not None:
            try:
                settings = await self.guild_settings(message.guild.id)
                custom = (settings.prefix or "").strip()
                if custom:
                    prefixes = {custom, f"{custom} "}
            except Exception:
                pass
        return commands.when_mentioned_or(*prefixes)(bot, message)

    # ---------------------------------------------------------------- settings
    async def guild_settings(self, guild_id: int, *, force: bool = False) -> GuildSettings:
        """Guild settings with a tiny in-memory cache to keep hot paths cheap."""
        if guild_id == 0:
            return GuildSettings(guild_id=0)
        cached = self._settings_cache.get(guild_id)
        now = utcnow().timestamp()
        if cached and not force and now - cached[0] < 15:
            return cached[1]
        settings = await self.db.get_guild(guild_id)
        self._settings_cache[guild_id] = (now, settings)
        return settings

    def invalidate_settings(self, guild_id: int) -> None:
        self._settings_cache.pop(guild_id, None)
        self.automod.invalidate(guild_id)

    # ------------------------------------------------------------------ helpers
    async def send_embed(
        self, channel: discord.abc.Messageable | None, embed: discord.Embed, **kwargs
    ) -> discord.Message | None:
        if channel is None:
            return None
        try:
            return await channel.send(embed=embed, **kwargs)
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def log_mod_action(self, guild: discord.Guild, embed: discord.Embed) -> None:
        try:
            settings = await self.guild_settings(guild.id)
        except Exception:
            return
        channel_id = settings.mod_log_channel or settings.automod_log_channel
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
        await self.send_embed(channel, embed)  # type: ignore[arg-type]

    async def notify_owners(self, embed: discord.Embed) -> None:
        for owner_id in self.owner_ids:
            user = self.get_user(owner_id)
            if user is None:
                try:
                    user = await self.fetch_user(owner_id)
                except (discord.NotFound, discord.HTTPException):
                    continue
            try:
                await user.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                continue

    @property
    def uptime(self) -> str:
        from .utils.duration import humanize

        return humanize(utcnow() - self.started_at)

    # -------------------------------------------------------------- error hooks
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if is_expired_interaction(error):
            log.debug("Interaction expired before %s answered", ctx.command)
            return
        if isinstance(error, commands.CommandInvokeError) and error.original:
            original = error.original
            if isinstance(original, discord.Forbidden):
                error = original  # type: ignore[assignment]
            elif isinstance(original, discord.HTTPException):
                log.warning("HTTP error in %s: %s", ctx.command, original)
                embed = error_embed(f"Discord refused the action: `{original.status}` {original.text}")
                await self._reply(ctx, embed)
                return
            else:
                log.exception("Command %s crashed", ctx.command, exc_info=original)
                await self._reply(ctx, error_embed("Something went wrong while running that command."))
                return

        if isinstance(error, commands.MissingPermissions):
            embed = error_embed(
                "You need the " + ", ".join(f"**{p.replace('_', ' ').title()}**" for p in error.missing_permissions) + " permission."
            )
        elif isinstance(error, commands.BotMissingPermissions):
            embed = error_embed(
                "I am missing the " + ", ".join(f"**{p.replace('_', ' ').title()}**" for p in error.missing_permissions) + " permission."
            )
        elif isinstance(error, commands.CommandOnCooldown):
            embed = error_embed(f"Slow down — try again in **{error.retry_after:.1f}s**.")
        elif isinstance(error, commands.MissingRequiredArgument):
            embed = error_embed(
                f"Missing argument `{error.param.name}`.\n**Usage:** `{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`"
            )
        elif isinstance(error, commands.BadArgument):
            embed = error_embed(f"{error}\n**Usage:** `{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`")
        elif isinstance(error, commands.CheckFailure):
            embed = error_embed(str(error) or "You are not allowed to use this command.")
        elif isinstance(error, discord.Forbidden):
            embed = error_embed("Discord refused the action — check my role position and permissions.")
        else:
            log.exception("Unhandled command error in %s", ctx.command, exc_info=error)
            embed = error_embed("An unexpected error occurred. It has been logged.")
        await self._reply(ctx, embed)

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Friendly errors for slash commands (including hybrid ones)."""
        if is_expired_interaction(error):
            log.debug("Interaction expired before the command answered")
            return
        original = getattr(error, "original", error)
        if isinstance(original, commands.MissingPermissions):
            missing = ", ".join(p.replace("_", " ").title() for p in original.missing_permissions)
            embed = error_embed(f"You need the **{missing}** permission.")
        elif isinstance(original, commands.BotMissingPermissions):
            missing = ", ".join(p.replace("_", " ").title() for p in original.missing_permissions)
            embed = error_embed(f"I am missing the **{missing}** permission.")
        elif isinstance(original, (app_commands.MissingPermissions,)):
            missing = ", ".join(p.replace("_", " ").title() for p in original.missing_permissions)
            embed = error_embed(f"You need the **{missing}** permission.")
        elif isinstance(original, app_commands.CommandOnCooldown):
            embed = error_embed(f"Slow down — try again in **{original.retry_after:.1f}s**.")
        elif isinstance(original, (commands.CheckFailure, app_commands.CheckFailure)):
            embed = error_embed(str(original) or "You are not allowed to use this command.")
        elif isinstance(original, discord.Forbidden):
            embed = error_embed("Discord refused the action — check my permissions and role position.")
        elif isinstance(original, discord.HTTPException):
            embed = error_embed(f"Discord returned an error: `{original.status}`.")
        else:
            log.exception("Slash command failed", exc_info=original)
            embed = error_embed("An unexpected error occurred. It has been logged.")

        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass

    async def _reply(self, ctx: commands.Context, embed: discord.Embed) -> None:
        try:
            if ctx.interaction is not None and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await ctx.reply(embed=embed, mention_author=False, delete_after=15)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def on_guild_join(self, guild: discord.Guild) -> None:
        try:
            await self.db.ensure_guild(guild.id)
        except Exception:
            log.exception("Could not register guild %s", guild.id)
        log.info("Joined guild %s (%s members)", guild.name, guild.member_count)
        embed = base_embed(
            f"Thanks for adding **Vantab** to **{guild.name}**!\n\n"
            f"• Auto-moderation is **on** by default — tune it with `~automod rules`.\n"
            f"• Ask anything with `~{{your question}}`.\n"
            f"• Set a log channel with `~setlog #channel`.\n"
            f"• `~help` lists every command.",
            title="Vantab is here",
            colour=Brand.PRIMARY,
        )
        channel = guild.system_channel
        if channel is not None and channel.permissions_for(guild.me).send_messages:
            await self.send_embed(channel, embed)
        else:
            try:
                await guild.owner.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info("Removed from guild %s (%s)", guild.name, guild.id)

    async def on_error(self, event_method: str, /, *args, **kwargs) -> None:  # type: ignore[override]
        log.exception("Unhandled error in event %s", event_method)
