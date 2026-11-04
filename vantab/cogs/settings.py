"""Per-server configuration commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..automod.rules import rule_config_dict
from ..checks import error_embed
from ..utils.embeds import Brand, base_embed
from ..utils.text import truncate

if TYPE_CHECKING:  # pragma: no cover
    from ..bot import Vantab

log = logging.getLogger("vantab.cogs.settings")


async def style_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    persona = getattr(interaction.client, "persona", None)
    names = getattr(persona, "style_names", []) or []
    current = current.lower()
    return [app_commands.Choice(name=name, value=name) for name in names if current in name][:25]


def manage_guild_only(func):
    func = commands.guild_only()(func)
    func = commands.has_guild_permissions(manage_guild=True)(func)
    return func


class Settings(commands.Cog, name="Settings"):
    """Everything about how Vantab behaves in your server."""

    def __init__(self, bot: Vantab) -> None:
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    # ------------------------------------------------------------------ overview
    @commands.hybrid_command(name="config", aliases=["settings"], description="Show this server's Vantab configuration.")
    @commands.guild_only()
    async def config(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        assert guild is not None
        settings = await self.db.get_guild(guild.id)

        def channel(channel_id: int | None) -> str:
            if not channel_id:
                return "—"
            channel = guild.get_channel(channel_id)
            return channel.mention if channel else f"`{channel_id}` (deleted)"

        def role(role_id: int | None) -> str:
            if not role_id:
                return "—"
            role_obj = guild.get_role(role_id)
            return role_obj.mention if role_obj else f"`{role_id}` (deleted)"

        embed = base_embed(
            f"Prefix: **`{settings.prefix or self.bot.settings.default_prefix}`**",
            title=f"⚙️ Configuration · {guild.name}",
            colour=Brand.PRIMARY,
        )
        embed.add_field(name="Mod log", value=channel(settings.mod_log_channel), inline=True)
        embed.add_field(name="AutoMod log", value=channel(settings.automod_log_channel), inline=True)
        embed.add_field(name="AI channel", value=channel(settings.ai_channel) if settings.ai_channel else "everywhere", inline=True)
        embed.add_field(name="AutoMod", value="🟢 on" if settings.automod_enabled else "🔴 off", inline=True)
        embed.add_field(name="AI", value="🟢 on" if settings.ai_enabled else "🔴 off", inline=True)
        embed.add_field(name="Anti-nuke", value="🟢 on" if settings.antinuke_enabled else "🔴 off", inline=True)
        embed.add_field(name="Verification", value=("🟢 " + channel(settings.verify_channel_id) + " → " + role(settings.verify_role_id)) if settings.verify_enabled else "🔴 off", inline=False)
        embed.add_field(name="Warn decay", value=f"{settings.warn_decay_days} days", inline=True)
        embed.add_field(
            name="Escalation",
            value=", ".join(
                f"{step.get('warns')}→{step.get('action')}" for step in settings.escalation
            ) or "—",
            inline=True,
        )
        embed.add_field(
            name="AI style",
            value=f"`{self.bot.persona.resolve_style(settings.ai_style)}`",
            inline=True,
        )
        embed.add_field(
            name="AI persona",
            value="custom" if settings.ai_system_prompt else f"`{self.bot.persona.bot_name}`",
            inline=True,
        )
        embed.set_footer(text="~help settings · every setting is also a command")
        await ctx.reply(embed=embed, mention_author=False)

    # -------------------------------------------------------------------- prefix
    @commands.hybrid_command(name="setprefix", description="Change the command prefix for this server.")
    @app_commands.describe(prefix="New prefix (1-5 characters)")
    @manage_guild_only
    async def setprefix(self, ctx: commands.Context, prefix: str) -> None:
        prefix = prefix.strip()
        if not 1 <= len(prefix) <= 5:
            await ctx.reply(embed=error_embed("Prefix must be between **1** and **5** characters."), mention_author=False)
            return
        if prefix.startswith("/"):
            await ctx.reply(embed=error_embed("The prefix cannot start with `/`."), mention_author=False)
            return
        await self.db.update_guild(ctx.guild.id, prefix=prefix)
        self.bot.invalidate_settings(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(f"Prefix changed to **`{prefix}`**.", colour=Brand.SUCCESS),
            mention_author=False,
        )

    # ------------------------------------------------------------------ channels
    async def _set_channel(
        self,
        ctx: commands.Context,
        field: str,
        channel: discord.TextChannel | None,
        label: str,
    ) -> None:
        await self.db.update_guild(ctx.guild.id, **{field: channel.id if channel else None})
        self.bot.invalidate_settings(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(
                f"{label}: {channel.mention if channel else '**disabled**'}.",
                colour=Brand.SUCCESS,
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="setlog", description="Set the moderation log channel.")
    @app_commands.describe(channel="Where to send logs (omit to disable)")
    @manage_guild_only
    async def setlog(self, ctx: commands.Context, channel: discord.TextChannel | None = None) -> None:
        await self._set_channel(ctx, "mod_log_channel", channel, "Moderation log")

    @commands.hybrid_command(name="setautomodlog", description="Set the auto-moderation log channel.")
    @app_commands.describe(channel="Where to send automod logs (omit to disable)")
    @manage_guild_only
    async def setautomodlog(self, ctx: commands.Context, channel: discord.TextChannel | None = None) -> None:
        await self._set_channel(ctx, "automod_log_channel", channel, "AutoMod log")

    @commands.hybrid_command(name="setaichannel", description="Restrict the AI to one channel.")
    @app_commands.describe(channel="The AI channel (omit to allow everywhere)")
    @manage_guild_only
    async def setaichannel(self, ctx: commands.Context, channel: discord.TextChannel | None = None) -> None:
        await self._set_channel(ctx, "ai_channel", channel, "AI channel")

    # ------------------------------------------------------------------- toggles
    async def _toggle(self, ctx: commands.Context, field: str, enabled: bool, label: str) -> None:
        await self.db.update_guild(ctx.guild.id, **{field: enabled})
        self.bot.invalidate_settings(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(
                f"{label} is now **{'enabled' if enabled else 'disabled'}**.",
                colour=Brand.SUCCESS if enabled else Brand.MUTED,
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="toggleautomod", description="Enable or disable auto-moderation.")
    @app_commands.describe(state="on or off")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @manage_guild_only
    async def toggleautomod(self, ctx: commands.Context, state: str) -> None:
        await self._toggle(ctx, "automod_enabled", state.lower() == "on", "Auto-moderation")

    @commands.hybrid_command(name="toggleai", description="Enable or disable the AI in this server.")
    @app_commands.describe(state="on or off")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @manage_guild_only
    async def toggleai(self, ctx: commands.Context, state: str) -> None:
        await self._toggle(ctx, "ai_enabled", state.lower() == "on", "The AI")

    @commands.hybrid_command(name="toggleantinuke", description="Enable or disable anti-nuke protection.")
    @app_commands.describe(state="on or off")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @manage_guild_only
    async def toggleantinuke(self, ctx: commands.Context, state: str) -> None:
        await self._toggle(ctx, "antinuke_enabled", state.lower() == "on", "Anti-nuke protection")

    # ----------------------------------------------------------------- AI persona
    @commands.hybrid_command(name="setstyle", description="Choose the AI persona style for this server.")
    @app_commands.describe(style="Style name — see ~persona")
    @app_commands.autocomplete(style=style_autocomplete)
    @manage_guild_only
    async def setstyle(self, ctx: commands.Context, style: str) -> None:
        guild = ctx.guild
        assert guild is not None
        persona = self.bot.persona
        chosen = style.strip().lower()
        if chosen not in persona.styles:
            available = ", ".join(f"`{name}`" for name in persona.style_names)
            await ctx.reply(
                embed=error_embed(f"Unknown style. Available styles: {available}."),
                mention_author=False,
            )
            return
        await self.db.update_guild(guild.id, ai_style=chosen)
        self.bot.invalidate_settings(guild.id)
        preview = persona.render(
            persona.styles[chosen], user_name=ctx.author.display_name
        )
        await ctx.reply(
            embed=base_embed(
                f"AI style set to **{chosen}**.\n> {truncate(preview, 400)}",
                title="🎀 Persona updated",
                colour=Brand.SUCCESS,
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="persona", description="Show the AI persona, greeting and available styles.")
    async def persona(self, ctx: commands.Context) -> None:
        persona = self.bot.persona
        style = self.bot.settings.ai_style
        if ctx.guild is not None:
            settings = await self.bot.db.get_guild(ctx.guild.id)
            style = self.bot.persona.resolve_style(settings.ai_style or style)
        embed = base_embed(
            f"**Name:** `{persona.bot_name}` · {persona.description}\n"
            f"**Active style:** `{style}`\n"
            f"**Greeting:** {persona.greeting_text(self.bot.settings.ai_greeting, user_name=ctx.author.display_name) or '—'}\n"
            f"**Styles:** " + ", ".join(f"`{name}`" for name in persona.style_names),
            title="🎀 AI persona",
            colour=Brand.PRIMARY,
        )
        embed.add_field(
            name="Prompt preview",
            value=f">>> {truncate(persona.render(persona.styles[style], user_name=ctx.author.display_name), 700)}",
            inline=False,
        )
        embed.set_footer(text="Change it with ~setstyle <style> · edit prompts/persona.json for more")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="setaisystem", description="Set a custom system prompt for the AI in this server.")
    @app_commands.describe(prompt="The persona text, or 'reset' to restore the default")
    @manage_guild_only
    async def setaisystem(self, ctx: commands.Context, *, prompt: str) -> None:
        if prompt.strip().lower() == "reset":
            await self.db.update_guild(ctx.guild.id, ai_system_prompt=None)
            self.bot.invalidate_settings(ctx.guild.id)
            await ctx.reply(embed=base_embed("AI persona reset to the default.", colour=Brand.SUCCESS), mention_author=False)
            return
        if len(prompt) > 6000:
            await ctx.reply(embed=error_embed("That prompt is too long (max 6000 characters)."), mention_author=False)
            return
        await self.db.update_guild(ctx.guild.id, ai_system_prompt=prompt)
        self.bot.invalidate_settings(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(
                f"AI persona updated.\n> {truncate(prompt, 300)}",
                colour=Brand.SUCCESS,
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="setwarnedecay", description="Automatically expire warnings after N days.")
    @app_commands.describe(days="Days before a warning stops counting (1-365)")
    @manage_guild_only
    async def setwarnedecay(self, ctx: commands.Context, days: int) -> None:
        if not 1 <= days <= 365:
            await ctx.reply(embed=error_embed("Choose between **1** and **365** days."), mention_author=False)
            return
        await self.db.update_guild(ctx.guild.id, warn_decay_days=days)
        self.bot.invalidate_settings(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(f"Warnings now expire after **{days}** days.", colour=Brand.SUCCESS),
            mention_author=False,
        )

    @commands.hybrid_command(name="exportconfig", description="Export this server's configuration as JSON.")
    @manage_guild_only
    async def exportconfig(self, ctx: commands.Context) -> None:
        import io
        import json

        guild = ctx.guild
        assert guild is not None
        settings = await self.db.get_guild(guild.id)
        rules = await self.db.get_rules(guild.id)
        exemptions = await self.db.get_exemptions(guild.id)
        payload = {
            "guild_id": guild.id,
            "prefix": settings.prefix,
            "mod_log_channel": settings.mod_log_channel,
            "automod_log_channel": settings.automod_log_channel,
            "ai_channel": settings.ai_channel,
            "ai_enabled": settings.ai_enabled,
            "automod_enabled": settings.automod_enabled,
            "antinuke_enabled": settings.antinuke_enabled,
            "escalation": settings.escalation,
            "warn_decay_days": settings.warn_decay_days,
            "rules": {name: rule_config_dict(rule) for name, rule in rules.items()},
            "exemptions": {kind: sorted(ids) for kind, ids in exemptions.items()},
            "badwords": sorted(await self.db.get_badwords(guild.id)),
            "allowlist": sorted(await self.db.get_allowlist(guild.id)),
        }
        buffer = io.BytesIO(json.dumps(payload, indent=2).encode("utf-8"))
        await ctx.reply(
            embed=base_embed("Here is your configuration export.", colour=Brand.SUCCESS),
            file=discord.File(buffer, filename=f"vantab-{guild.id}.json"),
            mention_author=False,
        )


async def setup(bot: Vantab) -> None:
    await bot.add_cog(Settings(bot))
