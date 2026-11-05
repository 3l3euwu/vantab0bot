"""Shared fixtures for the Vantab test-suite."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from discord.ext import tasks  # noqa: E402

from vantab.bot import INITIAL_COGS, Vantab  # noqa: E402
from vantab.config import Settings  # noqa: E402
from vantab.database import Database  # noqa: E402


def make_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = dict(  # noqa: C408
        token="test-token",
        owner_ids=frozenset({1}),
        default_prefix="~",
        database_path=Path("/tmp/vantab-test.db"),
        log_level="DEBUG",
        dev_guild_id=None,
        status_text="over {guilds} servers",
        activity_type="watching",
        ai_enabled=True,
        ai_provider="openai",
        ai_base_url="https://api.example.com/v1",
        ai_api_key="sk-test",
        ai_model="gpt-4o-mini",
        ai_temperature=0.5,
        ai_max_tokens=256,
        ai_timeout=30.0,
        ai_stream=True,
        ai_history_limit=4,
        ai_history_ttl_days=7,
        ai_cooldown=0.0,
        ai_system_prompt_path=None,
        ai_system_prompt_override=None,
        persona_file=ROOT / "prompts" / "persona.json",
        ai_style="elee",
        ai_greeting=None,
        ai_vision=True,
        default_mute_minutes=10,
        max_timeout_minutes=40320,
        dm_on_punish=True,
        antinuke_poll_seconds=6.0,
        ai_extra_headers={},
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture()
def settings() -> Settings:
    return make_settings()


@pytest.fixture()
async def bot(monkeypatch):
    """A fully-loaded bot (background loops are not started)."""
    monkeypatch.setattr(tasks.Loop, "start", lambda self, *args, **kwargs: None)
    instance = Vantab(make_settings(owner_ids=frozenset({1234})))
    for extension in INITIAL_COGS:
        await instance.load_extension(extension)
    yield instance
    for name in list(instance.cogs):
        await instance.remove_cog(name)
    await instance.close()


@pytest.fixture()
async def db(tmp_path: Path):
    database = Database(tmp_path / "vantab.db")
    await database.connect()
    try:
        yield database
    finally:
        await database.close()


class FakeContent:
    """Mimics ``aiohttp.StreamReader.iter_any``."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_any(self):
        for chunk in self._chunks:
            yield chunk


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        json_data: Any = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body
        self._json = json_data
        self.released = False
        self.content = FakeContent([body] if body else [])

    async def text(self) -> str:
        return self._body.decode()

    async def json(self, content_type: str | None = None) -> Any:
        return self._json

    def release(self) -> None:
        self.released = True


class FakeSession:
    """Records calls and returns queued responses."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("FakeSession ran out of responses")
        return self.responses.pop(0)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_session_factory():
    return FakeSession
