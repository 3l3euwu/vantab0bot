"""Small text helpers shared by the automod, AI and utility cogs."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

__all__ = [
    "URL_RE",
    "INVITE_RE",
    "DISCORD_INVITE_CODES",
    "chunk_text",
    "truncate",
    "normalize",
    "extract_urls",
    "domain_of",
    "looks_like_phishing",
    "strip_markdown",
    "escape_markdown",
    "luhn_valid",
    "count_caps_ratio",
    "zalgo_ratio",
    "emoji_count",
    "plural",
]

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
#: discord.gg/xxx, discord.com/invite/xxx, discordapp.com/invite/xxx, discord.me/xxx
INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.(?:gg|com/invite|me)|discord\.gg)/[\w-]+",
    re.IGNORECASE,
)
DISCORD_INVITE_CODES = re.compile(r"(?:discord\.gg/|discord(?:app)?\.com/invite/)[\w-]+", re.IGNORECASE)

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


def truncate(text: str, limit: int = 1900, suffix: str = "…") -> str:
    """Trim ``text`` to at most ``limit`` characters, appending ``suffix``."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def chunk_text(text: str, limit: int = 1900) -> list[str]:
    """Split a long message on natural boundaries so Discord accepts it.

    Tries paragraphs, then newlines, then spaces, then a hard cut. Never returns
    an empty list for non-empty input.
    """
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[:limit]
        split_at = window.rfind("\n\n")
        if split_at < limit * 0.5:
            split_at = window.rfind("\n")
        if split_at < limit * 0.5:
            split_at = window.rfind(" ")
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n")
    return [c for c in chunks if c]


def normalize(text: str) -> str:
    """Normalise text for duplicate detection."""
    text = (text or "").translate(_ZERO_WIDTH).casefold()
    text = re.sub(r"https?://\S+", " <url> ", text)
    text = re.sub(r"<@!?\d+>|<@&\d+>", " <mention> ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_urls(text: str) -> list[str]:
    return URL_RE.findall(text or "")


def domain_of(url: str) -> str:
    """Return the lower-cased registrable-ish domain of a URL."""
    candidate = url if "://" in url else f"http://{url}"
    try:
        host = (urlparse(candidate).hostname or "").lower().strip(".")
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    # Reject garbage that urlparse happily accepts (spaces, no TLD, …).
    if not re.fullmatch(r"[a-z0-9._\-:\[\]]+", host):
        return ""
    if "." not in host and host != "localhost":
        return ""
    return host


_SUSPICIOUS_TLDS = (".zip", ".mov", ".top", ".xyz", ".click", ".gq", ".tk", ".ml", ".cf", ".ga", ".work", ".kim", ".loan")
_SUSPICIOUS_HOST_PARTS = (
    "discord-nitro",
    "discordnitro",
    "discord-gift",
    "discordgift",
    "nitro-gift",
    "nitrogift",
    "free-nitro",
    "freenitro",
    "dlscord",
    "discrod",
    "discordapp.gift",
    "steamcommunlty",
    "steamcomrnunity",
    "steamcommunity.gift",
    "steamcommunity.ru",
    "airdrop",
    "claim-reward",
    "crypto-gift",
    "csgo-skins",
    "skin-gift",
    "token-grab",
    "grabber",
    "roblox-gift",
    "robuxgenerator",
    "free-robux",
    "nitro-boost",
    "boost-gift",
)
_IP_HOST_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def looks_like_phishing(url: str) -> bool:
    """Heuristic (no network calls) phishing / scam link detector."""
    host = domain_of(url)
    if not host:
        return False
    if host.startswith("xn--") or ".xn--" in host:
        return True
    if _IP_HOST_RE.match(host):
        return True
    if any(part in host for part in _SUSPICIOUS_HOST_PARTS):
        return True
    if host.endswith(_SUSPICIOUS_TLDS) and "discord" in host:
        return True
    # Look-alike domains: discord.<something> that is not actually discord.com
    lookalike = ("discord" in host or "steam" in host) and not (
        host.endswith("discord.com")
        or host.endswith("discord.gg")
        or host.endswith("discordapp.com")
        or host.endswith("discord.gift")
        or host.endswith("discordapp.net")
        or host.endswith("steampowered.com")
        or host.endswith("steamcommunity.com")
    )
    return bool(lookalike)


_MD_RE = re.compile(r"[*_~`>|]")


def strip_markdown(text: str) -> str:
    return _MD_RE.sub("", text or "")


def escape_markdown(text: str) -> str:
    return re.sub(r"([*_~`>|\\])", r"\\\1", text or "")


def luhn_valid(number: str) -> bool:
    """Luhn checksum, used to avoid flagging random digit strings as cards."""
    digits = [int(d) for d in re.sub(r"\D", "", number or "")]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def count_caps_ratio(text: str) -> tuple[float, int]:
    """Return (uppercase ratio, letter count) ignoring URLs and mentions."""
    cleaned = URL_RE.sub("", text or "")
    cleaned = re.sub(r"<[@#!&:][^>]*>", "", cleaned)
    letters = [c for c in cleaned if c.isalpha()]
    if not letters:
        return 0.0, 0
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters), len(letters)


def zalgo_ratio(text: str) -> float:
    if not text:
        return 0.0
    combining = 0
    for char in text:
        if unicodedata.combining(char) or unicodedata.category(char) in {"Mn", "Me"}:
            combining += 1
    return combining / max(1, len(text))


_EMOJI_RE = re.compile(
    "[" "\U0001f300-\U0001faff" "\U00002700-\U000027bf" "\U0001f000-\U0001f2ff" "\U00002600-\U000026ff" "]+",
    flags=re.UNICODE,
)
_CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")


def emoji_count(text: str) -> int:
    if not text:
        return 0
    custom = len(_CUSTOM_EMOJI_RE.findall(text))
    plain = sum(len(match.group(0)) for match in _EMOJI_RE.finditer(_CUSTOM_EMOJI_RE.sub("", text)))
    return custom + plain


def plural(amount: int, singular: str, plural_form: str | None = None) -> str:
    if amount == 1:
        return f"{amount} {singular}"
    return f"{amount} {plural_form or singular + 's'}"
