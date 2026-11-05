"""The always-on moderation loop.

``AutoModEngine.process()`` is called for every message. It decides whether the
message breaks a rule, how harsh the response should be (with escalating
punishments for repeat offenders) and applies it. No human input required.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import discord

from ..database import Database, GuildSettings, RuleConfig, utcnow
from ..utils.duration import format_seconds
from ..utils.embeds import Brand, base_embed
from ..utils.text import truncate
from .actions import Outcome, ban_member, dm_user, kick_member, timeout_member
from .rules import (
    RULES,
    Detection,
    MessageContext,
    ViolationTracker,
    bump_action,
    evaluate,
    max_action,
    rule_config_dict,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..bot import Vantab

__all__ = ["AutoModEngine", "AutoModResult"]

log = logging.getLogger("vantab.automod")

CONFIG_TTL = timedelta(seconds=20)
RAID_PUNISH_WINDOW = timedelta(seconds=60)
RAID_PUNISH_USERS = 5


@dataclass(slots=True)
class GuildAutomodConfig:
    settings: GuildSettings
    rules: dict[str, RuleConfig]
    exemptions: dict[str, set[int]]
    badwords: set[str]
    allowlist: set[str]
    loaded_at: datetime


@dataclass(slots=True)
class AutoModResult:
    action: str
    detections: list[Detection]
    duration: int
    punished: bool
    outcome: Outcome | None = None
    case_number: int | None = None


class AutoModEngine:
    def __init__(self, bot: Vantab) -> None:
        self.bot = bot
        self.tracker = ViolationTracker()
        self._cache: dict[int, GuildAutomodConfig] = {}
        self._punishments: dict[int, deque[tuple[datetime, int]]] = defaultdict(deque)
        self._raid_announced: set[int] = set()

    # ------------------------------------------------------------------ config
    @property
    def db(self) -> Database:
        return self.bot.db

    async def config(self, guild_id: int, *, force: bool = False) -> GuildAutomodConfig:
        cached = self._cache.get(guild_id)
        now = utcnow()
        if cached and not force and now - cached.loaded_at < CONFIG_TTL:
            return cached
        settings = await self.db.get_guild(guild_id)
        rules = await self.db.get_rules(guild_id)
        exemptions = await self.db.get_exemptions(guild_id)
        badwords = await self.db.get_badwords(guild_id)
        allowlist = await self.db.get_allowlist(guild_id)
        config = GuildAutomodConfig(settings, rules, exemptions, badwords, allowlist, now)
        self._cache[guild_id] = config
        return config

    def invalidate(self, guild_id: int | None = None) -> None:
        if guild_id is None:
            self._cache.clear()
        else:
            self._cache.pop(guild_id, None)

    def rule_action(self, config: GuildAutomodConfig, rule: str) -> str:
        configured = config.rules.get(rule)
        if configured is not None:
            return configured.action
        spec = RULES.get(rule)
        return spec.default_action if spec else "delete"

    def rule_duration(self, config: GuildAutomodConfig, rule: str) -> int:
        configured = config.rules.get(rule)
        if configured is not None and configured.duration:
            return configured.duration
        spec = RULES.get(rule)
        return spec.default_duration if spec else 0

    # ------------------------------------------------------------------ bypass
    def exemption_for(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        channel: discord.abc.GuildChannel | None,
        config: GuildAutomodConfig,
        is_bot: bool = False,
    ) -> str | None:
        """Why ``member`` is not checked in ``channel`` (``None`` = they are checked)."""
        if is_bot:
            return "bot"
        owner_ids = self.bot.owner_ids or ()
        if member.id == guild.owner_id or member.id in owner_ids:
            return "owner"
        if member.id in (self.bot.settings.owner_ids or ()):
            return "owner"
        if channel is not None and channel.id in config.exemptions.get("channel", set()):
            return "channel-exempt"
        if member.id in config.exemptions.get("user", set()):
            return "user-exempt"
        role_ids = {role.id for role in getattr(member, "roles", [])}
        if role_ids & config.exemptions.get("role", set()):
            return "role-exempt"
        if channel is not None and not config.settings.raid_mode:
            try:
                perms = channel.permissions_for(member)
            except (TypeError, AttributeError):
                perms = None
            if perms is not None and (
                perms.administrator or perms.manage_messages or perms.manage_guild
            ):
                return "moderator"
        return None

    def bypass_reason(
        self, message: discord.Message, config: GuildAutomodConfig
    ) -> str | None:
        guild = message.guild
        assert guild is not None
        if message.webhook_id:
            return "webhook"
        return self.exemption_for(
            guild=guild,
            member=message.author,
            channel=message.channel,
            config=config,
            is_bot=message.author.bot,
        )

    # Backwards-compatible private alias.
    _bypass = bypass_reason

    # ----------------------------------------------------------------- context
    def build_context(self, message: discord.Message) -> MessageContext:
        guild = message.guild
        assert guild is not None
        now = utcnow()
        created = message.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=now.tzinfo)
        author = message.author
        account_age: timedelta | None = None
        author_created = getattr(author, "created_at", None)
        if author_created is not None:
            account_age = now - author_created
        member = author if getattr(author, "guild", None) is not None else None
        everyone_allowed = False
        if member is not None:
            try:
                everyone_allowed = bool(message.channel.permissions_for(member).mention_everyone)
            except (TypeError, AttributeError):
                everyone_allowed = False
        attachment_names = tuple(att.filename for att in message.attachments)
        return MessageContext(
            guild_id=guild.id,
            channel_id=message.channel.id,
            author_id=author.id,
            content=message.content or "",
            created_at=created,
            mention_count=len({m.id for m in message.mentions}),
            role_mention_count=len(message.role_mentions),
            everyone_mention=bool(message.mention_everyone) and not everyone_allowed,
            attachment_count=len(message.attachments),
            attachment_names=attachment_names,
            embed_count=len(message.embeds),
            sticker_count=len(message.stickers),
            author_is_bot=author.bot,
            author_is_webhook=message.webhook_id is not None,
            account_age=account_age,
            is_reply=message.reference is not None,
            channel_nsfw=bool(getattr(message.channel, "nsfw", False)),
        )

    # ----------------------------------------------------------------- process
    async def process(self, message: discord.Message) -> AutoModResult | None:
        if message.guild is None or message.is_system():
            return None
        if not message.content and not message.attachments:
            return None

        try:
            config = await self.config(message.guild.id)
        except Exception:  # pragma: no cover - database hiccup
            log.exception("Failed to load automod config for guild %s", message.guild.id)
            return None

        if not config.settings.automod_enabled:
            return None
        bypass = self.bypass_reason(message, config)
        if bypass:
            log.debug(
                "automod: skipped %s in guild %s (%s)",
                message.author.id,
                message.guild.id,
                bypass,
            )
            return None

        ctx = self.build_context(message)
        detections = evaluate(
            ctx,
            rules={name: rule_config_dict(rule) for name, rule in config.rules.items()},
            badwords=config.badwords,
            allowlist=config.allowlist,
            tracker=self.tracker,
        )
        if not detections:
            return None

        action = max_action(*(self.rule_action(config, d.rule) for d in detections))
        if action == "none":
            return None

        punishable = action in {"warn", "timeout", "kick", "ban"}
        duration = max(
            (self.rule_duration(config, d.rule) for d in detections),
            default=0,
        )
        if action == "timeout" and duration <= 0:
            duration = 600

        if punishable:
            action, duration = self._escalate(
                message, ctx, config, action, duration, detections
            )

        result = await self._apply(message, action, duration, detections, config)
        if result is not None and result.punished:
            await self._maybe_raid(message.guild, message.author.id)
        return result

    def _escalate(
        self,
        message: discord.Message,
        ctx: MessageContext,
        config: GuildAutomodConfig,
        action: str,
        duration: int,
        detections: list[Detection],
    ) -> tuple[str, int]:
        guild = message.guild
        assert guild is not None
        strikes = self.tracker.add_strike(guild.id, ctx.author_id, ctx.created_at)
        bump = 0
        if strikes >= 6:
            bump = 2
        elif strikes >= 3:
            bump = 1
        if ctx.account_age is not None and ctx.account_age < timedelta(days=7) and action in {"delete", "warn"}:
            bump = max(bump, 1)
        if bump:
            new_action = bump_action(action, bump)
            if new_action != action:
                log.info(
                    "Escalating %s -> %s for %s in guild %s (%s strikes)",
                    action, new_action, ctx.author_id, guild.id, strikes,
                )
                action = new_action
                if duration <= 0 and action in {"timeout", "warn"}:
                    duration = 1800
                elif action == "ban":
                    duration = 0
        return action, duration

    async def _apply(
        self,
        message: discord.Message,
        action: str,
        duration: int,
        detections: list[Detection],
        config: GuildAutomodConfig,
    ) -> AutoModResult:
        guild = message.guild
        author = message.author
        assert guild is not None

        reason = "; ".join(d.reason for d in detections[:3])
        rules_fired = ", ".join(sorted({d.rule for d in detections}))
        full_reason = f"Automod ({rules_fired}): {reason}"

        # Always remove the offending message.
        deleted = False
        try:
            await message.delete()
            deleted = True
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            deleted = False

        outcome: Outcome | None = None
        case_number: int | None = None
        punished = action in {"warn", "timeout", "kick", "ban"}
        warning_added = False

        member = author if getattr(author, "guild", None) is not None else None
        if action == "timeout" and member is not None:
            outcome = await timeout_member(member, max(60, duration), full_reason)
        elif action == "kick" and member is not None:
            outcome = await kick_member(member, full_reason)
        elif action == "ban":
            outcome = await ban_member(
                member or author,  # type: ignore[arg-type]
                full_reason,
                guild=guild,
                delete_message_seconds=3600,
            )
        elif action == "warn":
            outcome = Outcome(True, "warned")

        if punished:
            try:
                case = await self.db.add_case(
                    guild.id,
                    action=action,
                    user_id=author.id,
                    moderator_id=self.bot.user.id if self.bot.user else 0,
                    reason=truncate(full_reason, 900),
                    duration=duration if action in {"timeout", "ban"} else 0,
                    automated=True,
                )
                case_number = case.case_number
            except Exception:
                log.exception("Failed to record automod case")

        if punished:
            try:
                await self.db.add_warning(
                    guild.id,
                    author.id,
                    self.bot.user.id if self.bot.user else 0,
                    truncate(full_reason, 900),
                    case_number,
                )
                warning_added = True
            except Exception:
                log.exception("Failed to record automod warning")

        if self.bot.settings.dm_on_punish and punished and member is not None:
            await dm_user(member, self._dm_embed(guild, action, duration, reason))

        await self._log(
            guild, config, author, message.channel, action, duration, detections, case_number, outcome
        )

        if action in {"timeout", "warn", "kick", "ban"}:
            await self._notice(message, action, duration)

        log.info(
            "Automod %s on %s in %s (guild %s): %s [deleted=%s, warning=%s, case=%s]",
            action, author.id, message.channel.id, guild.id, reason, deleted, warning_added, case_number,
        )
        return AutoModResult(
            action=action,
            detections=detections,
            duration=duration,
            punished=punished,
            outcome=outcome,
            case_number=case_number,
        )

    # ------------------------------------------------------------------- logs
    def _dm_embed(self, guild: discord.Guild, action: str, duration: int, reason: str) -> discord.Embed:
        verbs = {"warn": "warned", "timeout": "timed out", "kick": "kicked", "ban": "banned"}
        embed = base_embed(
            f"You were **{verbs.get(action, action)}** in **{guild.name}**.",
            title="Vantab · Moderation",
            colour=Brand.WARNING,
        )
        embed.add_field(name="Reason", value=truncate(reason, 1000), inline=False)
        if duration and action == "timeout":
            embed.add_field(name="Duration", value=format_seconds(duration), inline=True)
        embed.add_field(name="Appeal", value="Contact the server staff if you believe this is a mistake.", inline=False)
        return embed

    async def _log(
        self,
        guild: discord.Guild,
        config: GuildAutomodConfig,
        author: discord.abc.User,
        source_channel: discord.abc.GuildChannel,
        action: str,
        duration: int,
        detections: list[Detection],
        case_number: int | None,
        outcome: Outcome | None,
    ) -> None:
        channel_id = config.settings.automod_log_channel or config.settings.mod_log_channel
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        colour = {
            "delete": Brand.MUTED,
            "warn": Brand.WARNING,
            "timeout": Brand.WARNING,
            "kick": Brand.DANGER,
            "ban": Brand.ERROR,
        }.get(action, Brand.PRIMARY)
        embed = base_embed(
            f"**{author.mention}** (`{author.id}`) · action: **{action}**"
            + (f" · duration: **{format_seconds(duration)}**" if duration and action == "timeout" else ""),
            title="AutoMod triggered",
            colour=colour,
        )
        embed.add_field(
            name="Rules",
            value="\n".join(
                f"`{d.rule}` — {truncate(d.reason, 120)}" + (f"\n> {truncate(d.evidence, 80)}" if d.evidence else "")
                for d in detections[:5]
            ),
            inline=False,
        )
        embed.add_field(name="Channel", value=getattr(source_channel, "mention", "—"), inline=True)
        if case_number is not None:
            embed.add_field(name="Case", value=f"`#{case_number}`", inline=True)
        if outcome is not None and not outcome.ok:
            embed.add_field(name="⚠️ Action failed", value=truncate(outcome.detail, 200), inline=False)
        embed.set_thumbnail(url=author.display_avatar.url)
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Cannot write automod log in guild %s", guild.id)

    async def _notice(self, message: discord.Message, action: str, duration: int) -> None:
        """Short, self-deleting channel notice so members understand what happened."""
        if not isinstance(message.channel, discord.TextChannel):
            return
        texts = {
            "warn": "⚠️ {user}, that message broke the rules — warning issued.",
            "timeout": f"🔇 {{user}} has been timed out for {format_seconds(duration)}.",
            "kick": "👢 {user} has been kicked by AutoMod.",
            "ban": "🔨 {user} has been banned by AutoMod.",
        }
        text = texts.get(action)
        if not text:
            return
        try:
            await message.channel.send(
                text.format(user=message.author.mention),
                allowed_mentions=discord.AllowedMentions(users=True),
                delete_after=8,
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ------------------------------------------------------------------ raids
    async def _maybe_raid(self, guild: discord.Guild, user_id: int) -> None:
        bucket = self._punishments[guild.id]
        now = utcnow()
        bucket.append((now, user_id))
        while bucket and bucket[0][0] < now - RAID_PUNISH_WINDOW:
            bucket.popleft()
        distinct = {uid for _, uid in bucket}
        if len(distinct) >= RAID_PUNISH_USERS and guild.id not in self._raid_announced:
            self._raid_announced.add(guild.id)
            log.warning("Possible raid in guild %s: %s users punished in 60s", guild.id, len(distinct))
            try:
                security = self.bot.get_cog("Security")
                if security is not None:
                    await security.auto_raid(  # type: ignore[attr-defined]
                        guild, f"{len(distinct)} members punished by AutoMod within {int(RAID_PUNISH_WINDOW.total_seconds())}s"
                    )
            except Exception:
                log.exception("Failed to trigger raid mode")
            # Reset so we can detect the next wave later.
            self._raid_announced.discard(guild.id)
            bucket.clear()

    def prune(self) -> None:
        self.tracker.prune()
        now = utcnow()
        for guild_id in list(self._punishments):
            bucket = self._punishments[guild_id]
            while bucket and bucket[0][0] < now - RAID_PUNISH_WINDOW:
                bucket.popleft()
            if not bucket:
                self._punishments.pop(guild_id, None)
