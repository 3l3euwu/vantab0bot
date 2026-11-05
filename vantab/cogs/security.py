"""Server security: anti-nuke, raid detection and the verification gate.

The anti-nuke module watches the audit log. If one account starts mass-banning,
mass-deleting channels or creating webhooks, Vantab strips its roles and bans it
before the damage spreads.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..checks import error_embed
from ..ui.views import VerifyView
from ..utils.embeds import Brand, base_embed
from ..utils.locks import apply_lock, is_locked, snapshot_lock
from ..utils.text import truncate

if TYPE_CHECKING:  # pragma: no cover
    from ..bot import Vantab

log = logging.getLogger("vantab.cogs.security")

#: How many destructive actions within the window trigger a punishment.
ANTINUKE_LIMITS: dict[str, int] = {
    "ban": 3,
    "kick": 5,
    "channel_delete": 2,
    "role_delete": 2,
    "webhook_create": 3,
    "bot_add": 2,
    "member_prune": 1,
}
ANTINUKE_WINDOW = timedelta(seconds=20)

RAID_JOIN_LIMIT = 8
RAID_JOIN_WINDOW = timedelta(seconds=20)
NEW_ACCOUNT_HOURS = 24

_AUDIT_MAP = {
    discord.AuditLogAction.ban: "ban",
    discord.AuditLogAction.kick: "kick",
    discord.AuditLogAction.channel_delete: "channel_delete",
    discord.AuditLogAction.role_delete: "role_delete",
    discord.AuditLogAction.webhook_create: "webhook_create",
    discord.AuditLogAction.bot_add: "bot_add",
    discord.AuditLogAction.member_prune: "member_prune",
}


class Security(commands.Cog, name="Security"):
    """Anti-nuke, raid protection and verification."""

    def __init__(self, bot: Vantab) -> None:
        self.bot = bot
        self._events: dict[int, dict[int, deque[tuple[discord.utils.UTC, str]]]] = defaultdict(
            lambda: defaultdict(deque)
        )
        self._seen_entries: dict[int, deque[int]] = defaultdict(lambda: deque(maxlen=500))
        self._last_audit: dict[int, discord.utils.UTC] = {}
        self._raid_active: set[int] = set()
        self._raid_locks: dict[int, dict[int, dict[str, bool | None]]] = {}
        self._verify_tasks: set[asyncio.Task] = set()

    async def cog_load(self) -> None:
        self.antinuke_watcher.change_interval(seconds=self.bot.settings.antinuke_poll_seconds)
        self.antinuke_watcher.start()

    async def cog_unload(self) -> None:
        self.antinuke_watcher.cancel()
        for task in list(self._verify_tasks):
            task.cancel()

    @property
    def db(self):
        return self.bot.db

    # --------------------------------------------------------------- anti-nuke
    @tasks.loop(seconds=6.0)
    async def antinuke_watcher(self) -> None:
        await self.bot.wait_until_ready()
        for guild in list(self.bot.guilds):
            try:
                await self._scan_guild(guild)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Anti-nuke scan failed for guild %s", guild.id)

    @antinuke_watcher.before_loop
    async def _before_watcher(self) -> None:
        await self.bot.wait_until_ready()

    async def _scan_guild(self, guild: discord.Guild) -> None:
        settings = await self.bot.guild_settings(guild.id)
        if not settings.antinuke_enabled:
            return
        me = guild.me
        if me is None or not guild.me.guild_permissions.view_audit_log:
            return
        since = self._last_audit.get(guild.id)
        if since is None:
            self._last_audit[guild.id] = discord.utils.utcnow()
            return
        try:
            entries = [
                entry
                async for entry in guild.audit_logs(limit=50, after=since, oldest_first=True)
            ]
        except discord.Forbidden:
            return
        except discord.HTTPException:
            return
        if not entries:
            return
        seen = self._seen_entries[guild.id]
        newest = since
        for entry in entries:
            newest = max(newest, entry.created_at)
            if entry.id in seen:
                continue
            seen.append(entry.id)
            key = _AUDIT_MAP.get(entry.action)
            if key is None or entry.user is None:
                continue
            if entry.user.id == (self.bot.user.id if self.bot.user else 0):
                continue
            await self._register(guild, entry.user.id, key, entry.target.id if entry.target else None)
        # Step back one second so entries created during the scan are not missed;
        # duplicates are filtered by the seen-ids ring buffer.
        self._last_audit[guild.id] = newest - timedelta(seconds=1)

    async def _register(
        self, guild: discord.Guild, executor_id: int, key: str, target_id: int | None
    ) -> None:
        now = discord.utils.utcnow()
        bucket = self._events[guild.id][executor_id]
        bucket.append((now, key))
        while bucket and bucket[0][0] < now - ANTINUKE_WINDOW:
            bucket.popleft()
        try:
            await self.db.record_antinuke_event(guild.id, executor_id, key, target_id)
        except Exception:
            log.debug("Could not persist anti-nuke event", exc_info=True)

        counts: dict[str, int] = defaultdict(int)
        for _, action in bucket:
            counts[action] += 1
        triggered = [action for action, count in counts.items() if count >= ANTINUKE_LIMITS.get(action, 999)]
        if triggered:
            await self._punish_nuker(guild, executor_id, counts, triggered)

    async def _punish_nuker(
        self, guild: discord.Guild, executor_id: int, counts: dict[str, int], triggered: list[str]
    ) -> None:
        settings = await self.bot.guild_settings(guild.id)
        if not settings.antinuke_enabled:
            return
        if executor_id == guild.owner_id:
            return
        whitelist = await self.db.antinuke_whitelist(guild.id)
        if executor_id in whitelist:
            return
        member = guild.get_member(executor_id)
        if member is None or member.id == (self.bot.user.id if self.bot.user else 0):
            return
        detail = ", ".join(f"{action}×{count}" for action, count in counts.items() if count)
        if guild.me is None or member.top_role >= guild.me.top_role:
            await self._alert(
                guild,
                base_embed(
                    f"Detected a possible nuke attempt by {member.mention} (`{member.id}`) but I cannot "
                    f"punish them: their highest role is above mine.\n**Actions:** {detail}",
                    title="🚨 Anti-nuke alert — manual action needed",
                    colour=Brand.ERROR,
                ),
            )
            return

        self._events[guild.id].pop(executor_id, None)
        removed_roles = 0
        for role in list(member.roles):
            if role.is_default():
                continue
            try:
                await member.remove_roles(role, reason="Vantab anti-nuke: stripping permissions")
                removed_roles += 1
            except discord.HTTPException:
                continue
        banned = True
        try:
            await guild.ban(
                member,
                reason=f"Vantab anti-nuke: {detail}"[:512],
                delete_message_seconds=3600,
            )
        except discord.HTTPException:
            banned = False

        embed = base_embed(
            f"**{member}** (`{member.id}`) triggered anti-nuke protection.\n\n"
            f"**Evidence:** {detail} within {int(ANTINUKE_WINDOW.total_seconds())}s\n"
            f"**Roles removed:** {removed_roles}\n"
            f"**Banned:** {'yes' if banned else 'no (missing permissions)'}",
            title="🛡️ Anti-nuke triggered",
            colour=Brand.ERROR,
        )
        case = await self.db.add_case(
            guild.id,
            action="antinuke",
            user_id=member.id,
            moderator_id=self.bot.user.id if self.bot.user else 0,
            reason=truncate(f"Anti-nuke: {detail}", 900),
            automated=True,
        )
        embed.set_footer(text=f"Case #{case.case_number}")
        log.warning("Anti-nuke punished %s in %s: %s", member.id, guild.id, detail)
        await self._alert(guild, embed)
        await self.bot.notify_owners(
            base_embed(
                f"Anti-nuke fired in **{guild.name}** against {member} (`{member.id}`): {detail}",
                title="Anti-nuke report",
                colour=Brand.ERROR,
            )
        )

    # ------------------------------------------------------------------- alerts
    async def _alert(self, guild: discord.Guild, embed: discord.Embed) -> None:
        settings = await self.bot.guild_settings(guild.id)
        channel_id = settings.antinuke_log_channel or settings.mod_log_channel
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            channel = guild.system_channel
        if channel is not None:
            await self.bot.send_embed(channel, embed)  # type: ignore[arg-type]
        await self.bot.log_mod_action(guild, embed)

    # --------------------------------------------------------------------- raid
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        guild = member.guild
        bucket = self._events[guild.id][0]
        now = discord.utils.utcnow()
        bucket.append((now, "join"))
        while bucket and bucket[0][0] < now - RAID_JOIN_WINDOW:
            bucket.popleft()
        joins = len(bucket)
        if joins >= RAID_JOIN_LIMIT:
            bucket.clear()
            await self.auto_raid(guild, f"{joins} joins in {int(RAID_JOIN_WINDOW.total_seconds())}s")

        settings = await self.bot.guild_settings(guild.id)
        if not settings.verify_enabled:
            return
        account_age = discord.utils.utcnow() - member.created_at
        if settings.raid_mode or account_age < timedelta(hours=NEW_ACCOUNT_HOURS):
            self._schedule_verify_watch(member, settings.verify_timeout_seconds, settings.verify_role_id)

    def _schedule_verify_watch(
        self, member: discord.Member, timeout_seconds: int, role_id: int | None
    ) -> None:
        task = asyncio.create_task(self._verify_watch(member, timeout_seconds, role_id))
        self._verify_tasks.add(task)
        task.add_done_callback(self._verify_tasks.discard)

    async def _verify_watch(
        self, member: discord.Member, timeout_seconds: int, role_id: int | None
    ) -> None:
        await asyncio.sleep(max(60, timeout_seconds))
        guild = member.guild
        try:
            fresh = guild.get_member(member.id) or await guild.fetch_member(member.id)
        except (discord.NotFound, discord.HTTPException):
            return
        if role_id and any(role.id == role_id for role in fresh.roles):
            return
        try:
            await fresh.kick(reason="Vantab: did not complete verification in time")
            await self._alert(
                guild,
                base_embed(
                    f"{fresh} (`{fresh.id}`) was kicked for not verifying in time.",
                    title="Verification timeout",
                    colour=Brand.WARNING,
                ),
            )
        except discord.HTTPException:
            log.debug("Could not kick unverified member %s", fresh.id)

    async def auto_raid(self, guild: discord.Guild, reason: str) -> None:
        """Called by the automod engine or by join-rate detection."""
        if guild.id in self._raid_active:
            return
        self._raid_active.add(guild.id)
        try:
            await self.db.update_guild(guild.id, raid_mode=True)
        except Exception:
            log.exception("Could not persist raid mode")
        self.bot.invalidate_settings(guild.id)

        locked = 0
        state: dict[int, dict[str, bool | None]] = {}
        for channel in guild.text_channels:
            overwrite = channel.overwrites_for(guild.default_role)
            state[channel.id] = snapshot_lock(overwrite)
            if is_locked(overwrite):
                continue
            apply_lock(overwrite, locked=True)
            try:
                await channel.set_permissions(
                    guild.default_role, overwrite=overwrite, reason="Vantab raid mode"
                )
                locked += 1
            except discord.HTTPException:
                continue
        self._raid_locks[guild.id] = state

        embed = base_embed(
            f"🚨 **Raid mode engaged**\n**Reason:** {reason}\n"
            f"**Channels locked:** {locked}\n\n"
            "Auto-moderation now watches administrators too. "
            "Use `~raidmode off` once the raid is over.",
            title="Raid protection",
            colour=Brand.ERROR,
        )
        await self._alert(guild, embed)
        await self.bot.notify_owners(
            base_embed(f"Raid mode engaged in **{guild.name}**: {reason}", colour=Brand.ERROR)
        )

    # ------------------------------------------------------------------ commands
    @commands.hybrid_group(
        name="antinuke",
        description="Anti-nuke protection settings.",
        invoke_without_command=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def antinuke(self, ctx: commands.Context) -> None:
        await self._antinuke_status(ctx)

    @antinuke.command(name="status", description="Show anti-nuke status and limits.")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def antinuke_status(self, ctx: commands.Context) -> None:
        await self._antinuke_status(ctx)

    async def _antinuke_status(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        assert guild is not None
        settings = await self.db.get_guild(guild.id)
        whitelist = await self.db.antinuke_whitelist(guild.id)
        embed = base_embed(
            f"Protection is **{'enabled' if settings.antinuke_enabled else 'disabled'}**.\n"
            f"Watching the audit log every **{self.bot.settings.antinuke_poll_seconds:.0f}s**.",
            title="🛡️ Anti-nuke",
            colour=Brand.SUCCESS if settings.antinuke_enabled else Brand.MUTED,
        )
        embed.add_field(
            name="Limits (per 20s)",
            value="\n".join(f"`{action}` — {limit}" for action, limit in ANTINUKE_LIMITS.items()),
            inline=True,
        )
        embed.add_field(
            name="Whitelisted",
            value=", ".join(f"<@{uid}>" for uid in sorted(whitelist)) or "— (owner is always exempt)",
            inline=False,
        )
        if not guild.me.guild_permissions.view_audit_log:
            embed.add_field(
                name="⚠️ Missing permission",
                value="I need **View Audit Log** to protect this server.",
                inline=False,
            )
        await ctx.reply(embed=embed, mention_author=False)

    @antinuke.command(name="enable", description="Enable anti-nuke protection.")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def antinuke_enable(self, ctx: commands.Context) -> None:
        await self.db.update_guild(ctx.guild.id, antinuke_enabled=True)
        self.bot.invalidate_settings(ctx.guild.id)
        await ctx.reply(
            embed=base_embed("Anti-nuke protection **enabled**.", colour=Brand.SUCCESS),
            mention_author=False,
        )

    @antinuke.command(name="disable", description="Disable anti-nuke protection.")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def antinuke_disable(self, ctx: commands.Context) -> None:
        await self.db.update_guild(ctx.guild.id, antinuke_enabled=False)
        self.bot.invalidate_settings(ctx.guild.id)
        await ctx.reply(
            embed=base_embed("Anti-nuke protection **disabled**.", colour=Brand.WARNING),
            mention_author=False,
        )

    @antinuke.group(name="whitelist", description="Accounts anti-nuke must never punish.", invoke_without_command=True)
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def antinuke_whitelist(self, ctx: commands.Context) -> None:
        await self._whitelist_list(ctx)

    @antinuke_whitelist.command(name="add", description="Trust a user with destructive permissions.")
    @app_commands.describe(user="The user to trust")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def whitelist_add(self, ctx: commands.Context, user: discord.User) -> None:
        added = await self.db.add_antinuke_whitelist(ctx.guild.id, user.id)
        await ctx.reply(
            embed=base_embed(
                f"{'Trusted' if added else 'Already trusted:'} **{user}** (`{user.id}`).",
                colour=Brand.SUCCESS if added else Brand.MUTED,
            ),
            mention_author=False,
        )

    @antinuke_whitelist.command(name="remove", description="Remove a user from the anti-nuke whitelist.")
    @app_commands.describe(user="The user to untrust")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def whitelist_remove(self, ctx: commands.Context, user: discord.User) -> None:
        removed = await self.db.remove_antinuke_whitelist(ctx.guild.id, user.id)
        await ctx.reply(
            embed=base_embed(
                f"Removed **{user}** from the whitelist." if removed else "That user was not whitelisted.",
                colour=Brand.SUCCESS if removed else Brand.MUTED,
            ),
            mention_author=False,
        )

    @antinuke_whitelist.command(name="list", description="List whitelisted users.")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def whitelist_list(self, ctx: commands.Context) -> None:
        await self._whitelist_list(ctx)

    async def _whitelist_list(self, ctx: commands.Context) -> None:
        whitelist = await self.db.antinuke_whitelist(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(
                "\n".join(f"• <@{uid}> (`{uid}`)" for uid in sorted(whitelist)) or "Nobody is whitelisted.",
                title="🛡️ Anti-nuke whitelist",
                colour=Brand.PRIMARY,
            ),
            mention_author=False,
        )

    # ----------------------------------------------------------------- raid mode
    @commands.hybrid_command(name="raidmode", description="Manually engage or lift raid mode.")
    @app_commands.describe(state="on or off")
    @app_commands.choices(state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def raidmode(self, ctx: commands.Context, state: str) -> None:
        guild = ctx.guild
        assert guild is not None
        if state.lower() == "on":
            self._raid_active.discard(guild.id)
            await self.auto_raid(guild, f"manually engaged by {ctx.author}")
            await ctx.reply(
                embed=base_embed("🚨 Raid mode engaged.", colour=Brand.ERROR), mention_author=False
            )
            return
        await self.db.update_guild(guild.id, raid_mode=False)
        self.bot.invalidate_settings(guild.id)
        self._raid_active.discard(guild.id)
        state_map = self._raid_locks.pop(guild.id, {})
        restored = 0
        for channel in guild.text_channels:
            overwrite = channel.overwrites_for(guild.default_role)
            previous = state_map.get(channel.id)
            if previous is None:
                if not is_locked(overwrite):
                    continue
                apply_lock(overwrite, locked=False)
            else:
                for permission, value in previous.items():
                    setattr(overwrite, permission, value)
            try:
                await channel.set_permissions(
                    guild.default_role, overwrite=overwrite, reason="Vantab raid mode off"
                )
                restored += 1
            except discord.HTTPException:
                continue
        await ctx.reply(
            embed=base_embed(
                f"🔓 Raid mode lifted — {restored} channel(s) restored.", colour=Brand.SUCCESS
            ),
            mention_author=False,
        )

    # --------------------------------------------------------------- verification
    @commands.hybrid_group(
        name="verify",
        description="Configure the one-click verification gate.",
        invoke_without_command=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def verify(self, ctx: commands.Context) -> None:
        await self._verify_status(ctx)

    @verify.command(name="setup", description="Create the verification gate.")
    @app_commands.describe(channel="Channel where members verify", role="Role granted on verification")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def verify_setup(
        self, ctx: commands.Context, channel: discord.TextChannel, role: discord.Role
    ) -> None:
        guild = ctx.guild
        assert guild is not None
        if role >= guild.me.top_role:
            await ctx.reply(
                embed=error_embed("That role is above mine — move my role higher first."),
                mention_author=False,
            )
            return
        if role.permissions.administrator or role.permissions.manage_guild:
            await ctx.reply(
                embed=error_embed("Use a low-permission role for verification, not an admin role."),
                mention_author=False,
            )
            return
        await self.db.update_guild(
            guild.id,
            verify_enabled=True,
            verify_channel_id=channel.id,
            verify_role_id=role.id,
        )
        self.bot.invalidate_settings(guild.id)
        await channel.send(embed=VerifyView.build_embed(guild), view=VerifyView(self.bot))
        await ctx.reply(
            embed=base_embed(
                f"Verification enabled in {channel.mention} → {role.mention}.",
                colour=Brand.SUCCESS,
            ),
            mention_author=False,
        )

    @verify.command(name="disable", description="Turn verification off.")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def verify_disable(self, ctx: commands.Context) -> None:
        await self.db.update_guild(ctx.guild.id, verify_enabled=False)
        self.bot.invalidate_settings(ctx.guild.id)
        await ctx.reply(
            embed=base_embed("Verification disabled.", colour=Brand.WARNING),
            mention_author=False,
        )

    @verify.command(name="status", description="Show verification settings.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def verify_status(self, ctx: commands.Context) -> None:
        await self._verify_status(ctx)

    async def _verify_status(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        assert guild is not None
        settings = await self.db.get_guild(guild.id)
        if not settings.verify_enabled:
            await ctx.reply(
                embed=base_embed(
                    "Verification is **disabled**.\nUse `~verify setup #channel @role` to enable it.",
                    title="Verification",
                    colour=Brand.MUTED,
                ),
                mention_author=False,
            )
            return
        channel = guild.get_channel(settings.verify_channel_id or 0)
        role = guild.get_role(settings.verify_role_id or 0)
        await ctx.reply(
            embed=base_embed(
                f"Channel: {channel.mention if channel else '*(deleted)*'}\n"
                f"Role: {role.mention if role else '*(deleted)*'}\n"
                f"Kick unverified after: {settings.verify_timeout_seconds}s\n"
                f"Raid mode: {'active' if settings.raid_mode else 'inactive'}",
                title="Verification",
                colour=Brand.SUCCESS,
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="security", description="Security overview: anti-nuke, raid mode, verification.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def security(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        assert guild is not None
        settings = await self.db.get_guild(guild.id)
        whitelist = await self.db.antinuke_whitelist(guild.id)
        embed = base_embed(
            f"Anti-nuke: {'🟢 enabled' if settings.antinuke_enabled else '🔴 disabled'}\n"
            f"Raid mode: {'🚨 **active**' if settings.raid_mode else '🟢 inactive'}\n"
            f"Verification: {'🟢 enabled' if settings.verify_enabled else '🔴 disabled'}\n"
            f"Whitelisted: **{len(whitelist)}** user(s)",
            title="🔐 Server security",
            colour=Brand.ERROR if settings.raid_mode else Brand.SUCCESS,
        )
        embed.add_field(
            name="Permissions",
            value=(
                f"View audit log: {'✅' if guild.me.guild_permissions.view_audit_log else '❌'}\n"
                f"Ban members: {'✅' if guild.me.guild_permissions.ban_members else '❌'}\n"
                f"Manage channels: {'✅' if guild.me.guild_permissions.manage_channels else '❌'}\n"
                f"Manage roles: {'✅' if guild.me.guild_permissions.manage_roles else '❌'}"
            ),
            inline=False,
        )
        await ctx.reply(embed=embed, mention_author=False)


async def setup(bot: Vantab) -> None:
    await bot.add_cog(Security(bot))
