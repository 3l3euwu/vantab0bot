"""Unit tests for every auto-mod detection rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from vantab.automod.engine import AutoModEngine
from vantab.automod.rules import (
    RULES,
    MessageContext,
    ViolationTracker,
    bump_action,
    evaluate,
    max_action,
    rule_config_dict,
)

BASE = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)


def rules_config(**overrides) -> dict[str, dict]:
    config = {
        name: {
            "enabled": spec.enabled_by_default,
            "action": spec.default_action,
            "duration": spec.default_duration,
            "threshold": spec.default_threshold,
        }
        for name, spec in RULES.items()
    }
    for name, values in overrides.items():
        config[name].update(values)
    return config


def make_ctx(**kwargs) -> MessageContext:
    data = {
        "guild_id": 1,
        "channel_id": 2,
        "author_id": 3,
        "content": "",
        "created_at": BASE,
    }
    data.update(kwargs)
    return MessageContext(**data)


def only(rule: str, **values) -> dict[str, dict]:
    config = rules_config()
    for name in config:
        config[name]["enabled"] = name == rule
    config[rule].update(values)
    return config


def detect(ctx: MessageContext, config: dict, **kwargs) -> list[str]:
    detections = evaluate(
        ctx,
        rules=config,
        badwords=kwargs.get("badwords", ()),
        allowlist=kwargs.get("allowlist", ()),
        tracker=kwargs.get("tracker") or ViolationTracker(),
    )
    return [d.rule for d in detections]


def test_rule_config_dict_handles_slots() -> None:
    from types import SimpleNamespace

    config = SimpleNamespace(enabled=True, action="kick", duration=60, threshold=4)
    assert rule_config_dict(config) == {
        "enabled": True,
        "action": "kick",
        "duration": 60,
        "threshold": 4,
    }


def test_all_rules_have_specs() -> None:
    assert len(RULES) >= 16
    assert {"spam", "phishing", "badwords", "sensitive"} <= set(RULES)


def test_spam_detection() -> None:
    tracker = ViolationTracker()
    config = only("spam", threshold=5)
    fired = []
    for index in range(7):
        ctx = make_ctx(content=f"message {index}", created_at=BASE + timedelta(seconds=index))
        fired.append("spam" in detect(ctx, config, tracker=tracker))
    assert fired[:5] == [False] * 5
    assert fired[5] is True


def test_spam_window_expires() -> None:
    tracker = ViolationTracker()
    config = only("spam", threshold=3)
    for index in range(3):
        evaluate(
            make_ctx(content=f"m{index}", created_at=BASE + timedelta(seconds=index)),
            rules=config,
            tracker=tracker,
        )
    later = make_ctx(content="calm", created_at=BASE + timedelta(seconds=60))
    assert detect(later, config, tracker=tracker) == []


def test_duplicate_detection() -> None:
    tracker = ViolationTracker()
    config = only("duplicate", threshold=3)
    results = []
    for index in range(3):
        ctx = make_ctx(content="buy my stuff", created_at=BASE + timedelta(seconds=index * 2))
        results.append("duplicate" in detect(ctx, config, tracker=tracker))
    assert results[-1] is True


def test_short_duplicates_ignored() -> None:
    tracker = ViolationTracker()
    config = only("duplicate", threshold=2)
    for index in range(4):
        ctx = make_ctx(content="ok", created_at=BASE + timedelta(seconds=index))
        assert detect(ctx, config, tracker=tracker) == []


def test_cross_channel_detection() -> None:
    tracker = ViolationTracker()
    config = only("crosschannel", threshold=3)
    results = []
    for channel in (10, 11, 12):
        ctx = make_ctx(content="everyone look at this", channel_id=channel, created_at=BASE + timedelta(seconds=2))
        results.append("crosschannel" in detect(ctx, config, tracker=tracker))
    assert results[-1] is True


def test_mention_spam() -> None:
    config = only("mention_spam", threshold=4)
    assert detect(make_ctx(content="hi", mention_count=4), config) == ["mention_spam"]
    assert detect(make_ctx(content="hi", mention_count=3), config) == []


def test_everyone_mention() -> None:
    config = only("everyone")
    assert detect(make_ctx(content="@everyone hello", everyone_mention=True), config) == ["everyone"]
    assert detect(make_ctx(content="@everyone hello", everyone_mention=False), config) == []


def test_invite_detection() -> None:
    config = only("invites")
    assert detect(make_ctx(content="join https://discord.gg/abcdef"), config) == ["invites"]
    assert detect(make_ctx(content="discord.com/invite/xyz"), config) == ["invites"]
    assert detect(make_ctx(content="discord . gg / obfuscated"), config) == ["invites"]
    assert detect(make_ctx(content="no links here"), config) == []


def test_link_rule_and_allowlist() -> None:
    config = only("links")
    assert detect(make_ctx(content="https://spam.example/x"), config) == ["links"]
    assert detect(make_ctx(content="https://youtube.com/watch?v=1"), config, allowlist=["youtube.com"]) == []
    assert detect(make_ctx(content="https://cdn.youtube.com/x"), config, allowlist=["youtube.com"]) == []
    config_off = only("links", enabled=False)
    assert detect(make_ctx(content="https://spam.example/x"), config_off) == []


def test_phishing_detection() -> None:
    config = only("phishing")
    assert detect(make_ctx(content="free nitro at https://discord-nitro.com/gift"), config) == ["phishing"]
    assert detect(make_ctx(content="https://github.com/x"), config) == []


def test_scam_phrases_require_link() -> None:
    config = only("scam")
    assert detect(make_ctx(content="free nitro here https://x.io"), config) == ["scam"]
    assert detect(make_ctx(content="free nitro here"), config) == []


def test_badwords_with_wildcards_and_leet() -> None:
    config = only("badwords")
    assert detect(make_ctx(content="you are a badword"), config, badwords=["badword"]) == ["badwords"]
    assert detect(make_ctx(content="this is b4dw0rd"), config, badwords=["badword"]) == ["badwords"]
    assert detect(make_ctx(content="superbadwords"), config, badwords=["bad*"]) == ["badwords"]
    assert detect(make_ctx(content="totally fine"), config, badwords=["badword"]) == []


def test_caps_rule() -> None:
    config = only("caps")
    assert detect(make_ctx(content="THIS IS VERY LOUD INDEED"), config) == ["caps"]
    assert detect(make_ctx(content="This is a normal sentence"), config) == []
    assert detect(make_ctx(content="SHORT"), config) == []


def test_zalgo_rule() -> None:
    config = only("zalgo")
    text = "h̸̢̛e̷̢̛l̸̢̛l̷̢̛ơ̸̢ w̷̢̛ơ̷̢r̷̢̛l̷̢̛d̷̢̛"
    assert detect(make_ctx(content=text), config) == ["zalgo"]
    assert detect(make_ctx(content="plain text here"), config) == []


def test_emoji_rule_threshold() -> None:
    config = only("emoji", threshold=3)
    assert detect(make_ctx(content="🔥🔥🔥🔥"), config) == ["emoji"]
    assert detect(make_ctx(content="🔥"), config) == []


def test_newline_rule() -> None:
    config = only("newlines", threshold=5)
    assert detect(make_ctx(content="a\n" * 8), config) == ["newlines"]
    assert detect(make_ctx(content="a\nb"), config) == []


def test_attachment_rules() -> None:
    config = only("attachments", threshold=3)
    assert detect(make_ctx(content="", attachment_count=3, attachment_names=("a.png",) * 3), config) == ["attachments"]
    dangerous = make_ctx(content="", attachment_count=1, attachment_names=("virus.exe",))
    assert detect(dangerous, config) == ["attachments"]


def test_sensitive_credit_card_and_key() -> None:
    config = only("sensitive")
    assert detect(make_ctx(content="my card 4242 4242 4242 4242"), config) == ["sensitive"]
    assert detect(make_ctx(content="-----BEGIN PRIVATE KEY-----\nabc"), config) == ["sensitive"]
    assert detect(make_ctx(content="order 1234 5678 9012 3456"), config) == []


def test_disabled_rules_are_skipped() -> None:
    config = only("invites", enabled=False)
    assert detect(make_ctx(content="discord.gg/abc"), config) == []


def test_max_action_and_bump() -> None:
    assert max_action("delete", "timeout", "warn") == "timeout"
    assert max_action("none", "delete") == "delete"
    assert max_action() == "none"
    assert bump_action("delete") == "warn"
    assert bump_action("timeout", 2) == "ban"
    assert bump_action("ban", 3) == "ban"
    assert bump_action("unknown") == "delete"


def test_tracker_prune_and_clear() -> None:
    tracker = ViolationTracker()
    ctx = make_ctx(content="hello")
    tracker.record_message(ctx)
    assert tracker.message_count(ctx) == 1
    tracker.add_strike(1, 3, BASE)
    assert tracker.strike_count(1, 3, BASE) == 1
    tracker.clear(1, 3)
    assert tracker.strike_count(1, 3, BASE) == 0
    tracker.record_message(ctx)
    tracker.prune(BASE + timedelta(hours=1))
    assert tracker.message_count(ctx) == 0


def test_engine_escalates_repeat_offenders() -> None:
    engine = AutoModEngine(SimpleNamespace())  # type: ignore[arg-type]
    message = SimpleNamespace(guild=SimpleNamespace(id=1))
    ctx = make_ctx()
    config = SimpleNamespace()

    action, _ = engine._escalate(message, ctx, config, "warn", 0, [])  # type: ignore[arg-type]
    assert action == "warn"  # first strike: no escalation

    engine.tracker.add_strike(1, 3, BASE)
    engine.tracker.add_strike(1, 3, BASE)
    action, duration = engine._escalate(message, ctx, config, "warn", 0, [])  # type: ignore[arg-type]
    assert action == "timeout"
    assert duration == 1800


def test_engine_escalates_new_accounts() -> None:
    engine = AutoModEngine(SimpleNamespace())  # type: ignore[arg-type]
    message = SimpleNamespace(guild=SimpleNamespace(id=1))
    ctx = make_ctx(account_age=timedelta(days=1))
    action, _ = engine._escalate(message, ctx, SimpleNamespace(), "warn", 0, [])  # type: ignore[arg-type]
    assert action == "timeout"
