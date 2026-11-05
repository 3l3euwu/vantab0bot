"""Manual moderation: the commands staff actually use every day.

Every action creates a numbered case, DMs the target when possible and is
mirrored to the moderation log channel.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import defer_if_interaction, error_embed, hierarchy_error
from ..database import CaseEntry
from ..ui.views import ConfirmView
from ..utils.duration import DurationError, format_duration, format_seconds, parse_duration
from ..utils.embeds import Brand, base_embed, mod_action
from ..utils.text import truncate

if TYPE_CHECKING:  # pragma: no cover
    from ..bot import Vantab

log = logging.getLogger("vantab.cogs.moderation")

MAX_PURGE = 1000

_PURGE_FLAG_RE = re.compile(r"(?:^|\s)--purge\s+(\d{1,2})\s*", re.IGNORECASE)


def extract_purge(reason: str) -> tuple[int, str]:
    """Pull a ``--purge N`` flag out of the reason text."""
    match = _PURGE_FLAG_RE.search(reason or "")
    if not match:
        return 0, (reason or "").strip() or "No reason provided"
    days = max(0, min(7, int(match.group(1))))
    cleaned = (reason[: match.start()] + reason[match.end():]).strip()
    return days, cleaned or "No reason provided"


class Moderation(commands.Cog, name="Moderation"):
    """Ban, kick, timeout, warn, purge and lock channels."""

    def __init__(self, bot: Vantab) -> None:
        self.bot = bot
        #: channel_id -> previous send_messages override, used by ~lockdown
        self._locks: dict[int, dict[int, bool | None]] = {}

    # ------------------------------------------------------------------ helpers
    async def _dm(
        self,
        user: discord.abc.User,
        guild: discord.Guild,
        action: str,
        reason: str,
        duration: int = 0,
    ) -> bool:
        if not self.bot.settings.dm_on_punish:
            return False
        embed = base_embed(
            f"You were **{action}** in **{guild.name}**.",
            title="Moderation notice",
            colour=Brand.WARNING,
        )
        embed.add_field(name="Reason", value=truncate(reason, 1000) or "No reason provided", inline=False)
        if duration:
            embed.add_field(name="Duration", value=format_seconds(duration), inline=True)
        embed.set_footer(text=f"{guild.name} · powered by Vantab")
        try:
            await user.send(embed=embed)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def _finish(
        self,
        ctx: commands.Context,
        *,
        action: str,
        target: discord.abc.User,
        reason: str,
        duration: int = 0,
        colour: int = Brand.PRIMARY,
        extra: Iterable[tuple[str, str, bool]] | None = None,
        automated: bool = False,
    ) -> CaseEntry:
        guild = ctx.guild
        assert guild is not None
        moderator = ctx.author
        case = await self.bot.db.add_case(
            guild.id,
            action=action,
            user_id=target.id,
            moderator_id=moderator.id,
            reason=truncate(reason, 900) or "No reason provided",
            duration=duration,
            automated=automated,
        )
        embed = mod_action(
            action=action.title(),
            target=target,
            moderator=moderator,
            reason=truncate(reason, 900) or "No reason provided",
            case_id=case.case_number,
            duration=format_seconds(duration) if duration else None,
            colour=colour,
            extra=extra,
        )
        await self.bot.log_mod_action(guild, embed)
        return case

    async def _respond(self, ctx: commands.Context, embed: discord.Embed) -> None:
        try:
            if ctx.interaction is not None and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(embed=embed, ephemeral=False)
            else:
                await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            pass

    def _parse(self, value: str, *, default_unit: str = "m") -> timedelta | None:
        try:
            return parse_duration(value, default_unit=default_unit)
        except DurationError:
            return None

    # -------------------------------------------------------------------- bans
    @commands.hybrid_command(name="ban", description="Ban a member and open a case.")
    @app_commands.describe(
        user="The member or user ID to ban",
        reason="Why they are being banned — add --purge N to wipe N days of messages",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        ctx: commands.Context,
        user: discord.User,
        *,
        reason: str = "No reason provided",
    ) -> None:
        guild = ctx.guild
        assert guild is not None
        member = guild.get_member(user.id)
        if member is not None:
            problem = hierarchy_error(ctx, member)
            if problem:
                await ctx.reply(embed=error_embed(problem), mention_author=False)
                return
        purge, reason = extract_purge(reason)
        await self._dm(user, guild, "banned", reason)
        try:
            await guild.ban(
                user,
                reason=f"{ctx.author} ({ctx.author.id}): {reason}"[:512],
                delete_message_seconds=purge * 86400,
            )
        except discord.Forbidden:
            await ctx.reply(embed=error_embed("I can't ban that user — check my permissions and role position."), mention_author=False)
            return
        except discord.HTTPException as exc:
            await ctx.reply(embed=error_embed(f"Discord refused the ban: `{exc}`"), mention_author=False)
            return
        case = await self._finish(
            ctx,
            action="ban",
            target=user,
            reason=reason,
            duration=0,
            colour=Brand.ERROR,
            extra=[("Purged", f"{purge} day(s) of messages" if purge else "No messages purged", True)],
        )
        await self._respond(
            ctx,
            mod_action(
                action="Banned",
                target=user,
                moderator=ctx.author,
                reason=reason,
                case_id=case.case_number,
                colour=Brand.ERROR,
            ),
        )

    @commands.hybrid_command(name="unban", description="Unban a user by ID.")
    @app_commands.describe(user="The user to unban", reason="Why the ban is lifted")
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(
        self, ctx: commands.Context, user: discord.User, *, reason: str = "No reason provided"
    ) -> None:
        guild = ctx.guild
        assert guild is not None
        try:
            await guild.unban(user, reason=f"{ctx.author}: {reason}"[:512])
        except discord.NotFound:
            await ctx.reply(embed=error_embed("That user is not banned."), mention_author=False)
            return
        except discord.Forbidden:
            await ctx.reply(embed=error_embed("I lack the **Ban Members** permission."), mention_author=False)
            return
        case = await self._finish(
            ctx, action="unban", target=user, reason=reason, colour=Brand.SUCCESS
        )
        await self._respond(
            ctx,
            mod_action(
                action="Unbanned",
                target=user,
                moderator=ctx.author,
                reason=reason,
                case_id=case.case_number,
                colour=Brand.SUCCESS,
            ),
        )

    @commands.hybrid_command(name="tempban", description="Ban a member for a limited time.")
    @app_commands.describe(
        user="The member or user ID to ban",
        duration="How long, e.g. 1h, 1d, 1w",
        reason="Why — add --purge N to wipe N days of messages",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def tempban(
        self,
        ctx: commands.Context,
        user: discord.User,
        duration: str,
        *,
        reason: str = "No reason provided",
    ) -> None:
        guild = ctx.guild
        assert guild is not None
        delta = self._parse(duration)
        if delta is None or delta.total_seconds() <= 0:
            await ctx.reply(
                embed=error_embed("Invalid duration. Examples: `1h`, `1d`, `1w`."), mention_author=False
            )
            return
        seconds = int(delta.total_seconds())
        member = guild.get_member(user.id)
        if member is not None:
            problem = hierarchy_error(ctx, member)
            if problem:
                await ctx.reply(embed=error_embed(problem), mention_author=False)
                return
        purge, reason = extract_purge(reason)
        await self._dm(user, guild, "temporarily banned", reason, seconds)
        try:
            await guild.ban(
                user,
                reason=f"{ctx.author} ({ctx.author.id}): {reason}"[:512],
                delete_message_seconds=purge * 86400,
            )
        except discord.HTTPException as exc:
            await ctx.reply(embed=error_embed(f"Tempban failed: `{exc}`"), mention_author=False)
            return
        case = await self._finish(
            ctx,
            action="tempban",
            target=user,
            reason=reason,
            duration=seconds,
            colour=Brand.ERROR,
            extra=[("Purged", f"{purge} day(s) of messages" if purge else "No messages purged", True)],
        )
        await self._respond(
            ctx,
            mod_action(
                action="Temporarily banned",
                target=user,
                moderator=ctx.author,
                reason=reason,
                case_id=case.case_number,
                duration=format_duration(delta),
                colour=Brand.ERROR,
            ),
        )

    @commands.hybrid_command(name="banlist", description="List recent bans and their reasons.")
    @app_commands.describe(limit="How many bans to fetch (1-100)")
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def banlist(self, ctx: commands.Context, limit: int = 25) -> None:
        guild = ctx.guild
        assert guild is not None
        limit = max(1, min(100, limit))
        try:
            entries = [entry async for entry in guild.bans(limit=limit)]
        except discord.HTTPException:
            await ctx.reply(embed=error_embed("I could not read the ban list."), mention_author=False)
            return
        if not entries:
            await ctx.reply(embed=base_embed("Nobody is banned. 🎉", colour=Brand.SUCCESS), mention_author=False)
            return
        embed = base_embed(title=f"🔨 Recent bans ({len(entries)})", colour=Brand.ERROR)
        for entry in entries[:20]:
            reason = entry.reason or "No reason recorded"
            embed.add_field(
                name=f"{entry.user} · `{entry.user.id}`",
                value=truncate(reason, 200),
                inline=False,
            )
        if len(entries) > 20:
            embed.set_footer(text=f"Showing 20 of {len(entries)} bans")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="softban", description="Ban then immediately unban to purge messages.")
    @app_commands.describe(user="The member to softban", reason="Reason")
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def softban(
        self, ctx: commands.Context, user: discord.User, *, reason: str = "No reason provided"
    ) -> None:
        guild = ctx.guild
        assert guild is not None
        member = guild.get_member(user.id)
        if member is not None:
            problem = hierarchy_error(ctx, member)
            if problem:
                await ctx.reply(embed=error_embed(problem), mention_author=False)
                return
        try:
            await guild.ban(user, reason=f"Softban: {ctx.author}: {reason}"[:512], delete_message_seconds=604800)
            await guild.unban(user, reason="Softban — automatic unban")
        except discord.HTTPException as exc:
            await ctx.reply(embed=error_embed(f"Softban failed: `{exc}`"), mention_author=False)
            return
        case = await self._finish(
            ctx, action="softban", target=user, reason=reason, colour=Brand.WARNING
        )
        await self._respond(
            ctx,
            mod_action(
                action="Softbanned",
                target=user,
                moderator=ctx.author,
                reason=reason,
                case_id=case.case_number,
                colour=Brand.WARNING,
            ),
        )

    # -------------------------------------------------------------------- kicks
    @commands.hybrid_command(name="kick", description="Kick a member and open a case.")
    @app_commands.describe(member="The member to kick", reason="Why they are being kicked")
    @commands.guild_only()
    @commands.has_guild_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(
        self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"
    ) -> None:
        guild = ctx.guild
        assert guild is not None
        problem = hierarchy_error(ctx, member)
        if problem:
            await ctx.reply(embed=error_embed(problem), mention_author=False)
            return
        await self._dm(member, guild, "kicked", reason)
        try:
            await member.kick(reason=f"{ctx.author}: {reason}"[:512])
        except discord.HTTPException as exc:
            await ctx.reply(embed=error_embed(f"Kick failed: `{exc}`"), mention_author=False)
            return
        case = await self._finish(
            ctx, action="kick", target=member, reason=reason, colour=Brand.DANGER
        )
        await self._respond(
            ctx,
            mod_action(
                action="Kicked",
                target=member,
                moderator=ctx.author,
                reason=reason,
                case_id=case.case_number,
                colour=Brand.DANGER,
            ),
        )

    # ------------------------------------------------------------------ timeouts
    @commands.hybrid_command(
        name="timeout", aliases=["mute", "tempmute"], description="Timeout a member for a duration."
    )
    @app_commands.describe(member="The member to time out", duration="e.g. 10m, 2h, 1d", reason="Reason")
    @commands.guild_only()
    @commands.has_guild_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def timeout(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: str = "10m",
        *,
        reason: str = "No reason provided",
    ) -> None:
        guild = ctx.guild
        assert guild is not None
        problem = hierarchy_error(ctx, member)
        if problem:
            await ctx.reply(embed=error_embed(problem), mention_author=False)
            return
        delta = self._parse(duration)
        if delta is None or delta.total_seconds() <= 0:
            await ctx.reply(
                embed=error_embed("Invalid duration. Examples: `10m`, `2h`, `1d`, `90s`."),
                mention_author=False,
            )
            return
        seconds = int(delta.total_seconds())
        max_seconds = self.bot.settings.max_timeout_minutes * 60
        if seconds > max_seconds:
            await ctx.reply(
                embed=error_embed(f"Maximum timeout is **{format_duration(timedelta(seconds=max_seconds))}**."),
                mention_author=False,
            )
            return
        await self._dm(member, guild, "timed out", reason, seconds)
        try:
            await member.timeout(timedelta(seconds=seconds), reason=f"{ctx.author}: {reason}"[:512])
        except discord.HTTPException as exc:
            await ctx.reply(embed=error_embed(f"Timeout failed: `{exc}`"), mention_author=False)
            return
        case = await self._finish(
            ctx,
            action="timeout",
            target=member,
            reason=reason,
            duration=seconds,
            colour=Brand.WARNING,
        )
        await self._respond(
            ctx,
            mod_action(
                action="Timed out",
                target=member,
                moderator=ctx.author,
                reason=reason,
                case_id=case.case_number,
                duration=format_duration(delta),
                colour=Brand.WARNING,
            ),
        )

    @commands.hybrid_command(name="untimeout", aliases=["unmute"], description="Remove a member's timeout.")
    @app_commands.describe(member="The member whose timeout to lift", reason="Reason")
    @commands.guild_only()
    @commands.has_guild_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def untimeout(
        self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"
    ) -> None:
        try:
            await member.timeout(None, reason=f"{ctx.author}: {reason}"[:512])
        except discord.HTTPException as exc:
            await ctx.reply(embed=error_embed(f"Could not remove the timeout: `{exc}`"), mention_author=False)
            return
        case = await self._finish(
            ctx, action="untimeout", target=member, reason=reason, colour=Brand.SUCCESS
        )
        await self._respond(
            ctx,
            mod_action(
                action="Timeout removed",
                target=member,
                moderator=ctx.author,
                reason=reason,
                case_id=case.case_number,
                colour=Brand.SUCCESS,
            ),
        )

    # -------------------------------------------------------------------- warns
    @commands.hybrid_command(name="warn", description="Warn a member.")
    @app_commands.describe(member="The member to warn", reason="Why")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def warn(
        self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"
    ) -> None:
        guild = ctx.guild
        assert guild is not None
        problem = hierarchy_error(ctx, member, allow_self=False)
        if problem:
            await ctx.reply(embed=error_embed(problem), mention_author=False)
            return
        await self._dm(member, guild, "warned", reason)
        case = await self._finish(
            ctx, action="warn", target=member, reason=reason, colour=Brand.WARNING
        )
        await self.bot.db.add_warning(
            guild.id, member.id, ctx.author.id, truncate(reason, 900), case.case_number
        )
        count = await self.bot.db.count_warnings(guild.id, member.id)
        escalation = await self._check_escalation(ctx, member, count)
        await self._respond(
            ctx,
            mod_action(
                action="Warned",
                target=member,
                moderator=ctx.author,
                reason=reason,
                case_id=case.case_number,
                colour=Brand.WARNING,
                extra=[("Total warnings", str(count), True)]
                + ([( "Auto-escalation", escalation, False)] if escalation else []),
            ),
        )

    async def _check_escalation(
        self, ctx: commands.Context, member: discord.Member, count: int
    ) -> str | None:
        """Apply the configured warn ladder (3 warns = timeout, 5 = kick, …)."""
        guild = ctx.guild
        assert guild is not None
        settings = await self.bot.db.get_guild(guild.id)
        ladder = sorted(
            (step for step in settings.escalation if int(step.get("warns", 0)) > 0),
            key=lambda step: int(step["warns"]),
        )
        applicable = None
        for step in ladder:
            if count >= int(step["warns"]):
                applicable = step
        if applicable is None:
            return None
        action = str(applicable.get("action", "none"))
        if action not in {"timeout", "kick", "ban"}:
            return None
        duration = int(applicable.get("duration", 0) or 0)
        reason = f"Automatic escalation: {count} warnings"
        problem = hierarchy_error(ctx, member)
        if problem:
            return f"Escalation to **{action}** skipped — {problem}"
        try:
            if action == "timeout":
                seconds = duration or 3600
                await member.timeout(timedelta(seconds=seconds), reason=reason)
                detail = f"timed out for {format_seconds(seconds)}"
            elif action == "kick":
                await member.kick(reason=reason)
                detail = "kicked"
            else:
                await guild.ban(member, reason=reason, delete_message_seconds=0)
                detail = "banned"
        except discord.HTTPException as exc:
            return f"Escalation to **{action}** failed: `{exc}`"
        case = await self.bot.db.add_case(
            guild.id,
            action=action,
            user_id=member.id,
            moderator_id=self.bot.user.id if self.bot.user else 0,
            reason=reason + f" (case #{applicable.get('warns')})",
            duration=duration if action == "timeout" else 0,
            automated=True,
        )
        await self.bot.log_mod_action(
            guild,
            mod_action(
                action=f"Auto-escalation · {action.title()}",
                target=member,
                moderator=None,
                reason=f"{count} warnings reached",
                case_id=case.case_number,
                duration=format_seconds(duration) if action == "timeout" and duration else None,
                colour=Brand.DANGER,
            ),
        )
        return f"**{count}** warnings reached → member {detail} automatically."

    @commands.hybrid_command(name="warnings", aliases=["warns"], description="List a member's warnings.")
    @app_commands.describe(member="Whose warnings to show")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def warnings(self, ctx: commands.Context, member: discord.Member) -> None:
        guild = ctx.guild
        assert guild is not None
        entries = await self.bot.db.list_warnings(guild.id, member.id, active_only=True, limit=25)
        if not entries:
            await ctx.reply(
                embed=base_embed(f"**{member}** has no active warnings. 🎉", colour=Brand.SUCCESS),
                mention_author=False,
            )
            return
        embed = base_embed(
            f"**{len(entries)} active warning(s)** for {member.mention}",
            title=f"Warnings · {member}",
            colour=Brand.WARNING,
        )
        for entry in entries[:20]:
            embed.add_field(
                name=f"#{entry.id} · {discord.utils.format_dt(entry.created_at, 'd')}",
                value=truncate(entry.reason, 200) + f"\n> by <@{entry.moderator_id}>",
                inline=False,
            )
        if len(entries) > 20:
            embed.set_footer(text=f"Showing 20 of {len(entries)} warnings")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="delwarn", description="Delete a warning by its ID.")
    @app_commands.describe(warning_id="The warning ID shown in ~warnings")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def delwarn(self, ctx: commands.Context, warning_id: int) -> None:
        guild = ctx.guild
        assert guild is not None
        entry = await self.bot.db.get_warning(warning_id)
        if entry is None or entry.guild_id != guild.id:
            await ctx.reply(embed=error_embed("No warning with that ID."), mention_author=False)
            return
        await self.bot.db.deactivate_warning(warning_id)
        await ctx.reply(
            embed=base_embed(
                f"Deleted warning **#{warning_id}** for <@{entry.user_id}>.", colour=Brand.SUCCESS
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="clearwarns", description="Clear every warning for a member.")
    @app_commands.describe(member="Whose warnings to clear")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def clearwarns(self, ctx: commands.Context, member: discord.Member) -> None:
        guild = ctx.guild
        assert guild is not None
        removed = await self.bot.db.clear_warnings(guild.id, member.id)
        await ctx.reply(
            embed=base_embed(
                f"Cleared **{removed}** warning(s) for {member.mention}.", colour=Brand.SUCCESS
            ),
            mention_author=False,
        )

    # -------------------------------------------------------------------- purge
    @commands.hybrid_command(name="purge", aliases=["clear"], description="Bulk delete recent messages.")
    @app_commands.describe(
        amount="How many messages to scan (1-1000)",
        member="Only delete messages from this member",
        contains="Only delete messages containing this text",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge(
        self,
        ctx: commands.Context,
        amount: int,
        member: discord.Member | None = None,
        *,
        contains: str | None = None,
    ) -> None:
        if not 1 <= amount <= MAX_PURGE:
            await ctx.reply(
                embed=error_embed(f"Choose a number between **1** and **{MAX_PURGE}**."), mention_author=False
            )
            return
        channel = ctx.channel
        assert isinstance(channel, discord.TextChannel)
        # Deleting up to 1000 messages easily exceeds the 3-second window.
        await defer_if_interaction(ctx)

        def check(message: discord.Message) -> bool:
            if member is not None and message.author.id != member.id:
                return False
            return not (contains and contains.lower() not in message.content.lower())

        purge_kwargs: dict[str, object] = {}
        if ctx.message is not None:
            purge_kwargs["before"] = ctx.message
        try:
            deleted = await channel.purge(limit=amount, check=check, **purge_kwargs)
        except discord.Forbidden:
            await ctx.reply(embed=error_embed("I can't delete messages here."), mention_author=False)
            return
        except discord.HTTPException as exc:
            await ctx.reply(embed=error_embed(f"Purge failed: `{exc}`"), mention_author=False)
            return

        filters = []
        if member:
            filters.append(f"from {member.mention}")
        if contains:
            filters.append(f"containing `{truncate(contains, 50)}`")
        embed = base_embed(
            f"🧹 Deleted **{len(deleted)}** message(s)"
            + (f" {' '.join(filters)}" if filters else "")
            + ".",
            colour=Brand.SUCCESS,
        )
        await ctx.send(embed=embed, delete_after=6)
        if ctx.message is not None:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass

    @commands.hybrid_command(name="slowmode", description="Set the channel slowmode.")
    @app_commands.describe(seconds="e.g. 5s, 1m, off")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, seconds: str = "off") -> None:
        channel = ctx.channel
        assert isinstance(channel, discord.TextChannel)
        delta = self._parse(seconds, default_unit="s")
        if delta is None:
            await ctx.reply(
                embed=error_embed("Invalid duration. Try `10s`, `1m`, `30m` or `off`."), mention_author=False
            )
            return
        value = int(delta.total_seconds())
        if value > 21600:
            await ctx.reply(embed=error_embed("Slowmode cannot exceed **6 hours**."), mention_author=False)
            return
        await channel.edit(slowmode_delay=value, reason=f"{ctx.author}: slowmode")
        await ctx.reply(
            embed=base_embed(
                f"🐌 Slowmode set to **{format_seconds(value) or 'off'}** in {channel.mention}.",
                colour=Brand.SUCCESS,
            ),
            mention_author=False,
        )

    # --------------------------------------------------------------------- locks
    async def _set_lock(
        self, channel: discord.TextChannel, locked: bool, *, reason: str
    ) -> list[discord.Role]:
        targets: list[discord.Role] = [channel.guild.default_role]
        for role in channel.guild.roles:
            if role.is_default():
                continue
            overwrite = channel.overwrites_for(role)
            if overwrite.send_messages is True:
                targets.append(role)
        changed: list[discord.Role] = []
        for role in targets:
            overwrite = channel.overwrites_for(role)
            overwrite.send_messages = False if locked else None
            try:
                await channel.set_permissions(role, overwrite=overwrite, reason=reason)
                changed.append(role)
            except discord.HTTPException:
                continue
        return changed

    @commands.hybrid_command(name="lock", description="Prevent members from sending messages in a channel.")
    @app_commands.describe(channel="Channel to lock (defaults to this one)")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def lock(
        self, ctx: commands.Context, channel: discord.TextChannel | None = None
    ) -> None:
        target = channel or ctx.channel
        assert isinstance(target, discord.TextChannel)
        roles = await self._set_lock(target, True, reason=f"Locked by {ctx.author}")
        embed = base_embed(
            f"🔒 Locked {target.mention} for **{len(roles)}** role(s).",
            colour=Brand.WARNING,
        )
        await ctx.reply(embed=embed, mention_author=False)
        await self.bot.log_mod_action(
            ctx.guild,
            base_embed(
                f"{target.mention} locked by {ctx.author.mention}",
                title="Channel locked",
                colour=Brand.WARNING,
            ),
        )

    @commands.hybrid_command(name="unlock", description="Allow members to talk in a channel again.")
    @app_commands.describe(channel="Channel to unlock (defaults to this one)")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlock(
        self, ctx: commands.Context, channel: discord.TextChannel | None = None
    ) -> None:
        target = channel or ctx.channel
        assert isinstance(target, discord.TextChannel)
        roles = await self._set_lock(target, False, reason=f"Unlocked by {ctx.author}")
        embed = base_embed(
            f"🔓 Unlocked {target.mention} for **{len(roles)}** role(s).",
            colour=Brand.SUCCESS,
        )
        await ctx.reply(embed=embed, mention_author=False)
        await self.bot.log_mod_action(
            ctx.guild,
            base_embed(
                f"{target.mention} unlocked by {ctx.author.mention}",
                title="Channel unlocked",
                colour=Brand.SUCCESS,
            ),
        )

    @commands.hybrid_command(name="lockdown", description="Lock every text channel in the server.")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def lockdown(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        assert guild is not None
        state: dict[int, bool | None] = {}
        locked = 0
        for channel in guild.text_channels:
            overwrite = channel.overwrites_for(guild.default_role)
            state[channel.id] = overwrite.send_messages
            if overwrite.send_messages is False:
                continue
            overwrite.send_messages = False
            try:
                await channel.set_permissions(
                    guild.default_role, overwrite=overwrite, reason=f"Lockdown by {ctx.author}"
                )
                locked += 1
            except discord.HTTPException:
                continue
        self._locks[guild.id] = state
        await ctx.reply(
            embed=base_embed(
                f"🚨 **Lockdown engaged** — {locked} channel(s) locked. Use `~unlockdown` to restore.",
                colour=Brand.ERROR,
            ),
            mention_author=False,
        )
        await self.bot.log_mod_action(
            guild,
            base_embed(
                f"Server lockdown engaged by {ctx.author.mention} ({locked} channels).",
                title="Lockdown",
                colour=Brand.ERROR,
            ),
        )

    @commands.hybrid_command(name="unlockdown", description="Restore the channels locked by lockdown.")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlockdown(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        assert guild is not None
        state = self._locks.pop(guild.id, {})
        restored = 0
        for channel in guild.text_channels:
            overwrite = channel.overwrites_for(guild.default_role)
            previous = state.get(channel.id)
            if previous is True:
                overwrite.send_messages = True
            elif previous is None and channel.id in state:
                overwrite.send_messages = None
            elif channel.id not in state and overwrite.send_messages is not False:
                continue
            else:
                overwrite.send_messages = None
            try:
                await channel.set_permissions(
                    guild.default_role, overwrite=overwrite, reason=f"Unlockdown by {ctx.author}"
                )
                restored += 1
            except discord.HTTPException:
                continue
        await ctx.reply(
            embed=base_embed(f"🔓 Lockdown lifted — {restored} channel(s) restored.", colour=Brand.SUCCESS),
            mention_author=False,
        )
        await self.bot.log_mod_action(
            guild,
            base_embed(f"Lockdown lifted by {ctx.author.mention}.", title="Lockdown lifted", colour=Brand.SUCCESS),
        )

    @commands.hybrid_command(name="nuke", description="Clone and replace a channel, deleting its history.")
    @app_commands.describe(channel="Channel to nuke (defaults to this one)")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def nuke(self, ctx: commands.Context, channel: discord.TextChannel | None = None) -> None:
        target = channel or ctx.channel
        assert isinstance(target, discord.TextChannel)
        view = ConfirmView(ctx.author.id, confirm_label="Nuke it")
        prompt = base_embed(
            f"This will **delete** {target.mention} and create a clean clone with the same permissions.\n"
            "This cannot be undone.",
            title="⚠️ Confirm channel nuke",
            colour=Brand.ERROR,
        )
        message = await ctx.send(embed=prompt, view=view)
        await view.wait()
        if not view.value:
            try:
                await message.edit(
                    embed=base_embed("Nuke cancelled.", colour=Brand.MUTED), view=None
                )
            except discord.HTTPException:
                pass
            return
        position = target.position
        try:
            new_channel = await target.clone(reason=f"Nuke by {ctx.author}")
            await new_channel.edit(position=position)
            await target.delete(reason=f"Nuke by {ctx.author}")
        except discord.HTTPException as exc:
            await message.edit(embed=error_embed(f"Nuke failed: `{exc}`"), view=None)
            return
        await new_channel.send(
            embed=base_embed(
                f"💥 Channel nuked by {ctx.author.mention}. Fresh start.",
                colour=Brand.PRIMARY,
            )
        )
        await self.bot.log_mod_action(
            ctx.guild,
            base_embed(
                f"{target.name} nuked by {ctx.author.mention} → {new_channel.mention}",
                title="Channel nuked",
                colour=Brand.ERROR,
            ),
        )

    # -------------------------------------------------------------------- cases
    @commands.hybrid_command(name="case", description="Show a moderation case by number.")
    @app_commands.describe(number="The case number")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def case(self, ctx: commands.Context, number: int) -> None:
        guild = ctx.guild
        assert guild is not None
        entry = await self.bot.db.get_case(guild.id, number)
        if entry is None:
            await ctx.reply(embed=error_embed("No case with that number."), mention_author=False)
            return
        embed = self._case_embed(entry)
        await ctx.reply(embed=embed, mention_author=False)

    def _case_embed(self, entry: CaseEntry) -> discord.Embed:
        embed = base_embed(
            entry.reason or "No reason provided",
            title=f"Case #{entry.case_number} · {entry.action.title()}",
            colour=Brand.PRIMARY,
        )
        embed.add_field(name="User", value=f"<@{entry.user_id}>\n`{entry.user_id}`", inline=True)
        moderator = f"<@{entry.moderator_id}>" if entry.moderator_id else "Vantab"
        embed.add_field(name="Moderator", value=moderator, inline=True)
        embed.add_field(name="When", value=discord.utils.format_dt(entry.created_at, "F"), inline=False)
        if entry.duration:
            embed.add_field(name="Duration", value=format_seconds(entry.duration), inline=True)
        if entry.expires_at:
            embed.add_field(name="Expires", value=discord.utils.format_dt(entry.expires_at, "R"), inline=True)
        embed.add_field(name="Status", value="active" if entry.active else "inactive", inline=True)
        if entry.automated:
            embed.set_footer(text="Vantab · automated action")
        return embed

    @commands.hybrid_command(name="cases", aliases=["history"], description="List a member's moderation history.")
    @app_commands.describe(member="Whose cases to list")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def cases(self, ctx: commands.Context, member: discord.User) -> None:
        guild = ctx.guild
        assert guild is not None
        entries = await self.bot.db.list_cases(guild.id, user_id=member.id, limit=15)
        if not entries:
            await ctx.reply(
                embed=base_embed(f"**{member}** has a clean record.", colour=Brand.SUCCESS),
                mention_author=False,
            )
            return
        embed = base_embed(
            f"Last **{len(entries)}** case(s) for {member.mention}",
            title=f"History · {member}",
            colour=Brand.PRIMARY,
        )
        for entry in entries:
            embed.add_field(
                name=f"#{entry.case_number} · {entry.action} · {discord.utils.format_dt(entry.created_at, 'd')}",
                value=truncate(entry.reason, 180),
                inline=False,
            )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="reason", description="Edit the reason of an existing case.")
    @app_commands.describe(number="Case number", reason="New reason")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def reason(self, ctx: commands.Context, number: int, *, reason: str) -> None:
        guild = ctx.guild
        assert guild is not None
        if not await self.bot.db.update_case_reason(guild.id, number, truncate(reason, 900)):
            await ctx.reply(embed=error_embed("No case with that number."), mention_author=False)
            return
        await ctx.reply(
            embed=base_embed(f"✅ Case **#{number}** updated.", colour=Brand.SUCCESS),
            mention_author=False,
        )

    @commands.hybrid_command(name="modstats", description="Moderation statistics for this server.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def modstats(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        assert guild is not None
        total = await self.bot.db.case_count(guild.id)
        recent = await self.bot.db.list_cases(guild.id, limit=100)
        by_action: dict[str, int] = {}
        automated = 0
        for entry in recent:
            by_action[entry.action] = by_action.get(entry.action, 0) + 1
            automated += int(entry.automated)
        embed = base_embed(
            f"**{total}** cases recorded in **{guild.name}**.",
            title="Moderation statistics",
            colour=Brand.PRIMARY,
        )
        if by_action:
            embed.add_field(
                name="Last 100 cases by type",
                value="\n".join(f"`{name}` — {count}" for name, count in sorted(by_action.items(), key=lambda kv: -kv[1])),
                inline=False,
            )
        embed.add_field(name="Automated", value=f"{automated} / {len(recent)}", inline=True)
        embed.add_field(name="Manual", value=f"{len(recent) - automated} / {len(recent)}", inline=True)
        await ctx.reply(embed=embed, mention_author=False)


async def setup(bot: Vantab) -> None:
    await bot.add_cog(Moderation(bot))
