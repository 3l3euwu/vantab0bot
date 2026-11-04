"""Tests for the ``bot.py`` entry point helpers."""

from __future__ import annotations

import bot

MEMBERS = bot.GATEWAY_GUILD_MEMBERS
MEMBERS_LIMITED = bot.GATEWAY_GUILD_MEMBERS_LIMITED
CONTENT = bot.GATEWAY_MESSAGE_CONTENT
CONTENT_LIMITED = bot.GATEWAY_MESSAGE_CONTENT_LIMITED


def test_intent_status_all_off() -> None:
    assert bot.intent_status(0) == (False, False)


def test_intent_status_full_bits() -> None:
    assert bot.intent_status(MEMBERS | CONTENT) == (True, True)


def test_intent_status_limited_bits() -> None:
    assert bot.intent_status(MEMBERS_LIMITED | CONTENT_LIMITED) == (True, True)


def test_intent_status_mixed() -> None:
    assert bot.intent_status(MEMBERS | CONTENT_LIMITED) == (True, True)
    assert bot.intent_status(MEMBERS) == (True, False)
    assert bot.intent_status(CONTENT) == (False, True)


def test_close_hints_cover_actionable_codes() -> None:
    for code in (4004, 4008, 4009, 4010, 4011, 4013, 4014):
        assert code in bot.CLOSE_HINTS
    assert "Save Changes" in bot.CLOSE_HINTS[4014]
