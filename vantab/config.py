"""Environment-driven configuration for Vantab.

Everything is loaded once at start-up from ``.env`` (or the process environment).
No secrets are ever hard-coded: if the Discord token is missing the bot refuses
to boot with a helpful message instead of crashing with a stack trace.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .persona import Persona, load_persona

BASE_DIR = Path(__file__).resolve().parent.parent

#: Provider presets so users only have to set ``AI_PROVIDER`` + ``AI_API_KEY``.
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile"},
    "together": {"base_url": "https://api.together.xyz/v1", "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo"},
    "deepinfra": {"base_url": "https://api.deepinfra.com/v1/openai", "model": "meta-llama/Llama-3.3-70B-Instruct"},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "model": "mistral-large-latest"},
    "ollama": {"base_url": "http://127.0.0.1:11434/v1", "model": "llama3.1"},
    "lmstudio": {"base_url": "http://127.0.0.1:1234/v1", "model": "local-model"},
    "custom": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = _env(name)
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _float(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    raw = _env(name)
    try:
        value = float(raw) if raw is not None else default
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _id_set(name: str) -> frozenset[int]:
    raw = _env(name, "") or ""
    out: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            out.add(int(chunk))
    return frozenset(out)


def _read_token_file() -> str | None:
    """Legacy support for the ``tkn.txt`` file the original repo shipped with."""
    for candidate in (BASE_DIR / "tkn.txt", BASE_DIR / "token.txt"):
        try:
            if candidate.is_file():
                token = candidate.read_text(encoding="utf-8").strip()
                if token:
                    return token
        except OSError:  # pragma: no cover - unreadable file
            continue
    return None


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable snapshot of the bot configuration."""

    token: str
    owner_ids: frozenset[int]
    default_prefix: str
    database_path: Path
    log_level: str
    dev_guild_id: int | None
    status_text: str
    activity_type: str

    # --- AI -----------------------------------------------------------------
    ai_enabled: bool
    ai_provider: str
    ai_base_url: str
    ai_api_key: str
    ai_model: str
    ai_temperature: float
    ai_max_tokens: int
    ai_timeout: float
    ai_stream: bool
    ai_history_limit: int
    ai_history_ttl_days: int
    ai_cooldown: float
    ai_system_prompt_path: Path | None
    ai_system_prompt_override: str | None
    ai_vision: bool

    # --- moderation ---------------------------------------------------------
    default_mute_minutes: int
    max_timeout_minutes: int
    dm_on_punish: bool
    antinuke_poll_seconds: float

    ai_extra_headers: Mapping[str, str] = field(default_factory=dict)

    # --- persona -------------------------------------------------------------
    persona_file: Path = BASE_DIR / "prompts" / "persona.json"
    ai_style: str = "elee"
    ai_greeting: str | None = None

    @property
    def persona(self) -> Persona:
        """The loaded persona/styles, cached per process."""
        return load_persona(self.persona_file or None)

    @property
    def system_prompt(self) -> str:
        """Default system prompt.

        Precedence: ``AI_SYSTEM_PROMPT`` → ``AI_SYSTEM_PROMPT_FILE`` → persona style.
        """
        if self.ai_system_prompt_override:
            return self.ai_system_prompt_override
        if self.ai_system_prompt_path is not None:
            try:
                text = self.ai_system_prompt_path.read_text(encoding="utf-8").strip()
                if text:
                    return text
            except OSError:
                pass
        return self.persona.system_prompt(style=self.ai_style)

    @classmethod
    def load(cls) -> Settings:
        load_dotenv(BASE_DIR / ".env", override=False)

        provider = (_env("AI_PROVIDER", "openai") or "openai").lower()
        preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["custom"])

        token = _env("DISCORD_TOKEN") or _read_token_file() or ""
        if not token:
            raise SystemExit(
                "Missing Discord token.\n"
                "Set DISCORD_TOKEN in your environment/.env file,\n"
                "or create a tkn.txt file containing the token."
            )

        base_url = (_env("AI_BASE_URL", preset["base_url"]) or preset["base_url"]).rstrip("/")
        # Be forgiving: allow passing the full /chat/completions URL.
        if base_url.endswith("/chat/completions"):
            base_url = base_url[: -len("/chat/completions")]

        database_path = Path(_env("DATABASE_PATH", str(BASE_DIR / "data" / "vantab.db")) or "")
        if not database_path.is_absolute():
            database_path = (BASE_DIR / database_path).resolve()

        prompt_raw = _env("AI_SYSTEM_PROMPT_FILE", "")
        prompt_path: Path | None = None
        if prompt_raw:
            prompt_path = Path(prompt_raw)
            if not prompt_path.is_absolute():
                prompt_path = (BASE_DIR / prompt_path).resolve()

        persona_path = Path(_env("PERSONA_FILE", "prompts/persona.json") or "")
        if not persona_path.is_absolute():
            persona_path = (BASE_DIR / persona_path).resolve()

        extra_headers: dict[str, str] = {}
        raw_headers = _env("AI_EXTRA_HEADERS")
        if raw_headers:
            try:
                parsed_headers = json.loads(raw_headers)
                if isinstance(parsed_headers, dict):
                    extra_headers.update({str(k): str(v) for k, v in parsed_headers.items()})
            except json.JSONDecodeError:
                logging.getLogger("vantab.config").warning("AI_EXTRA_HEADERS is not valid JSON — ignored")
        if provider == "openrouter":
            extra_headers["HTTP-Referer"] = "https://github.com/3l3euwu/vantab0bot"
            extra_headers["X-Title"] = "Vantab"

        persona = load_persona(persona_path)
        style = (_env("AI_STYLE", "") or "").strip().lower() or persona.default_style

        dev_guild = _env("DEV_GUILD_ID")
        return cls(
            token=token,
            owner_ids=_id_set("OWNER_IDS"),
            default_prefix=_env("DEFAULT_PREFIX", "~") or "~",
            database_path=database_path,
            log_level=(_env("LOG_LEVEL", "INFO") or "INFO").upper(),
            dev_guild_id=int(dev_guild) if dev_guild and dev_guild.isdigit() else None,
            status_text=_env("STATUS_TEXT", "over {guilds} servers · ~{{question}}") or "",
            activity_type=(_env("ACTIVITY_TYPE", "watching") or "watching").lower(),
            ai_enabled=_bool("AI_ENABLED", True),
            ai_provider=provider,
            ai_base_url=base_url,
            ai_api_key=_env("AI_API_KEY", "not-needed") or "not-needed",
            ai_model=_env("AI_MODEL", preset["model"]) or preset["model"],
            ai_temperature=_float("AI_TEMPERATURE", 0.8, minimum=0.0, maximum=2.0),
            ai_max_tokens=_int("AI_MAX_TOKENS", 1200, minimum=16, maximum=32000),
            ai_timeout=_float("AI_TIMEOUT", 120.0, minimum=5.0, maximum=600.0),
            ai_stream=_bool("AI_STREAM", True),
            ai_history_limit=_int("AI_HISTORY_LIMIT", 24, minimum=0, maximum=200),
            ai_history_ttl_days=_int("AI_HISTORY_TTL_DAYS", 14, minimum=1, maximum=365),
            ai_cooldown=_float("AI_COOLDOWN", 2.0, minimum=0.0, maximum=60.0),
            ai_system_prompt_path=prompt_path,
            ai_system_prompt_override=_env("AI_SYSTEM_PROMPT"),
            ai_vision=_bool("AI_VISION", True),
            ai_extra_headers=extra_headers,
            default_mute_minutes=_int("DEFAULT_MUTE_MINUTES", 10, minimum=1, maximum=40320),
            max_timeout_minutes=_int("MAX_TIMEOUT_MINUTES", 40320, minimum=1, maximum=40320),
            dm_on_punish=_bool("DM_ON_PUNISH", True),
            antinuke_poll_seconds=_float("ANTINUKE_POLL_SECONDS", 6.0, minimum=2.0, maximum=120.0),
            persona_file=persona_path,
            ai_style=style,
            ai_greeting=_env("AI_GREETING"),
        )

    def as_public_dict(self) -> dict[str, Any]:
        """Configuration view that is safe to show in Discord (no secrets)."""
        return {
            "prefix": self.default_prefix,
            "ai_enabled": self.ai_enabled,
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_base_url": self.ai_base_url,
            "ai_stream": self.ai_stream,
            "ai_vision": self.ai_vision,
            "ai_style": self.ai_style,
            "persona": self.persona.bot_name,
            "database": str(self.database_path),
            "dev_guild": self.dev_guild_id,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings.load()
