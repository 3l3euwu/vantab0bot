"""Text helpers used across the bot."""

from __future__ import annotations

from vantab.utils.text import (
    chunk_text,
    count_caps_ratio,
    domain_of,
    emoji_count,
    escape_markdown,
    extract_urls,
    looks_like_phishing,
    luhn_valid,
    normalize,
    plural,
    strip_markdown,
    truncate,
    zalgo_ratio,
)


def test_truncate() -> None:
    assert truncate("hello", 10) == "hello"
    assert truncate("hello world", 8) == "hello w…"
    assert truncate("abc", 0) == ""


def test_chunk_text_respects_limit() -> None:
    text = "\n\n".join(["paragraph " * 50] * 10)
    chunks = chunk_text(text, 500)
    assert chunks
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") .replace(" ", "") == text.replace("\n", "").replace(" ", "")


def test_chunk_text_hard_split() -> None:
    chunks = chunk_text("x" * 250, 100)
    assert [len(c) for c in chunks] == [100, 100, 50]


def test_chunk_text_empty() -> None:
    assert chunk_text("", 100) == []


def test_normalize() -> None:
    assert normalize("HELLO,   World!!") == "hello world"
    assert normalize("check https://x.com/a") == "check url"
    assert normalize("hey <@123> you") == "hey mention you"
    assert normalize("zero\u200bwidth") == "zerowidth"


def test_urls_and_domains() -> None:
    text = "look at https://www.Example.com/path and http://sub.test.io"
    urls = extract_urls(text)
    assert urls == ["https://www.Example.com/path", "http://sub.test.io"]
    assert domain_of(urls[0]) == "example.com"
    assert domain_of("discord.gg/abc") == "discord.gg"
    assert domain_of("not a url") == ""


def test_phishing_detection() -> None:
    assert looks_like_phishing("https://discord-nitro.com/gift")
    assert looks_like_phishing("http://discord.com.ru/free")
    assert looks_like_phishing("https://xn--discord-9ja.com")
    assert looks_like_phishing("http://192.168.0.1/login")
    assert looks_like_phishing("https://steamcommunlty.com/gift")
    assert not looks_like_phishing("https://discord.com/channels/1/2")
    assert not looks_like_phishing("https://github.com/3l3euwu/vantab0bot")
    assert not looks_like_phishing("https://youtube.com/watch?v=1")


def test_luhn() -> None:
    assert luhn_valid("4242 4242 4242 4242")
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("1234 5678 9012 3456")
    assert not luhn_valid("123")


def test_caps_and_zalgo() -> None:
    ratio, letters = count_caps_ratio("THIS IS LOUD")
    assert letters == 10
    assert ratio == 1.0
    ratio, letters = count_caps_ratio("https://EXAMPLE.COM is fine")
    assert ratio < 1.0
    assert count_caps_ratio("12345") == (0.0, 0)
    assert zalgo_ratio("normal text") == 0.0
    assert zalgo_ratio("h̸̢̛e̷̢̛l̸̢̛l̷̢̛ơ̸̢") > 0.3


def test_emoji_count() -> None:
    assert emoji_count("hello 😀😀") == 2
    assert emoji_count("<:party:123456789012345678>") == 1
    assert emoji_count("no emojis") == 0


def test_markdown_helpers() -> None:
    assert strip_markdown("**bold** _italic_ `code`") == "bold italic code"
    assert escape_markdown("*not bold*") == r"\*not bold\*"


def test_plural() -> None:
    assert plural(1, "warning") == "1 warning"
    assert plural(3, "warning") == "3 warnings"
    assert plural(2, "child", "children") == "2 children"
