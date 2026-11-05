"""SQLite persistence layer for Vantab.

A single :class:`aiosqlite` connection is shared across cogs; SQLite handles the
serialisation for us and the bot is nowhere near busy enough to need a pool.
Everything is created lazily so upgrading a running bot only requires a restart.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import aiosqlite

__all__ = [
    "Database",
    "GuildSettings",
    "RuleConfig",
    "WarningEntry",
    "CaseEntry",
    "TicketEntry",
    "DEFAULT_ESCALATION",
    "ACTION_WEIGHTS",
]

ActionLiteral = Literal["none", "delete", "warn", "timeout", "kick", "ban"]

DEFAULT_ESCALATION: list[dict[str, Any]] = [
    {"warns": 3, "action": "timeout", "duration": 3600},
    {"warns": 5, "action": "kick", "duration": 0},
    {"warns": 7, "action": "ban", "duration": 0},
]

#: Used by the security cog to weigh audit-log entries in anti-nuke scoring.
ACTION_WEIGHTS: dict[str, int] = {
    "ban": 3,
    "kick": 2,
    "channel_delete": 3,
    "role_delete": 3,
    "webhook_create": 4,
    "bot_add": 2,
    "member_prune": 4,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS guilds (
    guild_id            INTEGER PRIMARY KEY,
    prefix              TEXT,
    mod_log_channel     INTEGER,
    automod_log_channel INTEGER,
    ai_channel          INTEGER,
    ai_enabled          INTEGER NOT NULL DEFAULT 1,
    automod_enabled     INTEGER NOT NULL DEFAULT 1,
    antinuke_enabled    INTEGER NOT NULL DEFAULT 1,
    antinuke_log_channel INTEGER,
    mute_role_id        INTEGER,
    verify_channel_id   INTEGER,
    verify_role_id      INTEGER,
    verify_enabled      INTEGER NOT NULL DEFAULT 0,
    raid_mode           INTEGER NOT NULL DEFAULT 0,
    escalation          TEXT,
    ai_system_prompt    TEXT,
    ai_style            TEXT,
    warn_decay_days     INTEGER NOT NULL DEFAULT 30,
    verify_timeout_seconds INTEGER NOT NULL DEFAULT 600,
    ticket_category_id  INTEGER,
    ticket_staff_role_id INTEGER,
    ticket_log_channel  INTEGER,
    tickets_enabled     INTEGER NOT NULL DEFAULT 1,
    ticket_max_open     INTEGER NOT NULL DEFAULT 1,
    ticket_counter      INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS automod_rules (
    guild_id   INTEGER NOT NULL,
    rule       TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1,
    action     TEXT NOT NULL DEFAULT 'delete',
    duration   INTEGER NOT NULL DEFAULT 0,
    threshold  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, rule)
);

CREATE TABLE IF NOT EXISTS automod_exemptions (
    guild_id  INTEGER NOT NULL,
    kind      TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, kind, target_id)
);

CREATE TABLE IF NOT EXISTS badwords (
    guild_id INTEGER NOT NULL,
    word     TEXT NOT NULL,
    PRIMARY KEY (guild_id, word)
);

CREATE TABLE IF NOT EXISTS allowlist_domains (
    guild_id INTEGER NOT NULL,
    domain   TEXT NOT NULL,
    PRIMARY KEY (guild_id, domain)
);

CREATE TABLE IF NOT EXISTS warnings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    case_id      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_warnings_user ON warnings (guild_id, user_id, active);

CREATE TABLE IF NOT EXISTS cases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    case_number  INTEGER NOT NULL,
    action       TEXT NOT NULL,
    user_id      INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    duration     INTEGER NOT NULL DEFAULT 0,
    expires_at   TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    automated    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (guild_id, case_number)
);
CREATE INDEX IF NOT EXISTS idx_cases_user ON cases (guild_id, user_id);

CREATE TABLE IF NOT EXISTS ai_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_lookup ON ai_messages (guild_id, channel_id, id);

CREATE TABLE IF NOT EXISTS tags (
    guild_id  INTEGER NOT NULL,
    name      TEXT NOT NULL,
    content   TEXT NOT NULL,
    owner_id  INTEGER NOT NULL,
    uses      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, name)
);

CREATE TABLE IF NOT EXISTS antinuke_whitelist (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS antinuke_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    action     TEXT NOT NULL,
    target_id  INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    channel_id    INTEGER NOT NULL UNIQUE,
    number        INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    subject       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'open',
    claimed_by    INTEGER,
    created_at    TEXT NOT NULL,
    closed_at     TEXT,
    closed_by     INTEGER,
    close_reason  TEXT NOT NULL DEFAULT '',
    UNIQUE (guild_id, number)
);
CREATE INDEX IF NOT EXISTS idx_tickets_guild ON tickets (guild_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets (guild_id, user_id, status);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).astimezone(UTC).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        # Handles SQLite's "YYYY-MM-DD HH:MM:SS" default format.
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


@dataclass(slots=True)
class GuildSettings:
    guild_id: int
    prefix: str | None = None
    mod_log_channel: int | None = None
    automod_log_channel: int | None = None
    ai_channel: int | None = None
    ai_enabled: bool = True
    automod_enabled: bool = True
    antinuke_enabled: bool = True
    antinuke_log_channel: int | None = None
    mute_role_id: int | None = None
    verify_channel_id: int | None = None
    verify_role_id: int | None = None
    verify_enabled: bool = False
    raid_mode: bool = False
    escalation: list[dict[str, Any]] = field(default_factory=lambda: [dict(e) for e in DEFAULT_ESCALATION])
    ai_system_prompt: str | None = None
    ai_style: str | None = None
    warn_decay_days: int = 30
    verify_timeout_seconds: int = 600
    ticket_category_id: int | None = None
    ticket_staff_role_id: int | None = None
    ticket_log_channel: int | None = None
    tickets_enabled: bool = True
    ticket_max_open: int = 1
    ticket_counter: int = 0

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> GuildSettings:
        data = dict(row)
        escalation = data.get("escalation")
        try:
            parsed_escalation = json.loads(escalation) if escalation else [dict(e) for e in DEFAULT_ESCALATION]
            if not isinstance(parsed_escalation, list):
                raise ValueError
        except (ValueError, TypeError):
            parsed_escalation = [dict(e) for e in DEFAULT_ESCALATION]
        return cls(
            guild_id=data["guild_id"],
            prefix=data.get("prefix"),
            mod_log_channel=data.get("mod_log_channel"),
            automod_log_channel=data.get("automod_log_channel"),
            ai_channel=data.get("ai_channel"),
            ai_enabled=bool(data.get("ai_enabled", 1)),
            automod_enabled=bool(data.get("automod_enabled", 1)),
            antinuke_enabled=bool(data.get("antinuke_enabled", 1)),
            antinuke_log_channel=data.get("antinuke_log_channel"),
            mute_role_id=data.get("mute_role_id"),
            verify_channel_id=data.get("verify_channel_id"),
            verify_role_id=data.get("verify_role_id"),
            verify_enabled=bool(data.get("verify_enabled", 0)),
            raid_mode=bool(data.get("raid_mode", 0)),
            escalation=parsed_escalation,
            ai_system_prompt=data.get("ai_system_prompt"),
            ai_style=data.get("ai_style"),
            warn_decay_days=data.get("warn_decay_days", 30) or 30,
            verify_timeout_seconds=data.get("verify_timeout_seconds", 600) or 600,
            ticket_category_id=data.get("ticket_category_id"),
            ticket_staff_role_id=data.get("ticket_staff_role_id"),
            ticket_log_channel=data.get("ticket_log_channel"),
            tickets_enabled=bool(data.get("tickets_enabled", 1)),
            ticket_max_open=int(data.get("ticket_max_open", 1) or 1),
            ticket_counter=int(data.get("ticket_counter", 0) or 0),
        )


@dataclass(slots=True)
class RuleConfig:
    rule: str
    enabled: bool = True
    action: str = "delete"
    duration: int = 0
    threshold: int = 0

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> RuleConfig:
        return cls(
            rule=row["rule"],
            enabled=bool(row["enabled"]),
            action=row["action"],
            duration=int(row["duration"] or 0),
            threshold=int(row["threshold"] or 0),
        )


@dataclass(slots=True)
class WarningEntry:
    id: int
    guild_id: int
    user_id: int
    moderator_id: int
    reason: str
    created_at: datetime
    active: bool
    case_id: int | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> WarningEntry:
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            moderator_id=row["moderator_id"],
            reason=row["reason"] or "",
            created_at=parse_iso(row["created_at"]) or utcnow(),
            active=bool(row["active"]),
            case_id=row["case_id"],
        )


@dataclass(slots=True)
class CaseEntry:
    id: int
    guild_id: int
    case_number: int
    action: str
    user_id: int
    moderator_id: int
    reason: str
    created_at: datetime
    duration: int
    expires_at: datetime | None
    active: bool
    automated: bool

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> CaseEntry:
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            case_number=row["case_number"],
            action=row["action"],
            user_id=row["user_id"],
            moderator_id=row["moderator_id"],
            reason=row["reason"] or "",
            created_at=parse_iso(row["created_at"]) or utcnow(),
            duration=int(row["duration"] or 0),
            expires_at=parse_iso(row["expires_at"]),
            active=bool(row["active"]),
            automated=bool(row["automated"]),
        )


@dataclass(slots=True)
class TicketEntry:
    id: int
    guild_id: int
    channel_id: int
    number: int
    user_id: int
    subject: str
    status: str
    claimed_by: int | None
    created_at: datetime
    closed_at: datetime | None
    closed_by: int | None
    close_reason: str

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> TicketEntry:
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            number=int(row["number"]),
            user_id=row["user_id"],
            subject=row["subject"] or "",
            status=row["status"] or "open",
            claimed_by=row["claimed_by"],
            created_at=parse_iso(row["created_at"]) or utcnow(),
            closed_at=parse_iso(row["closed_at"]),
            closed_by=row["closed_by"],
            close_reason=row["close_reason"] or "",
        )


class Database:
    """Thin async wrapper around the Vantab schema."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ setup
    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._migrate()

    async def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        rows = await self._fetchall("PRAGMA table_info(guilds)")
        columns = {row["name"] for row in rows}
        additions = {
            "ai_style": "TEXT",
            "ticket_category_id": "INTEGER",
            "ticket_staff_role_id": "INTEGER",
            "ticket_log_channel": "INTEGER",
            "tickets_enabled": "INTEGER NOT NULL DEFAULT 1",
            "ticket_max_open": "INTEGER NOT NULL DEFAULT 1",
            "ticket_counter": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, declaration in additions.items():
            if name not in columns:
                await self.conn.execute(f"ALTER TABLE guilds ADD COLUMN {name} {declaration}")
        await self.conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() must be awaited before use")
        return self._conn

    async def _execute(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Cursor:
        async with self._lock:
            cursor = await self.conn.execute(sql, params)
            await self.conn.commit()
            return cursor

    async def _executemany(self, sql: str, params: Iterable[Sequence[Any]]) -> None:
        async with self._lock:
            await self.conn.executemany(sql, params)
            await self.conn.commit()

    async def _fetchone(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(sql, params)
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return list(rows)

    # ----------------------------------------------------------------- guilds
    async def ensure_guild(self, guild_id: int) -> None:
        await self._execute(
            "INSERT INTO guilds (guild_id) VALUES (?) ON CONFLICT(guild_id) DO NOTHING",
            (guild_id,),
        )

    async def get_guild(self, guild_id: int) -> GuildSettings:
        await self.ensure_guild(guild_id)
        row = await self._fetchone("SELECT * FROM guilds WHERE guild_id = ?", (guild_id,))
        assert row is not None
        return GuildSettings.from_row(row)

    _GUILD_FIELDS = {
        "prefix",
        "mod_log_channel",
        "automod_log_channel",
        "ai_channel",
        "ai_enabled",
        "automod_enabled",
        "antinuke_enabled",
        "antinuke_log_channel",
        "mute_role_id",
        "verify_channel_id",
        "verify_role_id",
        "verify_enabled",
        "raid_mode",
        "escalation",
        "ai_system_prompt",
        "ai_style",
        "warn_decay_days",
        "verify_timeout_seconds",
        "ticket_category_id",
        "ticket_staff_role_id",
        "ticket_log_channel",
        "tickets_enabled",
        "ticket_max_open",
        "ticket_counter",
    }

    async def update_guild(self, guild_id: int, **fields: Any) -> GuildSettings:
        unknown = set(fields) - self._GUILD_FIELDS
        if unknown:
            raise ValueError(f"unknown guild settings: {sorted(unknown)}")
        await self.ensure_guild(guild_id)
        if fields:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            values: list[Any] = []
            for value in fields.values():
                if isinstance(value, bool):
                    values.append(int(value))
                elif isinstance(value, (list, dict)):
                    values.append(json.dumps(value))
                else:
                    values.append(value)
            values.append(iso())
            values.append(guild_id)
            await self._execute(
                f"UPDATE guilds SET {assignments}, updated_at = ? WHERE guild_id = ?",
                values,
            )
        return await self.get_guild(guild_id)

    async def all_guilds(self) -> list[GuildSettings]:
        rows = await self._fetchall("SELECT * FROM guilds")
        return [GuildSettings.from_row(row) for row in rows]

    async def guild_count(self) -> int:
        row = await self._fetchone("SELECT COUNT(*) AS c FROM guilds")
        return int(row["c"]) if row else 0

    # ------------------------------------------------------------------ rules
    async def get_rules(self, guild_id: int) -> dict[str, RuleConfig]:
        rows = await self._fetchall("SELECT * FROM automod_rules WHERE guild_id = ?", (guild_id,))
        return {row["rule"]: RuleConfig.from_row(row) for row in rows}

    async def get_rule(self, guild_id: int, rule: str) -> RuleConfig | None:
        row = await self._fetchone(
            "SELECT * FROM automod_rules WHERE guild_id = ? AND rule = ?", (guild_id, rule)
        )
        return RuleConfig.from_row(row) if row else None

    async def set_rule(
        self,
        guild_id: int,
        rule: str,
        *,
        enabled: bool | None = None,
        action: str | None = None,
        duration: int | None = None,
        threshold: int | None = None,
    ) -> RuleConfig:
        existing = await self.get_rule(guild_id, rule)
        current = existing or RuleConfig(rule=rule)
        updated = RuleConfig(
            rule=rule,
            enabled=current.enabled if enabled is None else enabled,
            action=current.action if action is None else action,
            duration=current.duration if duration is None else duration,
            threshold=current.threshold if threshold is None else threshold,
        )
        await self._execute(
            """
            INSERT INTO automod_rules (guild_id, rule, enabled, action, duration, threshold)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, rule) DO UPDATE SET
                enabled = excluded.enabled,
                action = excluded.action,
                duration = excluded.duration,
                threshold = excluded.threshold
            """,
            (guild_id, rule, int(updated.enabled), updated.action, updated.duration, updated.threshold),
        )
        return updated

    async def delete_rule(self, guild_id: int, rule: str) -> bool:
        cursor = await self._execute(
            "DELETE FROM automod_rules WHERE guild_id = ? AND rule = ?", (guild_id, rule)
        )
        return cursor.rowcount > 0

    # ------------------------------------------------------------- exemptions
    async def get_exemptions(self, guild_id: int) -> dict[str, set[int]]:
        rows = await self._fetchall(
            "SELECT kind, target_id FROM automod_exemptions WHERE guild_id = ?", (guild_id,)
        )
        result: dict[str, set[int]] = {"channel": set(), "role": set(), "user": set()}
        for row in rows:
            result.setdefault(row["kind"], set()).add(row["target_id"])
        return result

    async def add_exemption(self, guild_id: int, kind: str, target_id: int) -> bool:
        if kind not in {"channel", "role", "user"}:
            raise ValueError("kind must be channel, role or user")
        cursor = await self._execute(
            "INSERT INTO automod_exemptions (guild_id, kind, target_id) VALUES (?, ?, ?) "
            "ON CONFLICT DO NOTHING",
            (guild_id, kind, target_id),
        )
        return cursor.rowcount > 0

    async def remove_exemption(self, guild_id: int, kind: str, target_id: int) -> bool:
        cursor = await self._execute(
            "DELETE FROM automod_exemptions WHERE guild_id = ? AND kind = ? AND target_id = ?",
            (guild_id, kind, target_id),
        )
        return cursor.rowcount > 0

    # --------------------------------------------------------------- badwords
    async def get_badwords(self, guild_id: int) -> set[str]:
        rows = await self._fetchall("SELECT word FROM badwords WHERE guild_id = ?", (guild_id,))
        return {row["word"] for row in rows}

    async def add_badword(self, guild_id: int, word: str) -> bool:
        word = word.strip().lower()
        if not word:
            raise ValueError("word cannot be empty")
        cursor = await self._execute(
            "INSERT INTO badwords (guild_id, word) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (guild_id, word),
        )
        return cursor.rowcount > 0

    async def remove_badword(self, guild_id: int, word: str) -> bool:
        cursor = await self._execute(
            "DELETE FROM badwords WHERE guild_id = ? AND word = ?", (guild_id, word.strip().lower())
        )
        return cursor.rowcount > 0

    # -------------------------------------------------------------- allowlist
    async def get_allowlist(self, guild_id: int) -> set[str]:
        rows = await self._fetchall(
            "SELECT domain FROM allowlist_domains WHERE guild_id = ?", (guild_id,)
        )
        return {row["domain"] for row in rows}

    async def add_domain(self, guild_id: int, domain: str) -> bool:
        domain = domain.strip().lower().removeprefix("http://").removeprefix("https://").strip("/")
        if "." not in domain:
            raise ValueError("that does not look like a domain")
        cursor = await self._execute(
            "INSERT INTO allowlist_domains (guild_id, domain) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (guild_id, domain),
        )
        return cursor.rowcount > 0

    async def remove_domain(self, guild_id: int, domain: str) -> bool:
        cursor = await self._execute(
            "DELETE FROM allowlist_domains WHERE guild_id = ? AND domain = ?",
            (guild_id, domain.strip().lower()),
        )
        return cursor.rowcount > 0

    # --------------------------------------------------------------- warnings
    async def add_warning(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        reason: str,
        case_id: int | None = None,
        created_at: datetime | None = None,
    ) -> int:
        cursor = await self._execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at, active, case_id) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (guild_id, user_id, moderator_id, reason or "", iso(created_at), case_id),
        )
        return int(cursor.lastrowid)

    async def count_warnings(
        self, guild_id: int, user_id: int, *, since: datetime | None = None
    ) -> int:
        if since is None:
            row = await self._fetchone(
                "SELECT COUNT(*) AS c FROM warnings WHERE guild_id = ? AND user_id = ? AND active = 1",
                (guild_id, user_id),
            )
        else:
            row = await self._fetchone(
                "SELECT COUNT(*) AS c FROM warnings WHERE guild_id = ? AND user_id = ? "
                "AND active = 1 AND created_at >= ?",
                (guild_id, user_id, iso(since)),
            )
        return int(row["c"]) if row else 0

    async def list_warnings(
        self, guild_id: int, user_id: int | None = None, *, active_only: bool = False, limit: int = 50
    ) -> list[WarningEntry]:
        clauses = ["guild_id = ?"]
        params: list[Any] = [guild_id]
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if active_only:
            clauses.append("active = 1")
        params.append(limit)
        rows = await self._fetchall(
            f"SELECT * FROM warnings WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?",
            params,
        )
        return [WarningEntry.from_row(row) for row in rows]

    async def get_warning(self, warning_id: int) -> WarningEntry | None:
        row = await self._fetchone("SELECT * FROM warnings WHERE id = ?", (warning_id,))
        return WarningEntry.from_row(row) if row else None

    async def deactivate_warning(self, warning_id: int) -> bool:
        cursor = await self._execute("UPDATE warnings SET active = 0 WHERE id = ?", (warning_id,))
        return cursor.rowcount > 0

    async def clear_warnings(self, guild_id: int, user_id: int) -> int:
        cursor = await self._execute(
            "UPDATE warnings SET active = 0 WHERE guild_id = ? AND user_id = ? AND active = 1",
            (guild_id, user_id),
        )
        return cursor.rowcount

    async def decay_warnings(self, guild_id: int, older_than_days: int) -> int:
        cutoff = iso(utcnow() - timedelta(days=max(1, older_than_days)))
        cursor = await self._execute(
            "UPDATE warnings SET active = 0 WHERE guild_id = ? AND active = 1 AND created_at < ?",
            (guild_id, cutoff),
        )
        return cursor.rowcount

    # ------------------------------------------------------------------ cases
    async def add_case(
        self,
        guild_id: int,
        *,
        action: str,
        user_id: int,
        moderator_id: int,
        reason: str = "",
        duration: int = 0,
        automated: bool = False,
        created_at: datetime | None = None,
    ) -> CaseEntry:
        created = created_at or utcnow()
        expires_at = created + timedelta(seconds=duration) if duration > 0 else None
        async with self._lock:
            cursor = await self.conn.execute(
                "SELECT COALESCE(MAX(case_number), 0) + 1 AS n FROM cases WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            number = int(row["n"]) if row else 1
            cursor = await self.conn.execute(
                "INSERT INTO cases (guild_id, case_number, action, user_id, moderator_id, reason, "
                "created_at, duration, expires_at, active, automated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    guild_id,
                    number,
                    action,
                    user_id,
                    moderator_id,
                    reason or "",
                    iso(created),
                    duration,
                    iso(expires_at) if expires_at else None,
                    int(automated),
                ),
            )
            await self.conn.commit()
            await cursor.close()
        entry = await self.get_case(guild_id, number)
        assert entry is not None
        return entry

    async def get_case(self, guild_id: int, case_number: int) -> CaseEntry | None:
        row = await self._fetchone(
            "SELECT * FROM cases WHERE guild_id = ? AND case_number = ?", (guild_id, case_number)
        )
        return CaseEntry.from_row(row) if row else None

    async def list_cases(
        self, guild_id: int, *, user_id: int | None = None, action: str | None = None, limit: int = 25
    ) -> list[CaseEntry]:
        clauses = ["guild_id = ?"]
        params: list[Any] = [guild_id]
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if action is not None:
            clauses.append("action = ?")
            params.append(action)
        params.append(limit)
        rows = await self._fetchall(
            f"SELECT * FROM cases WHERE {' AND '.join(clauses)} ORDER BY case_number DESC LIMIT ?",
            params,
        )
        return [CaseEntry.from_row(row) for row in rows]

    async def update_case_reason(self, guild_id: int, case_number: int, reason: str) -> bool:
        cursor = await self._execute(
            "UPDATE cases SET reason = ? WHERE guild_id = ? AND case_number = ?",
            (reason, guild_id, case_number),
        )
        return cursor.rowcount > 0

    async def deactivate_case(self, guild_id: int, case_number: int) -> bool:
        cursor = await self._execute(
            "UPDATE cases SET active = 0 WHERE guild_id = ? AND case_number = ?",
            (guild_id, case_number),
        )
        return cursor.rowcount > 0

    async def expired_cases(self, now: datetime | None = None) -> list[CaseEntry]:
        rows = await self._fetchall(
            "SELECT * FROM cases WHERE active = 1 AND expires_at IS NOT NULL AND expires_at <= ?",
            (iso(now),),
        )
        return [CaseEntry.from_row(row) for row in rows]

    async def case_count(self, guild_id: int | None = None) -> int:
        if guild_id is None:
            row = await self._fetchone("SELECT COUNT(*) AS c FROM cases")
        else:
            row = await self._fetchone("SELECT COUNT(*) AS c FROM cases WHERE guild_id = ?", (guild_id,))
        return int(row["c"]) if row else 0

    # --------------------------------------------------------------- ai memory
    async def add_ai_message(
        self,
        guild_id: int,
        channel_id: int,
        user_id: int,
        role: str,
        content: str,
        *,
        created_at: datetime | None = None,
    ) -> None:
        await self._execute(
            "INSERT INTO ai_messages (guild_id, channel_id, user_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, user_id, role, content, iso(created_at)),
        )

    async def get_ai_history(
        self, guild_id: int, channel_id: int, *, limit: int = 20, since: datetime | None = None
    ) -> list[tuple[str, str]]:
        """Return ``(role, content)`` pairs, oldest first."""
        params: list[Any] = [guild_id, channel_id]
        clause = ""
        if since is not None:
            clause = " AND created_at >= ?"
            params.append(iso(since))
        params.append(limit)
        rows = await self._fetchall(
            f"SELECT role, content FROM ai_messages WHERE guild_id = ? AND channel_id = ?{clause} "
            "ORDER BY id DESC LIMIT ?",
            params,
        )
        return [(row["role"], row["content"]) for row in reversed(rows)]

    async def clear_ai_history(self, guild_id: int, channel_id: int | None = None) -> int:
        if channel_id is None:
            cursor = await self._execute("DELETE FROM ai_messages WHERE guild_id = ?", (guild_id,))
        else:
            cursor = await self._execute(
                "DELETE FROM ai_messages WHERE guild_id = ? AND channel_id = ?", (guild_id, channel_id)
            )
        return cursor.rowcount

    async def prune_ai_history(self, older_than_days: int) -> int:
        cutoff = iso(utcnow() - timedelta(days=max(1, older_than_days)))
        cursor = await self._execute("DELETE FROM ai_messages WHERE created_at < ?", (cutoff,))
        return cursor.rowcount

    # ------------------------------------------------------------------- tags
    async def add_tag(self, guild_id: int, name: str, content: str, owner_id: int) -> bool:
        cursor = await self._execute(
            "INSERT INTO tags (guild_id, name, content, owner_id, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, name) DO UPDATE SET content = excluded.content, owner_id = excluded.owner_id",
            (guild_id, name.strip().lower(), content, owner_id, iso()),
        )
        return cursor.rowcount > 0

    async def get_tag(self, guild_id: int, name: str) -> tuple[str, int, int] | None:
        row = await self._fetchone(
            "SELECT content, owner_id, uses FROM tags WHERE guild_id = ? AND name = ?",
            (guild_id, name.strip().lower()),
        )
        if not row:
            return None
        await self._execute(
            "UPDATE tags SET uses = uses + 1 WHERE guild_id = ? AND name = ?",
            (guild_id, name.strip().lower()),
        )
        return row["content"], row["owner_id"], row["uses"] + 1

    async def list_tags(self, guild_id: int) -> list[tuple[str, int]]:
        rows = await self._fetchall(
            "SELECT name, uses FROM tags WHERE guild_id = ? ORDER BY name", (guild_id,)
        )
        return [(row["name"], row["uses"]) for row in rows]

    async def delete_tag(self, guild_id: int, name: str) -> bool:
        cursor = await self._execute(
            "DELETE FROM tags WHERE guild_id = ? AND name = ?", (guild_id, name.strip().lower())
        )
        return cursor.rowcount > 0

    # -------------------------------------------------------------- anti-nuke
    async def antinuke_whitelist(self, guild_id: int) -> set[int]:
        rows = await self._fetchall(
            "SELECT user_id FROM antinuke_whitelist WHERE guild_id = ?", (guild_id,)
        )
        return {row["user_id"] for row in rows}

    async def add_antinuke_whitelist(self, guild_id: int, user_id: int) -> bool:
        cursor = await self._execute(
            "INSERT INTO antinuke_whitelist (guild_id, user_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (guild_id, user_id),
        )
        return cursor.rowcount > 0

    async def remove_antinuke_whitelist(self, guild_id: int, user_id: int) -> bool:
        cursor = await self._execute(
            "DELETE FROM antinuke_whitelist WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        return cursor.rowcount > 0

    async def record_antinuke_event(
        self, guild_id: int, user_id: int, action: str, target_id: int | None = None
    ) -> None:
        await self._execute(
            "INSERT INTO antinuke_events (guild_id, user_id, action, target_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, action, target_id, iso()),
        )

    async def recent_antinuke_events(
        self, guild_id: int, user_id: int, since: datetime
    ) -> list[tuple[str, datetime]]:
        rows = await self._fetchall(
            "SELECT action, created_at FROM antinuke_events WHERE guild_id = ? AND user_id = ? "
            "AND created_at >= ?",
            (guild_id, user_id, iso(since)),
        )
        return [(row["action"], parse_iso(row["created_at"]) or utcnow()) for row in rows]

    async def prune_antinuke_events(self, older_than_days: int = 7) -> int:
        cutoff = iso(utcnow() - timedelta(days=max(1, older_than_days)))
        cursor = await self._execute("DELETE FROM antinuke_events WHERE created_at < ?", (cutoff,))
        return cursor.rowcount

    # ----------------------------------------------------------------- tickets
    async def reserve_ticket_number(self, guild_id: int) -> int:
        """Atomically hand out the next ticket number for a guild."""
        await self.ensure_guild(guild_id)
        async with self._lock:
            await self.conn.execute(
                "UPDATE guilds SET ticket_counter = ticket_counter + 1 WHERE guild_id = ?",
                (guild_id,),
            )
            cursor = await self.conn.execute(
                "SELECT ticket_counter FROM guilds WHERE guild_id = ?", (guild_id,)
            )
            row = await cursor.fetchone()
            await cursor.close()
            await self.conn.commit()
        return int(row["ticket_counter"]) if row else 1

    async def create_ticket(
        self,
        guild_id: int,
        *,
        channel_id: int,
        user_id: int,
        number: int,
        subject: str = "",
    ) -> TicketEntry:
        await self._execute(
            "INSERT INTO tickets (guild_id, channel_id, number, user_id, subject, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'open', ?)",
            (guild_id, channel_id, number, user_id, subject[:200], iso()),
        )
        entry = await self.ticket_for_channel(channel_id)
        assert entry is not None
        return entry

    async def get_ticket(self, ticket_id: int) -> TicketEntry | None:
        row = await self._fetchone("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        return TicketEntry.from_row(row) if row else None

    async def ticket_for_channel(self, channel_id: int) -> TicketEntry | None:
        row = await self._fetchone("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,))
        return TicketEntry.from_row(row) if row else None

    async def open_ticket_for(self, guild_id: int, user_id: int) -> TicketEntry | None:
        row = await self._fetchone(
            "SELECT * FROM tickets WHERE guild_id = ? AND user_id = ? AND status = 'open' "
            "ORDER BY id DESC LIMIT 1",
            (guild_id, user_id),
        )
        return TicketEntry.from_row(row) if row else None

    async def count_open_tickets(self, guild_id: int, user_id: int | None = None) -> int:
        if user_id is None:
            row = await self._fetchone(
                "SELECT COUNT(*) AS c FROM tickets WHERE guild_id = ? AND status = 'open'", (guild_id,)
            )
        else:
            row = await self._fetchone(
                "SELECT COUNT(*) AS c FROM tickets WHERE guild_id = ? AND status = 'open' AND user_id = ?",
                (guild_id, user_id),
            )
        return int(row["c"]) if row else 0

    async def list_tickets(
        self, guild_id: int, *, status: str | None = None, limit: int = 25
    ) -> list[TicketEntry]:
        if status:
            rows = await self._fetchall(
                "SELECT * FROM tickets WHERE guild_id = ? AND status = ? ORDER BY number DESC LIMIT ?",
                (guild_id, status, limit),
            )
        else:
            rows = await self._fetchall(
                "SELECT * FROM tickets WHERE guild_id = ? ORDER BY number DESC LIMIT ?",
                (guild_id, limit),
            )
        return [TicketEntry.from_row(row) for row in rows]

    async def claim_ticket(self, ticket_id: int, user_id: int | None) -> TicketEntry | None:
        await self._execute("UPDATE tickets SET claimed_by = ? WHERE id = ?", (user_id, ticket_id))
        return await self.get_ticket(ticket_id)

    async def close_ticket(
        self, ticket_id: int, *, closed_by: int | None = None, reason: str = ""
    ) -> TicketEntry | None:
        await self._execute(
            "UPDATE tickets SET status = 'closed', closed_at = ?, closed_by = ?, close_reason = ? "
            "WHERE id = ? AND status = 'open'",
            (iso(), closed_by, reason[:500], ticket_id),
        )
        return await self.get_ticket(ticket_id)

    async def ticket_stats(self, guild_id: int) -> dict[str, int]:
        rows = await self._fetchall(
            "SELECT status, COUNT(*) AS c FROM tickets WHERE guild_id = ? GROUP BY status", (guild_id,)
        )
        stats = {"open": 0, "closed": 0, "total": 0}
        for row in rows:
            stats[row["status"]] = int(row["c"])
            stats["total"] += int(row["c"])
        return stats

    # ------------------------------------------------------------------- meta
    async def set_meta(self, key: str, value: str) -> None:
        await self._execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    async def get_meta(self, key: str) -> str | None:
        row = await self._fetchone("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else None
