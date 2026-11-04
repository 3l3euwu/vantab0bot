"""Load every cog and verify the command tree builds correctly."""

from __future__ import annotations

import pytest
from conftest import make_settings
from discord.ext import tasks

from vantab.bot import INITIAL_COGS, Vantab
from vantab.ui.views import ConfirmView, Paginator, VerifyView


@pytest.fixture()
async def bot(monkeypatch):
    # Background loops need a logged-in client; skip them in unit tests.
    monkeypatch.setattr(tasks.Loop, "start", lambda self, *args, **kwargs: None)
    instance = Vantab(make_settings(owner_ids=frozenset({1234})))
    for extension in INITIAL_COGS:
        await instance.load_extension(extension)
    yield instance
    for name in list(instance.cogs):
        await instance.remove_cog(name)
    await instance.close()


async def test_persona_loaded(bot: Vantab) -> None:
    assert bot.persona.bot_name == "Vantab0t"
    assert bot.persona.default_style == "elee"
    assert bot.persona.greeting.startswith("hewoooooo~")


async def test_all_cogs_load(bot: Vantab) -> None:
    expected = {
        "AI",
        "Moderation",
        "AutoMod",
        "Settings",
        "Logging",
        "Security",
        "Utility",
        "BackgroundTasks",
    }
    assert expected <= set(bot.cogs)


async def test_commands_registered(bot: Vantab) -> None:
    names = {command.name for command in bot.commands}
    assert {"ban", "tempban", "banlist", "kick", "timeout", "warn", "purge", "ask", "help", "automod"} <= names
    assert len(names) >= 40

    app_names = {command.name for command in bot.tree.get_commands()}
    assert {"ban", "ask", "automod", "antinuke", "verify", "help"} <= app_names


async def test_groups_have_children(bot: Vantab) -> None:
    automod = bot.get_command("automod")
    assert automod is not None
    children = {child.name for child in automod.commands}
    assert {"status", "rules", "enable", "disable", "set", "threshold", "test", "badword"} <= children

    badword = bot.get_command("automod badword")
    assert badword is not None
    assert {"add", "remove", "list"} <= {child.name for child in badword.commands}

    antinuke = bot.get_command("antinuke")
    assert antinuke is not None
    assert "whitelist" in {child.name for child in antinuke.commands}


async def test_ai_commands(bot: Vantab) -> None:
    ask = bot.get_command("ask")
    assert ask is not None
    assert "ai" in ask.aliases
    assert bot.tree.get_command("ask-private") is not None


async def test_hybrid_signatures(bot: Vantab) -> None:
    ban = bot.get_command("ban")
    assert ban is not None
    assert ban.signature
    timeout = bot.get_command("timeout")
    assert timeout is not None
    assert "timeout" in timeout.aliases or "mute" in timeout.aliases


def test_confirm_view_buttons() -> None:
    view = ConfirmView(1)
    labels = [child.label for child in view.children]
    assert labels == ["Confirm", "Cancel"]


def test_verify_view_is_persistent() -> None:
    class FakeBot:
        pass

    view = VerifyView(FakeBot())  # type: ignore[arg-type]
    assert view.timeout is None
    button = view.children[0]
    assert button.custom_id == "vantab:verify"
    assert button.label == "Verify"


def test_paginator_clamps() -> None:
    import discord

    embeds = [discord.Embed(title=str(index)) for index in range(3)]
    view = Paginator(embeds, author_id=1)
    assert len(view.embeds) == 3
    assert view.index == 0
    assert view.previous.disabled is True
    assert view.next.disabled is False


async def test_help_view_and_categories(bot: Vantab) -> None:
    from vantab.cogs.utility import HelpView, category_embed

    view = HelpView(bot, "~", author_id=1)
    categories = view.categories()
    assert "Moderation" in categories
    assert "AutoMod" in categories
    assert "AI" in categories
    assert "BackgroundTasks" not in categories

    embed = category_embed(bot, "~", "Moderation")
    assert embed.fields
    assert any("ban" in field.name for field in embed.fields)


async def test_every_command_has_help(bot: Vantab) -> None:
    from vantab.cogs.utility import command_summary

    missing = [command.qualified_name for command in bot.commands if command_summary(command) == "No description."]
    assert missing == []


async def test_automod_rules_are_documented(bot: Vantab) -> None:
    from vantab.automod.rules import RULES

    for spec in RULES.values():
        assert spec.label and spec.description
        assert spec.default_action in {"none", "delete", "warn", "timeout", "kick", "ban"}
