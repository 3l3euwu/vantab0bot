"""Consistent, good-looking embeds for every Vantab response."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

import discord

__all__ = [
    "Brand",
    "base_embed",
    "success",
    "error",
    "warning",
    "info",
    "mod_action",
    "field_list",
    "progress_bar",
]

BRAND_NAME = "Vantab"
FOOTER_ICON = "https://cdn.discordapp.com/emojis/1169471604359344138.png"


class Brand:
    """Colour palette (violet / magenta, matching the Vantab identity)."""

    PRIMARY = 0x7C5CFF
    SUCCESS = 0x2ECC71
    ERROR = 0xED4245
    WARNING = 0xFAA61A
    INFO = 0x5865F2
    MUTED = 0x4F545C
    DANGER = 0xFF4D6D


def base_embed(
    description: str | None = None,
    *,
    title: str | None = None,
    colour: int = Brand.PRIMARY,
    timestamp: datetime | None = None,
    footer: str | None = None,
    author: discord.abc.User | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        colour=colour,
        timestamp=timestamp or discord.utils.utcnow(),
    )
    embed.set_footer(
        text=footer or BRAND_NAME,
        icon_url=author.display_avatar.url if author else None,
    )
    return embed


def success(description: str, *, title: str | None = None, **kwargs) -> discord.Embed:
    return base_embed(description, title=title, colour=Brand.SUCCESS, **kwargs)


def error(description: str, *, title: str | None = None, **kwargs) -> discord.Embed:
    return base_embed(description, title=title, colour=Brand.ERROR, **kwargs)


def warning(description: str, *, title: str | None = None, **kwargs) -> discord.Embed:
    return base_embed(description, title=title, colour=Brand.WARNING, **kwargs)


def info(description: str, *, title: str | None = None, **kwargs) -> discord.Embed:
    return base_embed(description, title=title, colour=Brand.INFO, **kwargs)


def mod_action(
    *,
    action: str,
    target: discord.abc.User,
    moderator: discord.abc.User | None,
    reason: str,
    case_id: int | None = None,
    duration: str | None = None,
    colour: int = Brand.PRIMARY,
    extra: Iterable[tuple[str, str, bool]] | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"{action}",
        colour=colour,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="User", value=f"{target.mention}\n`{target.id}`", inline=True)
    embed.add_field(
        name="Moderator",
        value=(moderator.mention if moderator else "Vantab (automod)"),
        inline=True,
    )
    if case_id is not None:
        embed.add_field(name="Case", value=f"`#{case_id}`", inline=True)
    if duration:
        embed.add_field(name="Duration", value=duration, inline=True)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    if extra:
        for name, value, inline in extra:
            embed.add_field(name=name, value=value or "—", inline=inline)
    embed.set_footer(text=f"{BRAND_NAME} · {action.lower()}", icon_url=target.display_avatar.url)
    return embed


def field_list(items: Sequence[str], *, limit: int = 1024) -> str:
    text = "\n".join(items)
    return text[:limit] if text else "—"


def progress_bar(value: int, maximum: int, *, length: int = 10) -> str:
    if maximum <= 0:
        return "▱" * length
    filled = max(0, min(length, round(length * value / maximum)))
    return "▰" * filled + "▱" * (length - filled)
