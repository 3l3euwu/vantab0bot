"""The automod panel must be able to explain why a message was not filtered."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import discord

from vantab.bot import Vantab
from vantab.cogs.automod_config import EXEMPTION_LABELS


def _config(*, raid_mode: bool = False, exemptions: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(raid_mode=raid_mode),
        exemptions=exemptions or {},
    )


def _member(member_id: int, roles: tuple[int, ...] = (), bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=member_id,
        roles=[SimpleNamespace(id=role_id) for role_id in roles],
        bot=bot,
    )


def _ctx(member, *, guild=None, channel=None) -> SimpleNamespace:
    return SimpleNamespace(
        guild=guild if guild is not None else SimpleNamespace(owner_id=1),
        author=member,
        channel=channel,
    )


def test_exemption_labels_cover_engine_reasons() -> None:
    for reason in ("owner", "moderator", "channel-exempt", "user-exempt", "role-exempt", "bot"):
        assert reason in EXEMPTION_LABELS


def test_server_owner_is_exempt(bot: Vantab) -> None:
    cog = bot.get_cog("AutoMod")
    ctx = _ctx(_member(42), guild=SimpleNamespace(owner_id=42))
    assert cog._exemption(ctx, _config()) == "owner"


def test_configured_bot_owner_is_exempt(bot: Vantab) -> None:
    # The fixture builds the bot with owner_ids={1234}.
    cog = bot.get_cog("AutoMod")
    assert cog._exemption(_ctx(_member(1234)), _config()) == "owner"


def test_user_and_role_exemptions(bot: Vantab) -> None:
    cog = bot.get_cog("AutoMod")
    config = _config(exemptions={"user": {7}, "role": {11}})
    assert cog._exemption(_ctx(_member(7, roles=(11,))), config) == "user-exempt"
    assert cog._exemption(_ctx(_member(8, roles=(11,))), config) == "role-exempt"
    assert cog._exemption(_ctx(_member(8)), config) is None


def test_channel_exemption(bot: Vantab) -> None:
    cog = bot.get_cog("AutoMod")
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 3
    config = _config(exemptions={"channel": {3}})
    assert cog._exemption(_ctx(_member(9), channel=channel), config) == "channel-exempt"


def test_dm_context_has_no_exemption(bot: Vantab) -> None:
    cog = bot.get_cog("AutoMod")
    ctx = SimpleNamespace(guild=None, author=_member(5), channel=None)
    assert cog._exemption(ctx, _config()) is None
