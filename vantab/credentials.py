"""Credential file support (``tkn.txt``).

Vantab reads its secrets from a single file next to the bot. The format is
forgiving — one ``key=value`` per line::

    discord-token=MTIz...
    provider=gemini
    model=gemini-3.1-flash-lite
    api-key=AIza...

Accepted variations:

* ``key=value``, ``key: value``, ``export key=value``
* quoted values (``api-key="AIza..."``)
* comments with ``#``, ``//`` or ``;``
* a JSON object instead of lines
* the legacy single-line file that only contains the Discord token
* aliases such as ``apikey``, ``api_key``, ``token``, ``bot-token``,
  ``model-name``, ``baseurl``, ``endpoint`` …

The file is git-ignored and never logged. ``tkn.txt`` values take precedence
over environment variables so the file is the single source of truth.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "Credentials",
    "read_credentials",
    "parse_credentials",
    "normalize_key",
    "DEFAULT_CREDENTIALS_FILE",
]

log = logging.getLogger("vantab.credentials")

DEFAULT_CREDENTIALS_FILE = "tkn.txt"

#: Aliases → canonical key.
_KEY_ALIASES: dict[str, str] = {
    # Discord
    "token": "token",
    "discord-token": "token",
    "discordtoken": "token",
    "discord": "token",
    "bot-token": "token",
    "bottoken": "token",
    "discord-bot-token": "token",
    # AI provider
    "provider": "provider",
    "ai-provider": "provider",
    "aiprovider": "provider",
    "api-provider": "provider",
    # AI key
    "api-key": "api_key",
    "apikey": "api_key",
    "api": "api_key",
    "key": "api_key",
    "ai-api-key": "api_key",
    "gemini-api-key": "api_key",
    "openai-api-key": "api_key",
    "google-api-key": "api_key",
    "anthropic-api-key": "api_key",
    # Model
    "model": "model",
    "ai-model": "model",
    "aimodel": "model",
    "model-name": "model",
    "modelname": "model",
    "gemini-model": "model",
    # Endpoint
    "base-url": "base_url",
    "baseurl": "base_url",
    "base": "base_url",
    "url": "base_url",
    "endpoint": "base_url",
    "ai-base-url": "base_url",
    "api-url": "base_url",
    # Persona
    "style": "style",
    "ai-style": "style",
    "persona": "style",
    "persona-style": "style",
    "greeting": "greeting",
    "salutation": "greeting",
    # Generation
    "temperature": "temperature",
    "temp": "temperature",
    "max-tokens": "max_tokens",
    "maxtokens": "max_tokens",
    "max-token": "max_tokens",
    "tokens": "max_tokens",
    # Misc
    "owners": "owner_ids",
    "owner-ids": "owner_ids",
    "ownerids": "owner_ids",
    "admins": "owner_ids",
    "prefix": "prefix",
}

#: Canonical keys the rest of the app understands.
_KNOWN = {
    "token",
    "provider",
    "api_key",
    "model",
    "base_url",
    "style",
    "greeting",
    "temperature",
    "max_tokens",
    "owner_ids",
    "prefix",
}


def normalize_key(raw: str) -> str:
    """Normalise a key: ``"API_Key"`` → ``"api-key"``."""
    key = raw.strip().lower().lstrip("-").replace("_", "-").replace(" ", "-")
    key = re.sub(r"-{2,}", "-", key)
    return key


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1].strip()
    return value


def _clean_value(raw: str) -> str:
    value = raw.strip()
    value = _unquote(value)
    if value.endswith(","):
        value = value[:-1].rstrip()
    return _unquote(value)


def parse_credentials(text: str) -> dict[str, str]:
    """Parse the credential file content into canonical ``key → value`` pairs."""
    if not text:
        return {}
    text = text.lstrip("\ufeff")
    stripped = text.strip()
    if not stripped:
        return {}

    # JSON form: {"provider": "gemini", ...}
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, Mapping):
            result: dict[str, str] = {}
            for key, value in data.items():
                canonical = _KEY_ALIASES.get(normalize_key(str(key)))
                if canonical and value not in (None, ""):
                    result[canonical] = str(value).strip()
            return result

    result: dict[str, str] = {}
    lines = [line.strip() for line in text.splitlines()]
    content_lines = [
        line
        for line in lines
        if line and not line.startswith(("#", "//", ";", "["))
    ]

    # Legacy format: the file only contains the Discord bot token.
    if len(content_lines) == 1 and "=" not in content_lines[0] and ":" not in content_lines[0]:
        return {"token": _clean_value(content_lines[0])}

    for line in content_lines:
        line = re.sub(r"^export\s+", "", line, flags=re.IGNORECASE)
        eq = line.find("=")
        colon = line.find(":")
        if eq == -1 and colon == -1:
            continue
        # Prefer "=" so values such as https://… survive intact.
        split_at = eq if eq != -1 else colon
        key_part = line[:split_at]
        value_part = line[split_at + 1 :]
        canonical = _KEY_ALIASES.get(normalize_key(key_part))
        if canonical is None:
            canonical = normalize_key(key_part)
            if canonical not in _KNOWN:
                log.debug("Ignoring unknown credential key %r", key_part.strip())
                continue
        value = _clean_value(re.sub(r"\s+#.*$", "", value_part))
        if value:
            result[canonical] = value
    return result


@dataclass(frozen=True, slots=True)
class Credentials:
    token: str | None = None
    provider: str | None = None
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    style: str | None = None
    greeting: str | None = None
    temperature: str | None = None
    max_tokens: str | None = None
    owner_ids: tuple[int, ...] = ()
    prefix: str | None = None
    source: Path | None = None
    extra: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, str], *, source: Path | None = None) -> Credentials:
        owners = tuple(
            int(chunk) for chunk in re.split(r"[,;\s]+", data.get("owner_ids", "")) if chunk.isdigit()
        )
        known = {key: value for key, value in data.items() if key in _KNOWN}
        extra = {key: value for key, value in data.items() if key not in _KNOWN}
        return cls(
            token=known.get("token"),
            provider=known.get("provider"),
            api_key=known.get("api_key"),
            model=known.get("model"),
            base_url=known.get("base_url"),
            style=known.get("style"),
            greeting=known.get("greeting"),
            temperature=known.get("temperature"),
            max_tokens=known.get("max_tokens"),
            owner_ids=owners,
            prefix=known.get("prefix"),
            source=source,
            extra=extra,
        )

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.token,
                self.provider,
                self.api_key,
                self.model,
                self.base_url,
                self.style,
                self.greeting,
            )
        )


def read_credentials(path: str | Path | None) -> Credentials | None:
    """Read and parse a credentials file. Returns ``None`` if it is missing/empty."""
    if path is None:
        return None
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("Could not read %s (%s)", file_path, exc)
        return None

    data = parse_credentials(text)
    if not data:
        return None
    return Credentials.from_mapping(data, source=file_path)


def credentials_summary(credentials: Credentials | None) -> dict[str, Any]:
    """Safe-to-display summary (no secrets)."""
    if credentials is None:
        return {"file": None}
    return {
        "file": str(credentials.source) if credentials.source else None,
        "provider": credentials.provider,
        "model": credentials.model,
        "style": credentials.style,
        "has_token": bool(credentials.token),
        "has_api_key": bool(credentials.api_key),
    }
