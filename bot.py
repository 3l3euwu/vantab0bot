#!/usr/bin/env python3
"""Vantab entry point.

    python bot.py            # run the bot
    python bot.py --check    # validate the token / credentials, do not connect
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

import aiohttp
import discord

from vantab.bot import Vantab
from vantab.config import Settings, get_settings

log = logging.getLogger("vantab")

API = "https://discord.com/api/v10"

#: Gateway close codes that mean "fix your configuration", with a human hint.
CLOSE_HINTS: dict[int, str] = {
    4004: "Invalid token — copy it again from the portal (Bot → Reset Token) and paste it in tkn.txt.",
    4008: "Too many IDENTIFYs (rate limited by Discord) — wait ~15 minutes, then restart.",
    4009: "Session timed out — just restart the bot.",
    4010: "Invalid shard — the shard/shard_count values are wrong.",
    4011: "The bot is in too many servers — sharding is required.",
    4013: "Invalid intents — unknown intent bits were requested.",
    4014: "Privileged intents not enabled — enable Server Members + Message Content.",
}


def main() -> int:
    try:
        settings = get_settings()
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1

    discord.utils.setup_logging(level=settings.log_level, root=True)
    logging.getLogger("discord.http").setLevel(logging.WARNING)

    if "--check" in sys.argv[1:] or "--doctor" in sys.argv[1:]:
        return asyncio.run(_check(settings))

    bot = Vantab(settings)
    try:
        bot.run(settings.token, log_handler=None)
    except discord.LoginFailure:
        log.critical("Discord rejected the token — check DISCORD_TOKEN / tkn.txt.")
        return 1
    except discord.PrivilegedIntentsRequired:
        log.critical(
            "The Message Content and Server Members intents must be enabled in the "
            "Discord developer portal for this application."
        )
        return 1
    except discord.ConnectionClosed as exc:
        hint = CLOSE_HINTS.get(exc.code, "Discord closed the gateway connection.")
        log.critical("Gateway closed with code %s (%s). %s", exc.code, exc.reason or "no reason", hint)
        return 1
    except discord.GatewayNotFound:
        log.critical("Discord's gateway is unreachable — check your internet / proxy, or try again later.")
        return 1
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down.")
    return 0


async def _check(settings: Settings) -> int:
    """Validate the token and credentials without opening a gateway connection."""
    headers = {"Authorization": f"Bot {settings.token}", "User-Agent": "Vantab/1.0 (+check)"}
    try:
        return await _check_http(settings, headers)
    except (TimeoutError, aiohttp.ClientError, OSError) as exc:
        print(f"x Could not reach discord.com ({exc.__class__.__name__}: {exc}).")
        print("  Check your internet connection, VPN/proxy or firewall, then retry.")
        return 1


async def _check_http(settings: Settings, headers: dict[str, str]) -> int:
    ok = True
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as http:
        async with http.get(f"{API}/users/@me") as resp:
            body = await resp.text()
            if resp.status == 401:
                print("x Discord rejected the token (401). Regenerate it in the portal and update tkn.txt.")
                return 1
            if resp.status != 200:
                print(f"x HTTP {resp.status} while checking the token: {body[:200]}")
                return 1
            try:
                me = json.loads(body)
            except ValueError:
                print(f"x Unexpected reply from Discord: {body[:200]}")
                return 1
            kind = "bot" if me.get("bot") else "NOT a bot account"
            print(f"ok Token valid: {me.get('username')}#{me.get('discriminator', '0')} "
                  f"(id {me.get('id')}, {kind})")

        async with http.get(f"{API}/users/@me/guilds") as resp:
            if resp.status == 200:
                guilds = await resp.json()
                names = ", ".join(g["name"] for g in guilds[:8])
                more = "" if len(guilds) <= 8 else f" (+{len(guilds) - 8})"
                print(f"   Servers ({len(guilds)}): {names}{more}" if guilds else "   Servers: none — invite it!")
            else:
                print(f"   Could not list servers (HTTP {resp.status}).")

        async with http.get(f"{API}/gateway/bot") as resp:
            if resp.status == 200:
                info = await resp.json()
                limit = info.get("session_start_limit", {})
                remaining, total = limit.get("remaining"), limit.get("total")
                if remaining is not None and remaining <= 5:
                    print(f"! Only {remaining}/{total} gateway sessions left — Discord is rate limiting "
                          "IDENTIFY. Wait ~15 minutes before starting the bot, and make sure no other "
                          "copy of this bot is running.")
                    ok = False
                else:
                    print(f"   Gateway sessions left: {remaining}/{total}, shards: {info.get('shards')}")
            else:
                print(f"   Could not read gateway info (HTTP {resp.status}).")

    print(f"   provider={settings.ai_provider} model={settings.ai_model} "
          f"api-key={'set' if settings.ai_api_key else 'MISSING'} "
          f"credentials={settings.credentials_file or 'environment'}")
    if not settings.ai_api_key:
        print("   (the AI will not answer without api-key= in tkn.txt)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
