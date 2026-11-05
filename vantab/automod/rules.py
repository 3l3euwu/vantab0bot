"""Message-level detection rules.

Everything in this module is pure and synchronous: give it a :class:`MessageContext`
and it returns detections. That makes the whole automod trivially unit-testable
without a Discord connection.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from ..utils.text import (
    INVITE_RE,
    URL_RE,
    count_caps_ratio,
    domain_of,
    emoji_count,
    extract_urls,
    looks_like_phishing,
    luhn_valid,
    normalize,
    zalgo_ratio,
)

__all__ = [
    "MessageContext",
    "Detection",
    "RuleSpec",
    "RULES",
    "SEVERITY",
    "ViolationTracker",
    "evaluate",
    "max_action",
    "bump_action",
    "rule_config_dict",
    "DANGEROUS_EXTENSIONS",
]

SEVERITY: dict[str, int] = {"none": 0, "delete": 1, "warn": 2, "timeout": 3, "kick": 4, "ban": 5}
ACTION_ORDER = ["none", "delete", "warn", "timeout", "kick", "ban"]

SPAM_WINDOW = timedelta(seconds=8)
DUPLICATE_WINDOW = timedelta(seconds=45)
CROSS_CHANNEL_WINDOW = timedelta(seconds=25)

DANGEROUS_EXTENSIONS = (
    ".exe", ".scr", ".bat", ".cmd", ".com", ".pif", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".ps1", ".msi", ".jar", ".hta", ".cpl", ".lnk", ".reg", ".apk",
)


@dataclass(slots=True)
class MessageContext:
    """A snapshot of everything the rules need from a Discord message."""

    guild_id: int
    channel_id: int
    author_id: int
    content: str
    created_at: datetime
    mention_count: int = 0
    role_mention_count: int = 0
    everyone_mention: bool = False
    attachment_count: int = 0
    attachment_names: tuple[str, ...] = ()
    embed_count: int = 0
    sticker_count: int = 0
    author_is_bot: bool = False
    author_is_webhook: bool = False
    account_age: timedelta | None = None
    is_reply: bool = False
    channel_nsfw: bool = False

    @property
    def full_text(self) -> str:
        """Content plus attachment filenames, for link/extension scanning."""
        if self.attachment_names:
            return f"{self.content} {' '.join(self.attachment_names)}"
        return self.content


@dataclass(slots=True)
class Detection:
    rule: str
    reason: str
    evidence: str = ""
    severity_hint: int = 0

    def as_log(self) -> str:
        return f"{self.rule}: {self.reason}" + (f" ({self.evidence})" if self.evidence else "")


@dataclass(frozen=True, slots=True)
class RuleSpec:
    name: str
    label: str
    description: str
    category: str
    default_action: str = "delete"
    default_duration: int = 0
    default_threshold: int = 0
    threshold_hint: str = ""
    enabled_by_default: bool = True


RULES: dict[str, RuleSpec] = {
    spec.name: spec
    for spec in (
        RuleSpec(
            "spam", "Message spam", "Fires when a member sends too many messages in a few seconds.",
            "Rate", "timeout", 300, 6, "messages per 8s",
        ),
        RuleSpec(
            "duplicate", "Duplicate spam", "Repeated identical messages in a short window.",
            "Rate", "timeout", 300, 3, "identical messages per 45s",
        ),
        RuleSpec(
            "crosschannel", "Cross-channel spam", "Same message posted in several channels at once.",
            "Rate", "timeout", 900, 3, "distinct channels per 25s",
        ),
        RuleSpec(
            "mention_spam", "Mass mentions", "Too many user mentions in a single message.",
            "Mentions", "timeout", 900, 5, "mentions per message",
        ),
        RuleSpec(
            "everyone", "Ghost ping @everyone", "Unauthorised @everyone / @here mentions.",
            "Mentions", "timeout", 900, 1, "@everyone mentions",
        ),
        RuleSpec(
            "invites", "Discord invites", "Blocks discord.gg invite links.",
            "Links", "delete",
        ),
        RuleSpec(
            "links", "All links", "Blocks every link that is not on the allowlist.",
            "Links", "delete", enabled_by_default=False,
        ),
        RuleSpec(
            "phishing", "Phishing links", "Known scam/look-alike domains (nitro, steam, ip-loggers…).",
            "Safety", "ban",
        ),
        RuleSpec(
            "scam", "Scam phrases", "Classic scam wording (free nitro, double your crypto…).",
            "Safety", "timeout", 3600,
        ),
        RuleSpec(
            "badwords", "Banned words", "Your per-server banned word list (supports * wildcards).",
            "Content", "delete",
        ),
        RuleSpec(
            "caps", "Excessive caps", "MESSAGES SCREAMED IN ALL CAPS.",
            "Content", "delete", threshold_hint="percent of capital letters",
        ),
        RuleSpec(
            "zalgo", "Zalgo/glitch text", "Unicode abuse that breaks the chat.",
            "Content", "delete",
        ),
        RuleSpec(
            "emoji", "Emoji spam", "Way too many emojis in one message.",
            "Content", "delete", threshold_hint="emojis per message",
        ),
        RuleSpec(
            "newlines", "Wall of newlines", "Messages padded with huge line breaks.",
            "Content", "delete", threshold_hint="line breaks",
        ),
        RuleSpec(
            "attachments", "Attachment flood", "Multiple or executable attachments.",
            "Attachments", "delete", threshold_hint="attachments per message",
        ),
        RuleSpec(
            "sensitive", "Leaked secrets", "Credit cards, private keys and tokens are deleted to protect users.",
            "Safety", "delete",
        ),
    )
}


def rule_config_dict(rule: Any) -> dict[str, Any]:
    """Turn a RuleConfig (slotted dataclass) into a plain dict for ``evaluate``."""
    return {
        "enabled": bool(getattr(rule, "enabled", True)),
        "action": getattr(rule, "action", "delete"),
        "duration": int(getattr(rule, "duration", 0) or 0),
        "threshold": int(getattr(rule, "threshold", 0) or 0),
    }


def max_action(*actions: str) -> str:
    best = "none"
    for action in actions:
        if SEVERITY.get(action, 0) > SEVERITY.get(best, 0):
            best = action
    return best


def bump_action(action: str, steps: int = 1) -> str:
    """Return the action ``steps`` levels harsher, clamped at ``ban``."""
    try:
        index = ACTION_ORDER.index(action)
    except ValueError:
        index = 0
    return ACTION_ORDER[min(len(ACTION_ORDER) - 1, index + max(0, steps))]


# ---------------------------------------------------------------------------
# Stateful tracking (spam windows, repeat offenders)
# ---------------------------------------------------------------------------
class ViolationTracker:
    """In-memory sliding windows used by the rate-based rules and escalation."""

    def __init__(self, *, max_users: int = 20000) -> None:
        self.max_users = max_users
        self._messages: dict[tuple[int, int], deque[tuple[datetime, str]]] = defaultdict(deque)
        self._channels: dict[tuple[int, int], deque[tuple[datetime, int, str]]] = defaultdict(deque)
        self._strikes: dict[tuple[int, int], deque[datetime]] = defaultdict(deque)

    # -- messages
    def record_message(self, ctx: MessageContext) -> None:
        key = (ctx.guild_id, ctx.author_id)
        bucket = self._messages[key]
        bucket.append((ctx.created_at, normalize(ctx.content)))
        self._trim(bucket, ctx.created_at - timedelta(minutes=2))
        channel_bucket = self._channels[key]
        channel_bucket.append((ctx.created_at, ctx.channel_id, normalize(ctx.content)))
        self._trim(channel_bucket, ctx.created_at - timedelta(minutes=2))
        if len(self._messages) > self.max_users:
            self._messages.pop(next(iter(self._messages)))

    def message_count(self, ctx: MessageContext, window: timedelta = SPAM_WINDOW) -> int:
        cutoff = ctx.created_at - window
        bucket = self._messages.get((ctx.guild_id, ctx.author_id), ())
        return sum(1 for when, _ in bucket if when >= cutoff)

    def duplicate_count(self, ctx: MessageContext, window: timedelta = DUPLICATE_WINDOW) -> int:
        target = normalize(ctx.content)
        if len(target) < 4:
            return 0
        cutoff = ctx.created_at - window
        bucket = self._messages.get((ctx.guild_id, ctx.author_id), ())
        return sum(1 for when, text in bucket if when >= cutoff and text == target)

    def cross_channel_count(
        self, ctx: MessageContext, window: timedelta = CROSS_CHANNEL_WINDOW
    ) -> int:
        target = normalize(ctx.content)
        if len(target) < 4:
            return 0
        cutoff = ctx.created_at - window
        bucket = self._channels.get((ctx.guild_id, ctx.author_id), ())
        channels = {cid for when, cid, text in bucket if when >= cutoff and text == target}
        return len(channels)

    # -- strikes
    def add_strike(self, guild_id: int, user_id: int, when: datetime | None = None) -> int:
        now = when or datetime.now(UTC)
        bucket = self._strikes[(guild_id, user_id)]
        bucket.append(now)
        cutoff = now - timedelta(hours=1)
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        return len(bucket)

    def strike_count(self, guild_id: int, user_id: int, when: datetime | None = None) -> int:
        now = when or datetime.now(UTC)
        bucket = self._strikes.get((guild_id, user_id), ())
        return sum(1 for ts in bucket if ts >= now - timedelta(hours=1))

    def clear(self, guild_id: int | None = None, user_id: int | None = None) -> None:
        if guild_id is None:
            self._messages.clear()
            self._channels.clear()
            self._strikes.clear()
            return
        for store in (self._messages, self._channels, self._strikes):
            for key in [k for k in store if k[0] == guild_id and (user_id is None or k[1] == user_id)]:
                store.pop(key, None)

    def prune(self, now: datetime | None = None) -> None:
        """Drop stale buckets so a long-running process does not leak memory."""
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(minutes=5)
        for store in (self._messages, self._channels):
            for key in [k for k, bucket in store.items() if not bucket or bucket[-1][0] < cutoff]:
                store.pop(key, None)
        strike_cutoff = now - timedelta(hours=1)
        for key in [k for k, bucket in self._strikes.items() if not bucket or bucket[-1] < strike_cutoff]:
            self._strikes.pop(key, None)

    @staticmethod
    def _trim(bucket: deque[Any], cutoff: datetime) -> None:
        while bucket and bucket[0][0] < cutoff:
            bucket.popleft()


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})

_SCAM_PATTERNS = (
    re.compile(r"\bfree\s+(?:discord\s+)?nitro\b", re.IGNORECASE),
    re.compile(r"\bnitro\s+(?:gift|giveaway|drop|generator)\b", re.IGNORECASE),
    re.compile(r"\bdouble\s+your\s+(?:btc|eth|crypto|money|balance)\b", re.IGNORECASE),
    re.compile(r"\b(?:seed|recovery)\s+phrase\b", re.IGNORECASE),
    re.compile(r"\bconnect\s+(?:your\s+)?wallet\b", re.IGNORECASE),
    re.compile(r"\bclaim\s+(?:your\s+)?(?:reward|airdrop|gift|prize)\b", re.IGNORECASE),
    re.compile(r"\bsteam\s+(?:gift|giveaway)\b", re.IGNORECASE),
    re.compile(r"\byou(?:'ve|\s+have)?\s+won\b", re.IGNORECASE),
    re.compile(r"\bcrypto\s+(?:airdrop|giveaway)\b", re.IGNORECASE),
    re.compile(r"\bfree\s+robux\b", re.IGNORECASE),
    re.compile(r"\btoken\s+grab(?:ber)?\b", re.IGNORECASE),
)

_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")
_DISCORD_TOKEN_RE = re.compile(r"(?:mfa\.[\w-]{80,}|[\w-]{23,28}\.[\w-]{6}\.[\w-]{25,40})")
_CARD_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


@lru_cache(maxsize=1024)
def _badword_pattern(word: str) -> re.Pattern[str]:
    word = word.lower()
    escaped = re.escape(word).replace(r"\*", ".*")
    if "*" in word:
        # Wildcards are intentional partial matches: "bad*" also matches "superbadwords".
        return re.compile(escaped, re.IGNORECASE)
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def _badword_hit(content: str, badwords: Iterable[str]) -> tuple[str, str] | None:
    raw = content.lower()
    deleet = raw.translate(_LEET)
    deleet_norm = normalize(deleet)
    for word in badwords:
        if not word:
            continue
        pattern = _badword_pattern(word)
        for haystack in (raw, normalize(raw), deleet, deleet_norm):
            match = pattern.search(haystack)
            if match:
                return word, match.group(0)
    return None


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _snippet(text: str, limit: int = 120) -> str:
    snippet = re.sub(r"\s+", " ", text or "").strip()
    return snippet[:limit] + ("…" if len(snippet) > limit else "")


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------
RuleFn = Any


def _rule_spam(ctx: MessageContext, cfg: Mapping[str, Any], tracker: ViolationTracker) -> Detection | None:
    threshold = int(cfg.get("threshold") or 6)
    count = tracker.message_count(ctx)
    if count > threshold:
        return Detection(
            "spam",
            f"{count} messages in {int(SPAM_WINDOW.total_seconds())}s (limit {threshold})",
            _snippet(ctx.content, 60),
        )
    return None


def _rule_duplicate(ctx, cfg, tracker) -> Detection | None:
    threshold = int(cfg.get("threshold") or 3)
    count = tracker.duplicate_count(ctx)
    if count >= threshold:
        return Detection("duplicate", f"same message sent {count}x in 45s", _snippet(ctx.content, 60))
    return None


def _rule_crosschannel(ctx, cfg, tracker) -> Detection | None:
    threshold = int(cfg.get("threshold") or 3)
    count = tracker.cross_channel_count(ctx)
    if count >= threshold:
        return Detection("crosschannel", f"same message in {count} channels in 25s", _snippet(ctx.content, 60))
    return None


def _rule_mention_spam(ctx, cfg, tracker) -> Detection | None:
    threshold = int(cfg.get("threshold") or 5)
    total = ctx.mention_count + ctx.role_mention_count
    if total >= threshold:
        return Detection("mention_spam", f"{total} mentions in one message (limit {threshold})")
    return None


def _rule_everyone(ctx, cfg, tracker) -> Detection | None:
    if ctx.everyone_mention:
        return Detection("everyone", "unauthorised @everyone/@here mention")
    return None


def _rule_invites(ctx, cfg, tracker) -> Detection | None:
    text = ctx.full_text
    compact = _compact(text)
    if INVITE_RE.search(text) or INVITE_RE.search(compact):
        return Detection("invites", "Discord invite link", _snippet(text, 80))
    return None


def _rule_links(ctx, cfg, tracker) -> Detection | None:
    allowlist = set(cfg.get("allowlist") or ())
    urls = extract_urls(ctx.full_text)
    if not urls:
        return None
    blocked = [url for url in urls if not _domain_allowed(domain_of(url), allowlist)]
    if blocked:
        return Detection("links", "link not on the allowlist", _snippet(blocked[0], 80))
    return None


def _domain_allowed(host: str, allowlist: Iterable[str]) -> bool:
    if not host:
        return False
    for allowed in allowlist:
        allowed = allowed.lower().lstrip(".")
        if host == allowed or host.endswith(f".{allowed}"):
            return True
    return False


def _rule_phishing(ctx, cfg, tracker) -> Detection | None:
    urls = extract_urls(ctx.full_text)
    for url in urls:
        if looks_like_phishing(url):
            return Detection("phishing", "known scam / look-alike domain", _snippet(url, 80))
    return None


def _rule_scam(ctx, cfg, tracker) -> Detection | None:
    text = ctx.content
    has_link = bool(URL_RE.search(text))
    for pattern in _SCAM_PATTERNS:
        match = pattern.search(text)
        if match and (has_link or pattern.pattern.startswith(r"\b(?:seed|recovery)")):
            return Detection("scam", "classic scam wording", _snippet(match.group(0), 60))
    return None


def _rule_badwords(ctx, cfg, tracker) -> Detection | None:
    badwords = cfg.get("badwords") or ()
    if not badwords:
        return None
    hit = _badword_hit(ctx.content, badwords)
    if hit:
        word, matched = hit
        return Detection("badwords", f"banned word `{word}`", _snippet(matched, 60))
    return None


def _rule_caps(ctx, cfg, tracker) -> Detection | None:
    threshold = int(cfg.get("threshold") or 70)
    ratio, letters = count_caps_ratio(ctx.content)
    if letters >= 12 and ratio * 100 >= threshold:
        return Detection("caps", f"{ratio * 100:.0f}% capital letters", _snippet(ctx.content, 60))
    return None


def _rule_zalgo(ctx, cfg, tracker) -> Detection | None:
    ratio = zalgo_ratio(ctx.content)
    if ratio > 0.25 and len(ctx.content) > 8:
        return Detection("zalgo", f"{ratio * 100:.0f}% combining characters")
    return None


def _rule_emoji(ctx, cfg, tracker) -> Detection | None:
    threshold = int(cfg.get("threshold") or 12)
    count = emoji_count(ctx.content)
    if count >= threshold:
        return Detection("emoji", f"{count} emojis in one message", _snippet(ctx.content, 60))
    return None


def _rule_newlines(ctx, cfg, tracker) -> Detection | None:
    threshold = int(cfg.get("threshold") or 20)
    breaks = ctx.content.count("\n")
    if breaks > threshold:
        return Detection("newlines", f"{breaks} line breaks in one message")
    return None


def _rule_attachments(ctx, cfg, tracker) -> Detection | None:
    threshold = int(cfg.get("threshold") or 5)
    dangerous = [name for name in ctx.attachment_names if name.lower().endswith(DANGEROUS_EXTENSIONS)]
    if dangerous:
        return Detection("attachments", "executable attachment", _snippet(dangerous[0], 60))
    if ctx.attachment_count >= threshold:
        return Detection("attachments", f"{ctx.attachment_count} attachments in one message")
    return None


def _rule_sensitive(ctx, cfg, tracker) -> Detection | None:
    text = ctx.content
    if _PRIVATE_KEY_RE.search(text):
        return Detection("sensitive", "private key posted")
    if _DISCORD_TOKEN_RE.search(text):
        return Detection("sensitive", "possible Discord token posted")
    for candidate in _CARD_CANDIDATE_RE.findall(text):
        if luhn_valid(candidate):
            return Detection("sensitive", "possible credit card number")
    return None


_DETECTORS: dict[str, Any] = {
    "spam": _rule_spam,
    "duplicate": _rule_duplicate,
    "crosschannel": _rule_crosschannel,
    "mention_spam": _rule_mention_spam,
    "everyone": _rule_everyone,
    "invites": _rule_invites,
    "links": _rule_links,
    "phishing": _rule_phishing,
    "scam": _rule_scam,
    "badwords": _rule_badwords,
    "caps": _rule_caps,
    "zalgo": _rule_zalgo,
    "emoji": _rule_emoji,
    "newlines": _rule_newlines,
    "attachments": _rule_attachments,
    "sensitive": _rule_sensitive,
}


def evaluate(
    ctx: MessageContext,
    *,
    rules: Mapping[str, Mapping[str, Any]],
    badwords: Iterable[str] = (),
    allowlist: Iterable[str] = (),
    tracker: ViolationTracker | None = None,
) -> list[Detection]:
    """Run every enabled rule and return all detections (strongest first).

    ``rules`` maps rule name -> ``{"enabled", "action", "duration", "threshold"}``.
    """
    tracker = tracker or ViolationTracker()
    ctx_tracker = tracker
    # The tracker is only fed once per message; safe because evaluate is called once per message.
    ctx_tracker.record_message(ctx)

    detections: list[Detection] = []
    for name, spec in RULES.items():
        cfg = dict(rules.get(name) or {})
        if not cfg:
            cfg = {
                "enabled": spec.enabled_by_default,
                "action": spec.default_action,
                "duration": spec.default_duration,
                "threshold": spec.default_threshold,
            }
        if not cfg.get("enabled", spec.enabled_by_default):
            continue
        cfg.setdefault("threshold", spec.default_threshold)
        cfg.setdefault("action", spec.default_action)
        cfg.setdefault("duration", spec.default_duration)
        cfg["badwords"] = tuple(badwords)
        cfg["allowlist"] = tuple(allowlist)
        detector = _DETECTORS.get(name)
        if detector is None:
            continue
        try:
            detection = detector(ctx, cfg, ctx_tracker)
        except Exception:  # pragma: no cover - a broken rule must never kill the bot
            continue
        if detection is not None:
            detections.append(detection)

    detections.sort(key=lambda d: SEVERITY.get((rules.get(d.rule) or {}).get("action", "delete"), 1), reverse=True)
    return detections
