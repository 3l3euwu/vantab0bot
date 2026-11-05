"""Interaction-timeout handling: no traceback for expired interactions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from vantab.bot import is_expired_interaction, root_cause
from vantab.checks import defer_if_interaction


class Wrapper(Exception):
    def __init__(self, original: BaseException) -> None:
        super().__init__(str(original))
        self.original = original


def _not_found(code: int) -> discord.NotFound:
    response = SimpleNamespace(status=404, reason="Not Found")
    return discord.NotFound(response, {"code": code, "message": "not found"})


def test_root_cause_peels_wrappers() -> None:
    inner = RuntimeError("boom")
    nested = Wrapper(Wrapper(inner))
    assert root_cause(nested) is inner
    assert root_cause(inner) is inner


def test_expired_interaction_detected_through_wrappers() -> None:
    expired = _not_found(10062)
    error = commands.CommandInvokeError(expired)
    assert is_expired_interaction(Wrapper(Wrapper(error)))


def test_other_not_found_is_not_an_expired_interaction() -> None:
    assert not is_expired_interaction(_not_found(10008))
    assert not is_expired_interaction(RuntimeError("nope"))


def test_interaction_responded_counts_as_expired() -> None:
    assert is_expired_interaction(discord.InteractionResponded(SimpleNamespace()))


async def test_defer_only_for_slash_invocations() -> None:
    response = SimpleNamespace(is_done=lambda: False, defer=AsyncMock())
    ctx = SimpleNamespace(interaction=SimpleNamespace(response=response))
    assert await defer_if_interaction(ctx) is True
    response.defer.assert_awaited_once_with(ephemeral=True)


async def test_defer_skips_prefix_and_answered_interactions() -> None:
    assert await defer_if_interaction(SimpleNamespace(interaction=None)) is False

    response = SimpleNamespace(is_done=lambda: True, defer=AsyncMock())
    ctx = SimpleNamespace(interaction=SimpleNamespace(response=response))
    assert await defer_if_interaction(ctx) is False
    response.defer.assert_not_called()


async def test_defer_swallows_http_errors() -> None:
    response = SimpleNamespace(is_done=lambda: False, defer=AsyncMock(side_effect=_not_found(10062)))
    ctx = SimpleNamespace(interaction=SimpleNamespace(response=response))
    assert await defer_if_interaction(ctx) is False


async def test_error_handlers_ignore_expired_interactions(bot) -> None:
    """Both handlers must return quietly instead of trying to answer."""
    ctx = SimpleNamespace(
        command=SimpleNamespace(qualified_name="purge"), interaction=None, reply=AsyncMock()
    )
    error = commands.HybridCommandError(commands.CommandInvokeError(_not_found(10062)))
    await bot.on_command_error(ctx, error)
    ctx.reply.assert_not_called()

    interaction = SimpleNamespace(
        response=SimpleNamespace(is_done=lambda: True),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await bot.on_app_command_error(interaction, error)
    interaction.followup.send.assert_not_called()
