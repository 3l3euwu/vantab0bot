"""Background loops that keep Vantab autonomous between restarts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from ..utils.embeds import Brand, base_embed

if TYPE_CHECKING:  # pragma: no cover
    from ..bot import Vantab

log = logging.getLogger("vantab.cogs.tasks")


class BackgroundTasks(commands.Cog, name="BackgroundTasks"):
    """Temporary bans, warning decay, clean-up and status rotation."""

    def __init__(self, bot: Vantab) -> None:
        self.bot = bot
        self._status_index = 0

    async def cog_load(self) -> None:
        self.expire_cases.start()
        self.decay_warnings.start()
        self.housekeeping.start()
        self.rotate_status.start()

    async def cog_unload(self) -> None:
        for loop in (self.expire_cases, self.decay_warnings, self.housekeeping, self.rotate_status):
            loop.cancel()

    # ------------------------------------------------------------------ tempbans
    @tasks.loop(seconds=45)
    async def expire_cases(self) -> None:
        try:
            expired = await self.bot.db.expired_cases()
        except Exception:
            log.exception("Could not fetch expired cases")
            return
        for case in expired:
            guild = self.bot.get_guild(case.guild_id)
            if guild is None:
                continue
            try:
                if case.action in {"ban", "tempban"}:
                    try:
                        await guild.unban(
                            discord.Object(id=case.user_id),
                            reason=f"Temporary ban expired (case #{case.case_number})",
                        )
                    except discord.NotFound:
                        pass
                    except discord.HTTPException:
                        log.debug("Could not unban %s in %s", case.user_id, guild.id)
                await self.bot.db.deactivate_case(guild.id, case.case_number)
                if case.action in {"ban", "tempban"}:
                    await self.bot.log_mod_action(
                        guild,
                        base_embed(
                            f"Temporary ban for <@{case.user_id}> expired (case **#{case.case_number}**).",
                            title="⏳ Tempban expired",
                            colour=Brand.SUCCESS,
                        ),
                    )
            except Exception:
                log.exception("Failed to expire case #%s in %s", case.case_number, guild.id)

    @expire_cases.before_loop
    async def _before_expire(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ warnings
    @tasks.loop(hours=6)
    async def decay_warnings(self) -> None:
        for guild in list(self.bot.guilds):
            try:
                settings = await self.bot.db.get_guild(guild.id)
                removed = await self.bot.db.decay_warnings(guild.id, settings.warn_decay_days)
                if removed:
                    log.info("Decayed %s warning(s) in %s", removed, guild.id)
            except Exception:
                log.exception("Warning decay failed for guild %s", guild.id)

    @decay_warnings.before_loop
    async def _before_decay(self) -> None:
        await self.bot.wait_until_ready()

    # -------------------------------------------------------------- housekeeping
    @tasks.loop(minutes=10)
    async def housekeeping(self) -> None:
        try:
            pruned = await self.bot.db.prune_ai_history(self.bot.settings.ai_history_ttl_days)
            if pruned:
                log.info("Pruned %s old AI message(s)", pruned)
            await self.bot.db.prune_antinuke_events(7)
        except Exception:
            log.exception("Database housekeeping failed")
        self.bot.automod.prune()

    @housekeeping.before_loop
    async def _before_housekeeping(self) -> None:
        await self.bot.wait_until_ready()

    # -------------------------------------------------------------------- status
    @tasks.loop(minutes=10)
    async def rotate_status(self) -> None:
        await self.bot.wait_until_ready()
        statuses = [
            discord.Activity(type=discord.ActivityType.watching, name=f"over {len(self.bot.guilds)} servers"),
            discord.Activity(type=discord.ActivityType.listening, name="~{your question}"),
            discord.Activity(type=discord.ActivityType.watching, name="for rule breakers"),
            discord.Activity(type=discord.ActivityType.playing, name=f"{len(self.bot.commands)} commands"),
        ]
        activity = statuses[self._status_index % len(statuses)]
        self._status_index += 1
        try:
            await self.bot.change_presence(activity=activity, status=discord.Status.online)
        except Exception:
            pass

    @rotate_status.before_loop
    async def _before_status(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: Vantab) -> None:
    await bot.add_cog(BackgroundTasks(bot))
