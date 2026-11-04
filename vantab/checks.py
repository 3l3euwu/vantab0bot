"""Permission helpers and user-facing error embeds."""

from __future__ import annotations

from collections.abc import Callable

import discord
from discord.ext import commands

from .utils.embeds import Brand, base_embed

__all__ = [
    "error_embed",
    "success_embed",
    "warning_embed",
    "is_owner",
    "bot_has_permissions",
    "hierarchy_error",
    "can_act_on",
]


def error_embed(message: str, *, title: str | None = None) -> discord.Embed:
    return base_embed(message, title=title or "Something went wrong", colour=Brand.ERROR)


def success_embed(message: str, *, title: str | None = None) -> discord.Embed:
    return base_embed(message, title=title, colour=Brand.SUCCESS)


def warning_embed(message: str, *, title: str | None = None) -> discord.Embed:
    return base_embed(message, title=title, colour=Brand.WARNING)


def is_owner() -> Callable:
    """Allow bot owners (and the application owner) through."""

    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id in getattr(ctx.bot, "owner_ids", set()):
            return True
        if await ctx.bot.is_owner(ctx.author):
            return True
        raise commands.CheckFailure("This command is restricted to the bot owner.")

    return commands.check(predicate)


def bot_has_permissions(**perms: bool) -> Callable:
    """Like ``commands.bot_has_permissions`` but returns a readable failure."""
    return commands.bot_has_permissions(**perms)


def hierarchy_error(
    ctx: commands.Context,
    member: discord.Member,
    *,
    allow_self: bool = False,
    check_bot: bool = True,
) -> str | None:
    """Return a human readable reason why ``member`` cannot be moderated."""
    guild = ctx.guild
    if guild is None:
        return "This command only works in a server."
    if member.id == ctx.bot.user.id:  # type: ignore[union-attr]
        return "I can't moderate myself."
    if not allow_self and member.id == ctx.author.id:
        return "You can't use that on yourself."
    if member.id == guild.owner_id:
        return "The server owner cannot be moderated."
    author = ctx.author
    if (
        isinstance(author, discord.Member)
        and author.id != guild.owner_id
        and member.top_role >= author.top_role
    ):
        return "That member's highest role is equal to or above yours."
    if check_bot and guild.me is not None and member.top_role >= guild.me.top_role:
        return "That member's highest role is above mine — move my role higher."
    return None


def can_act_on(ctx: commands.Context, member: discord.Member) -> discord.Embed | None:
    """Convenience wrapper returning an embed when the action is not allowed."""
    problem = hierarchy_error(ctx, member)
    if problem:
        return error_embed(problem)
    return None
