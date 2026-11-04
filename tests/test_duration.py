"""Duration parsing and formatting."""

from __future__ import annotations

from datetime import timedelta

import pytest

from vantab.utils.duration import (
    DurationError,
    format_duration,
    format_seconds,
    humanize,
    parse_duration,
)


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [
        ("30s", 30),
        ("10m", 600),
        ("2h", 7200),
        ("1d", 86400),
        ("1w", 604800),
        ("1h30m", 5400),
        ("2 days", 172800),
        ("45", 2700),  # bare number defaults to minutes
        ("90 seconds", 90),
        ("1mo", 2592000),
        ("0", 0),
        ("off", 0),
        ("", None),
    ],
)
def test_parse_duration(raw: str, seconds: int | None) -> None:
    if seconds is None:
        with pytest.raises(DurationError):
            parse_duration(raw)
        return
    assert parse_duration(raw).total_seconds() == seconds


def test_parse_duration_default_unit_seconds() -> None:
    assert parse_duration("90", default_unit="s") == timedelta(seconds=90)


def test_parse_duration_accepts_numeric() -> None:
    assert parse_duration(3600, default_unit="s") == timedelta(hours=1)
    assert parse_duration(timedelta(days=2)) == timedelta(days=2)


@pytest.mark.parametrize("raw", ["abc", "10 lightyears", "10x", "-5m", "999999d", "1h30"])
def test_parse_duration_rejects(raw: str) -> None:
    with pytest.raises(DurationError):
        parse_duration(raw)


def test_format_roundtrip() -> None:
    assert format_duration(timedelta(seconds=5400)) == "1h 30m"
    assert format_duration(timedelta(seconds=0)) == "0s"
    assert format_seconds(3661) == "1h 1m"
    assert format_seconds(59) == "59s"
    assert humanize(timedelta(days=1, hours=3)) == "1 day, 3 hours"
    assert humanize(timedelta(seconds=0)) == "0 seconds"
    assert humanize(timedelta(minutes=1)) == "1 minute"
