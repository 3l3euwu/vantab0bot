"""Safe wrappers around Discord moderation endpoints.

Every function here returns an :class:`Outcome` instead of raising, so the
automod engine can always report *why* something failed without crashing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import discord

__all__ = [
    "Outcome",
    "hierarchy_problem",
    "timeout_member",
    "kick_member",
    "ban_member",
    "unban_user",
    "remove_timeout",
    "dm_user",
]


@dataclass(slots=True)
class Outcome:
    ok: bool
    detail: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.ok


def hierarchy_problem(guild: discord.Guild, member: discord.Member) -> str | None:
    """Return a human readable reason why Vantab cannot moderate ``member``."""
    me = guild.me
    if me is None:
        return "I am not in this server."
    if member.id == guild.owner_id:
        return "that user owns the server."
    if member.id == me.id:
        return "that is me."
    if me.top_role <= member.top_role and not me.guild_permissions.administrator:
        return "their highest role is above mine."
    return None


async def timeout_member(member: discord.Member, seconds: int, reason: str) -> Outcome:
    if seconds <= 0:
        return Outcome(False, "invalid timeout duration")
    problem = hierarchy_problem(member.guild, member)
    if problem:
        return Outcome(False, problem)
    if not member.guild.me.guild_permissions.moderate_members:
        return Outcome(False, "I lack the **Moderate Members** permission.")
    try:
        await member.timeout(timedelta(seconds=seconds), reason=reason[:512])
    except discord.Forbidden:
        return Outcome(False, "Discord refused the timeout (missing permissions).")
    except discord.HTTPException as exc:
        return Outcome(False, f"Discord error: {exc}")
    return Outcome(True, f"timed out for {seconds}s")


async def remove_timeout(member: discord.Member, reason: str = "Timeout removed") -> Outcome:
    if not member.guild.me.guild_permissions.moderate_members:
        return Outcome(False, "I lack the **Moderate Members** permission.")
    try:
        await member.timeout(None, reason=reason[:512])
    except discord.Forbidden:
        return Outcome(False, "Discord refused to remove the timeout.")
    except discord.HTTPException as exc:
        return Outcome(False, f"Discord error: {exc}")
    return Outcome(True, "timeout removed")


async def kick_member(member: discord.Member, reason: str) -> Outcome:
    problem = hierarchy_problem(member.guild, member)
    if problem:
        return Outcome(False, problem)
    if not member.guild.me.guild_permissions.kick_members:
        return Outcome(False, "I lack the **Kick Members** permission.")
    try:
        await member.kick(reason=reason[:512])
    except discord.Forbidden:
        return Outcome(False, "Discord refused the kick (missing permissions).")
    except discord.HTTPException as exc:
        return Outcome(False, f"Discord error: {exc}")
    return Outcome(True, "kicked")


async def ban_member(
    member: discord.Member | discord.User,
    reason: str,
    *,
    guild: discord.Guild | None = None,
    delete_message_seconds: int = 0,
) -> Outcome:
    if guild is None:
        guild = member.guild if isinstance(member, discord.Member) else None
    if guild is None:
        return Outcome(False, "no guild context for that ban")
    if isinstance(member, discord.Member):
        problem = hierarchy_problem(guild, member)
        if problem:
            return Outcome(False, problem)
    if not guild.me.guild_permissions.ban_members:
        return Outcome(False, "I lack the **Ban Members** permission.")
    try:
        await guild.ban(
            member,
            reason=reason[:512],
            delete_message_seconds=max(0, min(604800, delete_message_seconds)),
        )
    except discord.Forbidden:
        return Outcome(False, "Discord refused the ban (missing permissions).")
    except discord.HTTPException as exc:
        return Outcome(False, f"Discord error: {exc}")
    return Outcome(True, "banned")


async def unban_user(guild: discord.Guild, user: discord.User | discord.Object, reason: str) -> Outcome:
    if not guild.me.guild_permissions.ban_members:
        return Outcome(False, "I lack the **Ban Members** permission.")
    try:
        await guild.unban(user, reason=reason[:512])
    except discord.NotFound:
        return Outcome(False, "that user is not banned.")
    except discord.Forbidden:
        return Outcome(False, "Discord refused the unban (missing permissions).")
    except discord.HTTPException as exc:
        return Outcome(False, f"Discord error: {exc}")
    return Outcome(True, "unbanned")


async def dm_user(user: discord.abc.User, embed: discord.Embed) -> bool:
    """Best-effort DM; returns whether it was delivered."""
    try:
        await user.send(embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False
