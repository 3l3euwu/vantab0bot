"""Persistence layer tests."""

from __future__ import annotations

from datetime import timedelta

import pytest

from vantab.database import DEFAULT_ESCALATION, Database, utcnow


async def test_guild_defaults_and_updates(db: Database) -> None:
    settings = await db.get_guild(42)
    assert settings.guild_id == 42
    assert settings.automod_enabled is True
    assert settings.escalation == DEFAULT_ESCALATION

    updated = await db.update_guild(42, prefix="?", automod_enabled=False, mod_log_channel=123)
    assert updated.prefix == "?"
    assert updated.automod_enabled is False
    assert updated.mod_log_channel == 123

    assert (await db.get_guild(42)).prefix == "?"
    assert await db.guild_count() == 1


async def test_update_guild_rejects_unknown_field(db: Database) -> None:
    with pytest.raises(ValueError):
        await db.update_guild(1, nope=True)


async def test_escalation_roundtrip(db: Database) -> None:
    ladder = [{"warns": 2, "action": "kick", "duration": 0}]
    await db.update_guild(1, escalation=ladder)
    assert (await db.get_guild(1)).escalation == ladder


async def test_rules_crud(db: Database) -> None:
    assert await db.get_rules(1) == {}
    rule = await db.set_rule(1, "spam", enabled=False, action="kick", duration=60, threshold=9)
    assert rule.action == "kick"
    assert (await db.get_rule(1, "spam")).threshold == 9
    await db.set_rule(1, "spam", threshold=10)
    stored = await db.get_rule(1, "spam")
    assert stored.threshold == 10
    assert stored.action == "kick"  # untouched fields preserved
    assert await db.delete_rule(1, "spam") is True
    assert await db.get_rule(1, "spam") is None


async def test_exemptions(db: Database) -> None:
    assert await db.add_exemption(1, "channel", 10) is True
    assert await db.add_exemption(1, "channel", 10) is False
    assert await db.add_exemption(1, "role", 20) is True
    exemptions = await db.get_exemptions(1)
    assert exemptions["channel"] == {10}
    assert exemptions["role"] == {20}
    assert await db.remove_exemption(1, "channel", 10) is True
    assert (await db.get_exemptions(1))["channel"] == set()
    with pytest.raises(ValueError):
        await db.add_exemption(1, "planet", 1)


async def test_badwords_and_allowlist(db: Database) -> None:
    await db.add_badword(1, "Bad")
    await db.add_badword(1, "bad")  # duplicate after normalisation
    assert await db.get_badwords(1) == {"bad"}
    assert await db.remove_badword(1, "bad") is True
    assert await db.get_badwords(1) == set()

    assert await db.add_domain(1, "https://YouTube.com/") is True
    assert await db.get_allowlist(1) == {"youtube.com"}
    assert await db.remove_domain(1, "youtube.com") is True
    with pytest.raises(ValueError):
        await db.add_domain(1, "notadomain")


async def test_warnings_lifecycle(db: Database) -> None:
    first = await db.add_warning(1, 100, 200, "spamming")
    await db.add_warning(1, 100, 200, "again")
    assert await db.count_warnings(1, 100) == 2
    entries = await db.list_warnings(1, 100, active_only=True)
    assert entries[0].reason == "again"
    assert await db.deactivate_warning(first) is True
    assert await db.count_warnings(1, 100) == 1
    assert await db.clear_warnings(1, 100) == 1
    assert await db.count_warnings(1, 100) == 0


async def test_warning_decay(db: Database) -> None:
    await db.add_warning(1, 5, 6, "old", created_at=utcnow() - timedelta(days=40))
    await db.add_warning(1, 5, 6, "new")
    removed = await db.decay_warnings(1, 30)
    assert removed == 1
    assert await db.count_warnings(1, 5) == 1


async def test_cases_numbering_and_lookup(db: Database) -> None:
    first = await db.add_case(1, action="warn", user_id=10, moderator_id=20, reason="a")
    second = await db.add_case(1, action="ban", user_id=10, moderator_id=20, reason="b")
    other = await db.add_case(2, action="kick", user_id=10, moderator_id=20)

    assert first.case_number == 1
    assert second.case_number == 2
    assert other.case_number == 1  # per-guild numbering
    assert await db.case_count(1) == 2
    assert await db.case_count() == 3

    assert await db.update_case_reason(1, 2, "updated") is True
    assert await db.update_case_reason(1, 99, "nope") is False
    assert (await db.get_case(1, 2)).reason == "updated"
    history = await db.list_cases(1, user_id=10)
    assert [case.case_number for case in history] == [2, 1]


async def test_case_expiry(db: Database) -> None:
    await db.add_case(1, action="ban", user_id=1, moderator_id=2, duration=60)
    await db.add_case(1, action="ban", user_id=2, moderator_id=2, duration=0)
    expired = await db.expired_cases(utcnow() + timedelta(minutes=5))
    assert len(expired) == 1
    assert expired[0].user_id == 1
    assert expired[0].expires_at is not None
    assert await db.deactivate_case(1, expired[0].case_number) is True
    assert await db.expired_cases(utcnow() + timedelta(minutes=5)) == []


async def test_ai_history(db: Database) -> None:
    await db.add_ai_message(1, 2, 3, "user", "hello")
    await db.add_ai_message(1, 2, 3, "assistant", "hi there")
    history = await db.get_ai_history(1, 2)
    assert history == [("user", "hello"), ("assistant", "hi there")]
    assert await db.get_ai_history(1, 2, limit=1) == [("assistant", "hi there")]
    assert await db.get_ai_history(1, 999) == []
    assert await db.clear_ai_history(1, 2) == 2
    assert await db.get_ai_history(1, 2) == []


async def test_ai_history_prune(db: Database) -> None:
    await db.add_ai_message(1, 2, 3, "user", "old", created_at=utcnow() - timedelta(days=30))
    await db.add_ai_message(1, 2, 3, "user", "new")
    assert await db.prune_ai_history(14) == 1
    assert len(await db.get_ai_history(1, 2)) == 1


async def test_tags(db: Database) -> None:
    await db.add_tag(1, "Rules", "be nice", 10)
    content, owner, uses = await db.get_tag(1, "rules")
    assert content == "be nice"
    assert owner == 10
    assert uses == 1
    await db.add_tag(1, "rules", "be kind", 11)
    assert (await db.get_tag(1, "rules"))[0] == "be kind"
    assert await db.list_tags(1) == [("rules", 2)]
    assert await db.delete_tag(1, "rules") is True
    assert await db.get_tag(1, "rules") is None


async def test_antinuke_storage(db: Database) -> None:
    assert await db.add_antinuke_whitelist(1, 99) is True
    assert await db.add_antinuke_whitelist(1, 99) is False
    assert await db.antinuke_whitelist(1) == {99}
    assert await db.remove_antinuke_whitelist(1, 99) is True

    await db.record_antinuke_event(1, 99, "ban", 5)
    events = await db.recent_antinuke_events(1, 99, utcnow() - timedelta(minutes=1))
    assert events[0][0] == "ban"
    assert await db.prune_antinuke_events(0) >= 0


async def test_meta(db: Database) -> None:
    assert await db.get_meta("missing") is None
    await db.set_meta("k", "v")
    assert await db.get_meta("k") == "v"
    await db.set_meta("k", "v2")
    assert await db.get_meta("k") == "v2"


async def test_migration_adds_ai_style_column(tmp_path) -> None:
    """A database created before personas existed must be upgraded in place."""
    import aiosqlite

    path = tmp_path / "legacy.db"
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            """
            CREATE TABLE guilds (
                guild_id INTEGER PRIMARY KEY,
                prefix TEXT,
                mod_log_channel INTEGER,
                automod_log_channel INTEGER,
                ai_channel INTEGER,
                ai_enabled INTEGER NOT NULL DEFAULT 1,
                automod_enabled INTEGER NOT NULL DEFAULT 1,
                antinuke_enabled INTEGER NOT NULL DEFAULT 1,
                antinuke_log_channel INTEGER,
                mute_role_id INTEGER,
                verify_channel_id INTEGER,
                verify_role_id INTEGER,
                verify_enabled INTEGER NOT NULL DEFAULT 0,
                raid_mode INTEGER NOT NULL DEFAULT 0,
                escalation TEXT,
                ai_system_prompt TEXT,
                warn_decay_days INTEGER NOT NULL DEFAULT 30,
                verify_timeout_seconds INTEGER NOT NULL DEFAULT 600,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        await conn.execute("INSERT INTO guilds (guild_id, prefix) VALUES (7, '?')")
        await conn.commit()

    database = Database(path)
    await database.connect()
    try:
        settings = await database.get_guild(7)
        assert settings.prefix == "?"
        assert settings.ai_style is None
        await database.update_guild(7, ai_style="normal")
        assert (await database.get_guild(7)).ai_style == "normal"
    finally:
        await database.close()
