"""Human friendly duration parsing and formatting."""

from __future__ import annotations

import re
from datetime import timedelta

__all__ = [
    "DurationError",
    "parse_duration",
    "format_duration",
    "format_seconds",
    "humanize",
    "MAX_DURATION",
]

MAX_DURATION = timedelta(days=365)

_UNITS: dict[str, float] = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "w": 604800,
    "week": 604800,
    "weeks": 604800,
    "mo": 2592000,
    "month": 2592000,
    "months": 2592000,
    "y": 31536000,
    "year": 31536000,
    "years": 31536000,
}

_TOKEN_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z]+)")
_BARE_RE = re.compile(r"^\d+(?:\.\d+)?$")
_DEFAULT_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class DurationError(ValueError):
    """Raised when a duration string cannot be understood."""


def parse_duration(value: str | int | float | timedelta, *, default_unit: str = "m") -> timedelta:
    """Parse ``"1h30m"``, ``"2 days"``, ``"45"`` (bare = ``default_unit``) into a timedelta.

    Raises :class:`DurationError` for nonsense input so commands can reply with a
    friendly message instead of a traceback.
    """
    if isinstance(value, timedelta):
        result = value
    elif isinstance(value, (int, float)):
        result = timedelta(seconds=float(value) * _DEFAULT_UNIT_SECONDS.get(default_unit, 60))
    else:
        text = str(value).strip().lower().replace(",", "").replace(" ", "")
        if not text:
            raise DurationError("empty duration")
        if text in {"0", "off", "none", "never", "permanent", "perm"}:
            return timedelta(0)
        if _BARE_RE.match(text):
            result = timedelta(seconds=float(text) * _DEFAULT_UNIT_SECONDS.get(default_unit, 60))
        else:
            total = 0.0
            matched = 0
            for match in _TOKEN_RE.finditer(text):
                unit = match.group("unit")
                if unit not in _UNITS:
                    raise DurationError(f"unknown time unit: {unit!r}")
                total += float(match.group("num")) * _UNITS[unit]
                matched += len(match.group(0))
            # Reject strings with unparsed leftovers (e.g. "10minuteslol" or "abc").
            consumed = "".join(m.group(0) for m in _TOKEN_RE.finditer(text))
            if not matched or consumed != text:
                raise DurationError(f"could not parse duration: {value!r}")
            result = timedelta(seconds=total)

    if result < timedelta(0):
        raise DurationError("duration cannot be negative")
    if result > MAX_DURATION:
        raise DurationError("duration is too long (max 365 days)")
    return result


def format_duration(delta: timedelta) -> str:
    """Compact human representation, e.g. ``1h 30m``."""
    seconds = int(round(delta.total_seconds()))
    return format_seconds(seconds)


def format_seconds(seconds: float | int) -> str:
    seconds = int(round(seconds))
    if seconds <= 0:
        return "0s"
    parts: list[str] = []
    for label, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            amount, seconds = divmod(seconds, size)
            parts.append(f"{amount}{label}")
        if len(parts) == 2:
            break
    return " ".join(parts)


def humanize(delta: timedelta) -> str:
    """Long form used in embeds: ``1 day, 3 hours``."""
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "0 seconds"
    chunks = [
        ("day", 86400),
        ("hour", 3600),
        ("minute", 60),
        ("second", 1),
    ]
    pieces: list[str] = []
    for name, size in chunks:
        if seconds >= size:
            amount, seconds = divmod(seconds, size)
            pieces.append(f"{amount} {name}{'s' if amount != 1 else ''}")
        if len(pieces) == 2:
            break
    return ", ".join(pieces)
