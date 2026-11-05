"""tkn.txt parsing."""

from __future__ import annotations

from pathlib import Path

from vantab.credentials import (
    Credentials,
    credentials_summary,
    normalize_key,
    parse_credentials,
    read_credentials,
)


def test_normalize_key() -> None:
    assert normalize_key("API_KEY") == "api-key"
    assert normalize_key("  Model Name ") == "model-name"
    assert normalize_key("--Base__URL") == "base-url"


def test_parse_basic() -> None:
    data = parse_credentials(
        """
        # comment
        provider=gemini
        model=gemini-3.1-flash-lite
        api-key=AIza123
        """
    )
    assert data == {
        "provider": "gemini",
        "model": "gemini-3.1-flash-lite",
        "api_key": "AIza123",
    }


def test_parse_comments_quotes_and_colons() -> None:
    data = parse_credentials(
        """
        // slash comment
        ; semicolon comment
        apikey: "quoted-key"
        model = 'gemini-3.1-flash-lite'
        base-url=https://example.com/v1
        api_key=second-key  # trailing comment
        """
    )
    assert data["api_key"] == "second-key"
    assert data["model"] == "gemini-3.1-flash-lite"
    assert data["base_url"] == "https://example.com/v1"


def test_parse_export_and_aliases() -> None:
    data = parse_credentials(
        """
        export bot-token=discord-token-value
        API_PROVIDER=gemini
        model_name=gemini-3.1-flash-lite
        gemini-api-key=abc
        """
    )
    assert data["token"] == "discord-token-value"
    assert data["provider"] == "gemini"
    assert data["model"] == "gemini-3.1-flash-lite"
    assert data["api_key"] == "abc"


def test_parse_legacy_bare_token() -> None:
    assert parse_credentials("MTIzNDU2Nzg5.abcdef.ghijklmnop\n") == {"token": "MTIzNDU2Nzg5.abcdef.ghijklmnop"}


def test_parse_json_object() -> None:
    data = parse_credentials('{"discord-token": "t", "provider": "gemini", "api_key": "k"}')
    assert data == {"token": "t", "provider": "gemini", "api_key": "k"}


def test_unknown_keys_ignored() -> None:
    data = parse_credentials("provider=gemini\nsomethingelse=1\ngarbage line\n")
    assert data == {"provider": "gemini"}


def test_empty_input() -> None:
    assert parse_credentials("") == {}
    assert parse_credentials("   \n# only comments\n") == {}


def test_read_credentials_fields(tmp_path: Path) -> None:
    path = tmp_path / "tkn.txt"
    path.write_text(
        "discord-token=tok\nprovider=gemini\nmodel=gemini-3.1-flash-lite\n"
        "api-key=k\nbase-url=https://x/v1\nstyle=normal\ngreeting=hi\ntemperature=0.5\n"
        "max-tokens=500\nowners=1, 2;3\nprefix=?\n",
        encoding="utf-8",
    )
    creds = read_credentials(path)
    assert isinstance(creds, Credentials)
    assert creds.token == "tok"
    assert creds.provider == "gemini"
    assert creds.model == "gemini-3.1-flash-lite"
    assert creds.api_key == "k"
    assert creds.base_url == "https://x/v1"
    assert creds.style == "normal"
    assert creds.greeting == "hi"
    assert creds.temperature == "0.5"
    assert creds.max_tokens == "500"
    assert creds.owner_ids == (1, 2, 3)
    assert creds.prefix == "?"
    assert creds.source == path
    assert not creds.is_empty


def test_read_credentials_missing(tmp_path: Path) -> None:
    assert read_credentials(tmp_path / "missing.txt") is None
    assert read_credentials(None) is None


def test_read_credentials_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("# nothing\n", encoding="utf-8")
    assert read_credentials(path) is None


def test_credentials_summary_hides_secrets(tmp_path: Path) -> None:
    path = tmp_path / "tkn.txt"
    path.write_text("discord-token=secret\ndiscord-token-2=x\napi-key=very-secret\nprovider=gemini\n", encoding="utf-8")
    summary = credentials_summary(read_credentials(path))
    assert summary["has_token"] is True
    assert summary["has_api_key"] is True
    assert "secret" not in str(summary)
    assert credentials_summary(None) == {"file": None}
