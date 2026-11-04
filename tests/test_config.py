"""Configuration loading from the environment."""

from __future__ import annotations

import pytest

from vantab.config import PROVIDER_PRESETS, Settings


def _clear(monkeypatch) -> None:
    for name in (
        "DISCORD_TOKEN",
        "OWNER_IDS",
        "DEFAULT_PREFIX",
        "DATABASE_PATH",
        "AI_PROVIDER",
        "AI_BASE_URL",
        "AI_API_KEY",
        "AI_MODEL",
        "AI_STREAM",
        "AI_TEMPERATURE",
        "AI_MAX_TOKENS",
        "AI_HISTORY_LIMIT",
        "AI_SYSTEM_PROMPT",
        "DEV_GUILD_ID",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_missing_token_exits(monkeypatch, tmp_path) -> None:
    _clear(monkeypatch)
    monkeypatch.setattr("vantab.config.BASE_DIR", tmp_path)
    with pytest.raises(SystemExit):
        Settings.load()


def test_loads_defaults(monkeypatch, tmp_path) -> None:
    _clear(monkeypatch)
    monkeypatch.setattr("vantab.config.BASE_DIR", tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "abc.def.ghi")
    settings = Settings.load()
    assert settings.token == "abc.def.ghi"
    assert settings.default_prefix == "~"
    assert settings.ai_model == PROVIDER_PRESETS["openai"]["model"]
    assert settings.database_path.name == "vantab.db"


def test_provider_presets(monkeypatch, tmp_path) -> None:
    _clear(monkeypatch)
    monkeypatch.setattr("vantab.config.BASE_DIR", tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("AI_PROVIDER", "groq")
    settings = Settings.load()
    assert settings.ai_provider == "groq"
    assert settings.ai_base_url == "https://api.groq.com/openai/v1"
    assert settings.ai_model == "llama-3.3-70b-versatile"


def test_base_url_normalisation(monkeypatch, tmp_path) -> None:
    _clear(monkeypatch)
    monkeypatch.setattr("vantab.config.BASE_DIR", tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("AI_BASE_URL", "https://localhost:1234/v1/chat/completions/")
    settings = Settings.load()
    assert settings.ai_base_url == "https://localhost:1234/v1"


def test_owner_ids_and_dev_guild(monkeypatch, tmp_path) -> None:
    _clear(monkeypatch)
    monkeypatch.setattr("vantab.config.BASE_DIR", tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("OWNER_IDS", "1, 2;3,notanid")
    monkeypatch.setenv("DEV_GUILD_ID", "999")
    settings = Settings.load()
    assert settings.owner_ids == frozenset({1, 2, 3})
    assert settings.dev_guild_id == 999


def test_clamping(monkeypatch, tmp_path) -> None:
    _clear(monkeypatch)
    monkeypatch.setattr("vantab.config.BASE_DIR", tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("AI_TEMPERATURE", "9")
    monkeypatch.setenv("AI_MAX_TOKENS", "1")
    monkeypatch.setenv("AI_HISTORY_LIMIT", "-4")
    settings = Settings.load()
    assert settings.ai_temperature == 2.0
    assert settings.ai_max_tokens == 16
    assert settings.ai_history_limit == 0


def test_system_prompt_override_and_file(monkeypatch, tmp_path) -> None:
    _clear(monkeypatch)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("be useful", encoding="utf-8")
    monkeypatch.setattr("vantab.config.BASE_DIR", tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("AI_SYSTEM_PROMPT_FILE", str(prompt))
    assert Settings.load().system_prompt == "be useful"

    monkeypatch.setenv("AI_SYSTEM_PROMPT", "override")
    assert Settings.load().system_prompt == "override"


def test_token_file_fallback(monkeypatch, tmp_path) -> None:
    _clear(monkeypatch)
    monkeypatch.setattr("vantab.config.BASE_DIR", tmp_path)
    (tmp_path / "tkn.txt").write_text("file-token\n", encoding="utf-8")
    assert Settings.load().token == "file-token"


def test_extra_headers_json(monkeypatch, tmp_path) -> None:
    _clear(monkeypatch)
    monkeypatch.setattr("vantab.config.BASE_DIR", tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("AI_EXTRA_HEADERS", '{"X-Custom": "yes"}')
    settings = Settings.load()
    assert settings.ai_extra_headers["X-Custom"] == "yes"

    monkeypatch.setenv("AI_EXTRA_HEADERS", "not json")
    assert "X-Custom" not in Settings.load().ai_extra_headers


def test_public_dict_hides_secrets(settings) -> None:
    public = settings.as_public_dict()
    assert "ai_api_key" not in public
    assert public["ai_model"] == settings.ai_model
