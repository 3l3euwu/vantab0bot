"""AI client, prompt assembly and the ~{question} parser."""

from __future__ import annotations

import asyncio
import json

import pytest
from conftest import ROOT, FakeResponse, FakeSession, make_settings

from vantab.ai import AIClient, AIError, build_messages, extract_question

# ---------------------------------------------------------------- question parser


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("~{why is the sky blue}", "why is the sky blue"),
        ("~{multi word question} extra", "multi word question extra"),
        ("~ what is a quasar", "what is a quasar"),
        ("~what is a quasar", "what is a quasar"),
        ("~ban @someone", None),
        ("~ai hello", None),
        ("~help", None),
        ("hello", None),
        ("~", None),
        ("~{}", None),
    ],
)
def test_extract_question(content: str, expected: str | None) -> None:
    assert extract_question(content, "~", {"ban", "ai", "help"}) == expected


def test_extract_question_custom_prefix() -> None:
    assert extract_question("!{hello there}", "!", set()) == "hello there"


# ------------------------------------------------------------------ prompt building


def test_build_messages_trims_history() -> None:
    history = [("user", f"q{i}") for i in range(10)]
    messages = build_messages("SYS", history, "current", max_messages=3)
    assert messages[0] == {"role": "system", "content": "SYS"}
    assert messages[-1] == {"role": "user", "content": "current"}
    assert [m["content"] for m in messages[1:-1]] == ["q7", "q8", "q9"]


def test_build_messages_char_budget() -> None:
    history = [("user", "x" * 100), ("assistant", "y" * 100), ("user", "z" * 100)]
    messages = build_messages("SYS", history, "now", max_messages=10, max_chars=150)
    assert messages[-1]["content"] == "now"
    # Only the newest history entry fits within the budget.
    assert len(messages) == 3


def test_build_messages_extra_system() -> None:
    messages = build_messages("SYS", [], "q", extra_system="EXTRA")
    assert messages[0]["content"] == "SYS\n\nEXTRA"


def test_build_messages_no_system() -> None:
    messages = build_messages("", [], "q")
    assert messages == [{"role": "user", "content": "q"}]


# --------------------------------------------------------------------- payloads


def test_payload_standard_model() -> None:
    settings = make_settings()
    client = AIClient(settings, FakeSession([]))  # type: ignore[arg-type]
    payload = client.build_payload([{"role": "user", "content": "hi"}])
    assert payload["model"] == "gpt-4o-mini"
    assert payload["max_tokens"] == 256
    assert payload["temperature"] == 0.5
    assert payload["stream"] is True


def test_payload_reasoning_model() -> None:
    settings = make_settings(ai_model="o3-mini")
    client = AIClient(settings, FakeSession([]))  # type: ignore[arg-type]
    payload = client.build_payload([{"role": "user", "content": "hi"}])
    assert "temperature" not in payload
    assert payload["max_completion_tokens"] == 256
    assert "max_tokens" not in payload


def test_headers_include_auth() -> None:
    settings = make_settings()
    client = AIClient(settings, FakeSession([]))  # type: ignore[arg-type]
    headers = client.headers()
    assert headers["Authorization"] == "Bearer sk-test"
    assert client.endpoint == "https://api.example.com/v1/chat/completions"


# ------------------------------------------------------------------- completions


async def test_complete_parses_content() -> None:
    response = FakeResponse(
        json_data={"choices": [{"message": {"content": "  hello world  "}}]},
        headers={"Content-Type": "application/json"},
    )
    settings = make_settings()
    session = FakeSession([response])
    client = AIClient(settings, session)  # type: ignore[arg-type]
    assert await client.complete([{"role": "user", "content": "hi"}]) == "hello world"
    assert len(session.calls) == 1
    assert session.calls[0]["json"]["stream"] is False
    assert response.released


async def test_complete_handles_error_payload() -> None:
    response = FakeResponse(
        json_data={"error": {"message": "model overloaded"}},
        headers={"Content-Type": "application/json"},
    )
    client = AIClient(make_settings(), FakeSession([response]))  # type: ignore[arg-type]
    with pytest.raises(AIError, match="model overloaded"):
        await client.complete([{"role": "user", "content": "hi"}])


async def test_complete_reports_http_errors() -> None:
    response = FakeResponse(status=401, body=b'{"error": {"message": "bad key"}}')
    client = AIClient(make_settings(), FakeSession([response]))  # type: ignore[arg-type]
    with pytest.raises(AIError) as excinfo:
        await client.complete([{"role": "user", "content": "hi"}])
    assert excinfo.value.status == 401
    assert "invalid API key" in str(excinfo.value)
    assert "bad key" in str(excinfo.value)


async def test_retry_on_rate_limit(monkeypatch) -> None:
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    first = FakeResponse(status=429, body=b"slow down", headers={"Retry-After": "0"})
    second = FakeResponse(json_data={"choices": [{"message": {"content": "ok"}}]})
    session = FakeSession([first, second])
    client = AIClient(make_settings(), session)  # type: ignore[arg-type]
    assert await client.complete([{"role": "user", "content": "hi"}]) == "ok"
    assert len(session.calls) == 2
    assert first.released


# ---------------------------------------------------------------------- streaming


async def test_stream_reads_sse() -> None:
    events = [
        b'data: {"choices": [{"delta": {"content": "Hel"}}]}\n\n',
        b'data: {"choices": [{"delta": {"content": "lo"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    response = FakeResponse(headers={"Content-Type": "text/event-stream"}, body=b"".join(events))
    client = AIClient(make_settings(), FakeSession([response]))  # type: ignore[arg-type]
    pieces = [piece async for piece in client.stream([{"role": "user", "content": "hi"}])]
    assert pieces == ["Hel", "lo"]
    assert response.released


async def test_stream_falls_back_to_json() -> None:
    response = FakeResponse(
        headers={"Content-Type": "application/json"},
        json_data={"choices": [{"message": {"content": "whole answer"}}]},
    )
    client = AIClient(make_settings(), FakeSession([response]))  # type: ignore[arg-type]
    pieces = [piece async for piece in client.stream([{"role": "user", "content": "hi"}])]
    assert pieces == ["whole answer"]


async def test_stream_error_event() -> None:
    body = b'data: {"error": {"message": "nope"}}\n\n'
    response = FakeResponse(headers={"Content-Type": "text/event-stream"}, body=body)
    client = AIClient(make_settings(), FakeSession([response]))  # type: ignore[arg-type]
    with pytest.raises(AIError):
        [piece async for piece in client.stream([{"role": "user", "content": "hi"}])]


async def test_test_helper() -> None:
    response = FakeResponse(json_data={"choices": [{"message": {"content": "OK"}}]})
    client = AIClient(make_settings(), FakeSession([response]))  # type: ignore[arg-type]
    assert await client.test() == "OK"
    assert json.loads("{}") == {}


async def test_extract_content_supports_parts_and_reasoning() -> None:
    parts_response = FakeResponse(
        json_data={"choices": [{"message": {"content": [{"text": "a"}, {"text": "b"}]}}]}
    )
    client = AIClient(make_settings(), FakeSession([parts_response]))  # type: ignore[arg-type]
    assert await client.complete([{"role": "user", "content": "hi"}]) == "ab"

    reasoning_response = FakeResponse(
        json_data={"choices": [{"message": {"content": "", "reasoning_content": "thinking"}}]}
    )
    client = AIClient(make_settings(), FakeSession([reasoning_response]))  # type: ignore[arg-type]
    assert await client.complete([{"role": "user", "content": "hi"}]) == "thinking"


# --------------------------------------------------------- AI cog plumbing


class _CogBot:
    def __init__(self, **overrides) -> None:
        self.settings = make_settings(**overrides)
        from types import SimpleNamespace

        self.commands = [
            SimpleNamespace(name="ban", aliases=[]),
            SimpleNamespace(name="help", aliases=[]),
            SimpleNamespace(name="ask", aliases=["ai"]),
        ]
        self.user = None
        from vantab.persona import load_persona

        self.persona = load_persona(ROOT / "prompts" / "persona.json")


def _fake_message(content: str, *, guild=None, attachments=()):
    import datetime as _dt
    from types import SimpleNamespace

    return SimpleNamespace(
        author=SimpleNamespace(bot=False, id=7, created_at=_dt.datetime.now(_dt.UTC)),
        webhook_id=None,
        content=content,
        guild=guild,
        reference=None,
        mentions=[],
        attachments=list(attachments),
        channel=SimpleNamespace(id=2),
    )


def test_question_detection_in_guild() -> None:
    from vantab.cogs.ai import AI

    cog = AI(_CogBot())  # type: ignore[arg-type]
    guild = type("G", (), {"id": 1})()
    assert cog._question_from_message(_fake_message("~{hello}", guild=guild), "~") == "hello"
    assert cog._question_from_message(_fake_message("~ hello there", guild=guild), "~") == "hello there"
    assert cog._question_from_message(_fake_message("~ban @x", guild=guild), "~") is None
    assert cog._question_from_message(_fake_message("plain text", guild=guild), "~") is None


def test_question_detection_in_dms() -> None:
    from vantab.cogs.ai import AI

    cog = AI(_CogBot())  # type: ignore[arg-type]
    assert cog._question_from_message(_fake_message("hello there")) == "hello there"
    assert cog._question_from_message(_fake_message("~help")) is None


def test_images_from_message() -> None:
    from vantab.cogs.ai import images_from

    attachments = [
        type("A", (), {"url": "https://cdn/img.png", "content_type": "image/png"})(),
        type("A", (), {"url": "https://cdn/file.txt", "content_type": "text/plain"})(),
    ]
    assert images_from(_fake_message("look", attachments=attachments)) == ["https://cdn/img.png"]


def test_cooldown_tracking() -> None:
    from vantab.cogs.ai import AI

    cog = AI(_CogBot(ai_cooldown=30.0))  # type: ignore[arg-type]
    assert cog._cooling_down(1, 2) == 0.0
    assert cog._cooling_down(1, 2) > 0.0
    assert cog._cooling_down(1, 3) == 0.0


# ------------------------------------------------------------------ persona


def test_system_prompt_uses_persona() -> None:
    from vantab.cogs.ai import AI

    cog = AI(_CogBot())  # type: ignore[arg-type]
    prompt = cog._system_prompt(None, "alice")
    assert "Vantab0t" in prompt
    assert "alice" in prompt
    assert "minuscules" in prompt
    assert "kaomoji" in prompt


def test_system_prompt_respects_style_and_custom_prompt() -> None:
    from types import SimpleNamespace

    from vantab.cogs.ai import AI

    cog = AI(_CogBot())  # type: ignore[arg-type]
    normal = cog._system_prompt(SimpleNamespace(ai_style="normal", ai_system_prompt=None), "bob")
    assert "assistant utile" in normal

    custom = cog._system_prompt(
        SimpleNamespace(ai_style="normal", ai_system_prompt="parle comme {bot} à {user}"), "bob"
    )
    assert custom == "parle comme Vantab0t à bob"

    unknown = cog._style(SimpleNamespace(ai_style="does-not-exist"))
    assert unknown == "elee"


def test_greeting_only_on_fresh_conversation() -> None:
    from vantab.cogs.ai import AI

    cog = AI(_CogBot())  # type: ignore[arg-type]
    first = cog._apply_greeting("salut~", fresh=True, user_name="alice")
    assert first.startswith("hewoooooo~")
    assert first.endswith("salut~")
    assert cog._apply_greeting("salut~", fresh=False, user_name="alice") == "salut~"
    # Never double-prefix when the model already greets.
    already = cog._apply_greeting("hewoooooo~ cha vaaa~ ? :3 oui~", fresh=True, user_name="a")
    assert already.count("hewoooooo~") == 1


# ------------------------------------------------------------- model fallback


async def test_switches_model_on_503(monkeypatch) -> None:
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    first = FakeResponse(
        status=503,
        body=b'{"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}}',
    )
    second = FakeResponse(json_data={"choices": [{"message": {"content": "ok"}}]})
    session = FakeSession([first, second])
    settings = make_settings(ai_model="primary", ai_fallback_models=("backup",))
    client = AIClient(settings, session)  # type: ignore[arg-type]

    assert await client.complete([{"role": "user", "content": "hi"}]) == "ok"
    assert [call["json"]["model"] for call in session.calls] == ["primary", "backup"]
    assert client.model == "backup"
    assert client.model_index == 1
    assert first.released


async def test_fallback_chain_is_exhausted(monkeypatch) -> None:
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    responses = [FakeResponse(status=503, body=b"busy") for _ in range(4)]
    session = FakeSession(responses)
    settings = make_settings(ai_model="primary", ai_fallback_models=("backup",))
    client = AIClient(settings, session)  # type: ignore[arg-type]

    with pytest.raises(AIError) as excinfo:
        await client.complete([{"role": "user", "content": "hi"}])
    assert excinfo.value.status == 503
    models = [call["json"]["model"] for call in session.calls]
    assert models[0] == "primary"
    assert set(models[1:]) == {"backup"}


def test_switch_model_without_fallbacks() -> None:
    client = AIClient(make_settings(ai_model="solo"), FakeSession([]))  # type: ignore[arg-type]
    assert client.models == ("solo",)
    assert client.switch_model("busy") is None
    assert client.model_index == 0


def test_fallback_cooldown_returns_to_primary(monkeypatch) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr("vantab.ai.monotonic", lambda: clock["now"])
    client = AIClient(
        make_settings(ai_model="primary", ai_fallback_models=("backup",)), FakeSession([])  # type: ignore[arg-type]
    )
    assert client.switch_model("503") == "backup"
    assert client.model == "backup"

    clock["now"] += 301
    assert client.model == "primary"
    assert client.model_index == 0


def test_build_payload_uses_active_model() -> None:
    client = AIClient(
        make_settings(ai_model="primary", ai_fallback_models=("backup",)), FakeSession([])  # type: ignore[arg-type]
    )
    client.model_index = 1
    payload = client.build_payload([{"role": "user", "content": "x"}], stream=False)
    assert payload["model"] == "backup"
