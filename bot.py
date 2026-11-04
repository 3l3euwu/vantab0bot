#!/usr/bin/env python3
"""Vantab entry point.

    python bot.py
"""

from __future__ import annotations

import logging
import sys

import discord

from vantab.bot import Vantab
from vantab.config import get_settings

log = logging.getLogger("vantab")


def main() -> int:
    try:
        settings = get_settings()
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1

    discord.utils.setup_logging(level=settings.log_level, root=True)
    logging.getLogger("discord.http").setLevel(logging.WARNING)

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
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
