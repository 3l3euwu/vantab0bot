"""Load every cog and verify the command tree builds correctly."""

from __future__ import annotations

import inspect
import re

from vantab.bot import Vantab
from vantab.ui.views import ConfirmView, Paginator, VerifyView


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


async def test_command_prefix_resolves(bot: Vantab) -> None:
    from types import SimpleNamespace

    bot._connection.user = SimpleNamespace(id=999)  # type: ignore[assignment]
    message = SimpleNamespace(guild=None, content="~help")
    prefixes = await bot.get_prefix(message)
    assert "~" in prefixes
    assert any(prefix.startswith("<@999>") for prefix in prefixes)


async def test_automod_runs_before_commands(bot: Vantab, monkeypatch) -> None:
    from types import SimpleNamespace

    seen: list[str] = []

    async def fake_process(message):
        seen.append("automod")

    async def fake_process_commands(message):
        seen.append("commands")

    monkeypatch.setattr(bot.automod, "process", fake_process)
    monkeypatch.setattr(bot, "process_commands", fake_process_commands)

    message = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        author=SimpleNamespace(bot=False, id=5),
        content="porn",
        attachments=[],
        id=1,
    )
    await bot.on_message(message)
    assert seen == ["automod", "commands"]


async def test_automod_errors_do_not_block_commands(bot: Vantab, monkeypatch) -> None:
    from types import SimpleNamespace

    called: list[str] = []

    async def boom(message):
        raise RuntimeError("automod exploded")

    async def fake_process_commands(message):
        called.append("commands")

    monkeypatch.setattr(bot.automod, "process", boom)
    monkeypatch.setattr(bot, "process_commands", fake_process_commands)

    message = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        author=SimpleNamespace(bot=False, id=5),
        content="hello",
        attachments=[],
        id=2,
    )
    await bot.on_message(message)
    assert called == ["commands"]


async def test_edited_messages_are_checked(bot: Vantab, monkeypatch) -> None:
    from types import SimpleNamespace

    checked: list[str] = []

    async def fake_process(message):
        checked.append(message.content)

    monkeypatch.setattr(bot.automod, "process", fake_process)

    guild = SimpleNamespace(id=1)
    author = SimpleNamespace(bot=False, id=5)
    before = SimpleNamespace(guild=guild, author=author, content="hello", attachments=[], id=3)
    after = SimpleNamespace(guild=guild, author=author, content="hello porn", attachments=[], id=3)
    await bot.on_message_edit(before, after)
    assert checked == ["hello porn"]


async def test_every_private_helper_is_defined(bot: Vantab) -> None:
    """Catch ``self._thing()`` calls whose helper was never written."""
    missing: list[str] = []
    for name, cog in bot.cogs.items():
        source = inspect.getsource(type(cog))
        for helper in sorted(set(re.findall(r"self\.(_[A-Za-z_]\w*)\(", source))):
            if not hasattr(cog, helper):
                missing.append(f"{name}.{helper}")
    assert not missing, f"helpers called but not defined: {missing}"
