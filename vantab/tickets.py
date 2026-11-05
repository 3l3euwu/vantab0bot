"""Ticket domain logic: private channels, permissions and transcripts.

The cog in :mod:`vantab.cogs.tickets` only parses commands; everything that
talks to Discord lives here so it can be unit-tested with fakes.
"""

from __future__ import annotations

import contextlib
import html
import logging
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import discord

from .database import GuildSettings, TicketEntry, utcnow
from .utils.embeds import Brand, base_embed

if TYPE_CHECKING:  # pragma: no cover
    from .bot import Vantab

__all__ = [
    "TicketError",
    "TicketService",
    "build_transcript",
    "panel_embed",
    "sanitize_channel_name",
    "ticket_channel_name",
    "ticket_overwrites",
    "transcript_filename",
]

log = logging.getLogger("vantab.tickets")

MAX_SUBJECT = 200
MAX_CHANNEL_NAME = 90
TICKET_TOPIC_LIMIT = 1024


class TicketError(Exception):
    """A user-facing reason a ticket action cannot proceed."""


# --------------------------------------------------------------------- helpers
def sanitize_channel_name(text: str, *, fallback: str = "ticket") -> str:
    """Turn arbitrary text into something Discord accepts as a channel name."""
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in text.strip().lower())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:MAX_CHANNEL_NAME].strip("-_") or fallback


def ticket_channel_name(
    number: int, *, closed: bool = False, custom: str | None = None
) -> str:
    """``ticket-0007`` (or ``closed-0007`` / a custom stem)."""
    stem = "closed" if closed else sanitize_channel_name(custom or "ticket")
    return f"{stem}-{number:04d}"[:100]


def staff_overwrite() -> discord.PermissionOverwrite:
    """Permissions handed to the staff role inside a ticket channel."""
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        manage_messages=True,
        attach_files=True,
        embed_links=True,
    )


def ticket_overwrites(
    guild: discord.Guild,
    user: discord.abc.User,
    staff_role: discord.Role | None,
    *,
    closed: bool = False,
) -> dict[Any, discord.PermissionOverwrite]:
    """Permissions for a ticket channel: private, with staff access."""
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=not closed,
            read_message_history=True,
            attach_files=not closed,
            embed_links=not closed,
            add_reactions=not closed,
        ),
    }
    me = getattr(guild, "me", None)
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True,
            attach_files=True,
            embed_links=True,
        )
    if staff_role is not None:
        overwrites[staff_role] = staff_overwrite()
    return overwrites


def panel_embed(guild: discord.Guild | None = None, *, staff_role: discord.Role | None = None) -> discord.Embed:
    """The public "open a ticket" panel."""
    server = f"**{guild.name}**" if guild is not None else "this server"
    lines = [
        f"Need a hand in {server}?",
        "",
        "Click **Open a ticket** below and describe your issue.",
        "A private channel is created for you and the staff team.",
        "Closing a ticket archives a full transcript for the staff.",
        "",
        "One ticket at a time, please — never share passwords or 2FA codes.",
    ]
    if staff_role is not None:
        lines.append(f"\nStaff on duty: {staff_role.mention}")
    embed = base_embed("\n".join(lines), title="Support tickets", colour=Brand.PRIMARY)
    return embed


def as_text_channel(channel: Any) -> Any | None:
    """Return ``channel`` when it can behave like a text channel.

    Real guilds hand back :class:`discord.TextChannel`; tests (and eventual
    partial objects) only need ``history``/``edit``/``send``.
    """
    if isinstance(channel, discord.TextChannel):
        return channel
    if channel is not None and all(
        hasattr(channel, attribute) for attribute in ("history", "edit", "send")
    ):
        return channel
    return None


def transcript_filename(ticket: TicketEntry) -> str:
    return f"ticket-{ticket.number:04d}.html"


def _format_attachments(message: Any) -> str:
    attachments = list(getattr(message, "attachments", []) or [])
    if not attachments:
        return ""
    items = []
    for attachment in attachments:
        name = html.escape(str(getattr(attachment, "filename", "attachment")))
        url = html.escape(str(getattr(attachment, "url", "")), quote=True)
        items.append(f'<a href="{url}" target="_blank" rel="noreferrer">{name}</a>')
    return '<div class="attachments">📎 ' + " · ".join(items) + "</div>"


def build_transcript(
    *,
    ticket: TicketEntry,
    guild_name: str,
    messages: Iterable[Any],
    closed_by: Any | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Render a self-contained HTML transcript of a ticket channel."""
    stamp = (generated_at or utcnow()).strftime("%Y-%m-%d %H:%M UTC")
    rows: list[str] = []
    count = 0
    for message in messages:
        count += 1
        author = getattr(message, "author", None)
        name = str(getattr(author, "display_name", None) or getattr(author, "name", "unknown"))
        author_id = getattr(author, "id", "")
        bot_tag = ' <span class="bot">BOT</span>' if getattr(author, "bot", False) else ""
        created = getattr(message, "created_at", None)
        when = created.strftime("%Y-%m-%d %H:%M") if isinstance(created, datetime) else ""
        content = html.escape(str(getattr(message, "content", "") or "")).replace("\n", "<br>")
        if not content:
            content = '<span class="muted">(no text)</span>'
        embeds = len(getattr(message, "embeds", []) or [])
        embed_note = f'<div class="muted">[embed × {embeds}]</div>' if embeds else ""
        rows.append(
            '<div class="message">'
            f'<div class="head"><span class="author">{html.escape(name)}</span>'
            f'<span class="meta">{html.escape(str(author_id))} · {when}</span>{bot_tag}</div>'
            f'<div class="body">{content}</div>'
            f"{_format_attachments(message)}{embed_note}"
            "</div>"
        )
    if not rows:
        rows.append('<p class="muted">No messages were exchanged.</p>')

    closer = ""
    if closed_by is not None:
        closer_name = str(getattr(closed_by, "display_name", None) or getattr(closed_by, "name", "staff"))
        closer = f" · closed by {html.escape(closer_name)}"
    reason = f' · reason: {html.escape(ticket.close_reason)}' if ticket.close_reason else ""
    subject = html.escape(ticket.subject or "No subject")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ticket #{ticket.number:04d} — {html.escape(guild_name)}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 32px 16px; background: #1e1f22; color: #dbdee1;
         font: 15px/1.5 "gg sans", "Segoe UI", system-ui, sans-serif; }}
  main {{ max-width: 900px; margin: 0 auto; }}
  header {{ border-left: 4px solid #7c5cff; padding: 4px 0 4px 16px; margin-bottom: 28px; }}
  h1 {{ margin: 0 0 6px; font-size: 22px; color: #fff; }}
  .sub {{ color: #949ba4; font-size: 13px; }}
  .message {{ background: #2b2d31; border-radius: 8px; padding: 12px 14px; margin-bottom: 10px; }}
  .head {{ display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; margin-bottom: 4px; }}
  .author {{ font-weight: 600; color: #f2f3f5; }}
  .meta {{ color: #949ba4; font-size: 12px; }}
  .bot {{ background: #5865f2; color: #fff; font-size: 10px; padding: 1px 5px; border-radius: 4px; }}
  .body {{ white-space: normal; overflow-wrap: anywhere; }}
  .attachments {{ margin-top: 6px; font-size: 13px; }}
  .attachments a {{ color: #a5b4fc; }}
  .muted {{ color: #949ba4; font-size: 12px; }}
  footer {{ margin-top: 28px; color: #949ba4; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<main>
  <header>
    <h1>🎫 Ticket #{ticket.number:04d} — {subject}</h1>
    <div class="sub">
      {html.escape(guild_name)} · opened by {ticket.user_id} ·
      {ticket.created_at.strftime("%Y-%m-%d %H:%M UTC")} · {count} message(s){closer}{reason}
    </div>
  </header>
  {"".join(rows)}
  <footer>Generated by Vantab on {stamp}</footer>
</main>
</body>
</html>
"""


# --------------------------------------------------------------------- service
class TicketService:
    """Creates, archives and reports on tickets for one bot instance."""

    def __init__(self, bot: Vantab) -> None:
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    # ----------------------------------------------------------------- config
    async def settings(self, guild_id: int) -> GuildSettings:
        return await self.bot.guild_settings(guild_id)

    @staticmethod
    def is_staff(member: discord.Member, settings: GuildSettings) -> bool:
        perms = member.guild_permissions
        if perms.administrator or perms.manage_channels or perms.manage_guild:
            return True
        if settings.ticket_staff_role_id:
            return any(role.id == settings.ticket_staff_role_id for role in member.roles)
        return False

    @staticmethod
    def can_close(member: discord.Member, ticket: TicketEntry, settings: GuildSettings) -> bool:
        if member.id == ticket.user_id:
            return True
        return TicketService.is_staff(member, settings)

    def transcript_dir(self, guild_id: int) -> Path:
        root = Path(self.bot.settings.database_path).parent / "transcripts"
        return root / f"guild-{guild_id}"

    # ------------------------------------------------------------------- open
    async def open_ticket(
        self,
        guild: discord.Guild,
        user: discord.abc.User,
        *,
        subject: str = "",
        details: str = "",
    ) -> tuple[TicketEntry, discord.TextChannel]:
        settings = await self.settings(guild.id)
        if not settings.tickets_enabled:
            raise TicketError("Tickets are disabled on this server.")
        existing = await self.db.open_ticket_for(guild.id, user.id)
        if existing is not None:
            channel = guild.get_channel(existing.channel_id)
            where = channel.mention if channel is not None else f"<#{existing.channel_id}>"
            raise TicketError(f"You already have an open ticket: {where}")
        maximum = max(1, settings.ticket_max_open)
        if await self.db.count_open_tickets(guild.id, user.id) >= maximum:
            raise TicketError(f"You already have {maximum} open ticket(s) — close one first.")

        me = getattr(guild, "me", None)
        if me is not None and not me.guild_permissions.manage_channels:
            raise TicketError("I need the **Manage Channels** permission to open tickets.")

        category = await self._category(guild, settings)
        staff_role = guild.get_role(settings.ticket_staff_role_id) if settings.ticket_staff_role_id else None
        number = await self.db.reserve_ticket_number(guild.id)
        topic = f"Ticket #{number:04d} · {user} ({user.id})"
        if subject:
            topic += f" · {subject}"
        try:
            channel = await guild.create_text_channel(
                name=ticket_channel_name(number),
                category=category,
                topic=topic[:TICKET_TOPIC_LIMIT],
                overwrites=ticket_overwrites(guild, user, staff_role),
                reason=f"Ticket #{number:04d} opened by {user}",
            )
        except discord.HTTPException as exc:
            raise TicketError(f"Discord refused to create the channel (`{exc.status}`).") from exc

        try:
            ticket = await self.db.create_ticket(
                guild.id,
                channel_id=channel.id,
                user_id=user.id,
                number=number,
                subject=subject,
            )
        except Exception:
            with contextlib.suppress(discord.HTTPException):
                await channel.delete(reason="Ticket record could not be saved")
            raise

        await self._welcome(guild, channel, ticket, user, staff_role, details)
        await self._log(
            guild,
            settings,
            base_embed(
                f"Ticket {channel.mention} opened by {user.mention} (`{user.id}`).\n"
                f"**Subject:** {subject or '—'}",
                title=f"Ticket #{ticket.number:04d} opened",
                colour=Brand.SUCCESS,
            ),
        )
        log.info("Ticket #%04d opened in guild %s by %s", ticket.number, guild.id, user.id)
        return ticket, channel

    async def _category(
        self, guild: discord.Guild, settings: GuildSettings
    ) -> discord.CategoryChannel | None:
        if settings.ticket_category_id:
            existing = guild.get_channel(settings.ticket_category_id)
            if isinstance(existing, discord.CategoryChannel):
                return existing
        try:
            category = await guild.create_category("Tickets", reason="Vantab ticket system")
        except discord.HTTPException as exc:
            raise TicketError("I need **Manage Channels** to create the Tickets category.") from exc
        await self.db.update_guild(guild.id, ticket_category_id=category.id)
        self.bot.invalidate_settings(guild.id)
        return category

    async def _welcome(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        ticket: TicketEntry,
        user: discord.abc.User,
        staff_role: discord.Role | None,
        details: str,
    ) -> None:
        from .ui.tickets import TicketControlsView

        embed = base_embed(
            f"{user.mention}, thanks for opening a ticket — staff will be with you shortly.\n\n"
            f"**Subject:** {ticket.subject or '—'}\n"
            f"**Ticket:** #{ticket.number:04d}\n\n"
            "Use the buttons below to claim, close or archive this ticket.",
            title="Ticket opened",
            colour=Brand.PRIMARY,
        )
        if details:
            embed.add_field(name="Details", value=details[:1024], inline=False)
        try:
            await channel.send(
                content=staff_role.mention if staff_role is not None else None,
                embed=embed,
                view=TicketControlsView(self.bot),
                allowed_mentions=discord.AllowedMentions(roles=True, users=True),
            )
        except discord.HTTPException:  # pragma: no cover - permissions race
            log.debug("Could not post the welcome message in %s", channel.id)

    # ------------------------------------------------------------------ close
    async def close_ticket(
        self,
        ticket: TicketEntry,
        guild: discord.Guild,
        *,
        closed_by: discord.abc.User,
        reason: str = "",
    ) -> TicketEntry:
        if not ticket.is_open:
            raise TicketError("This ticket is already closed.")
        settings = await self.settings(guild.id)
        channel = as_text_channel(guild.get_channel(ticket.channel_id))
        path: Path | None = None
        if channel is not None:
            path = await self.export(guild, ticket, channel)

        closed = await self.db.close_ticket(ticket.id, closed_by=closed_by.id, reason=reason)
        assert closed is not None

        if channel is not None:
            opener = guild.get_member(closed.user_id) or discord.Object(id=closed.user_id)
            staff_role = (
                guild.get_role(settings.ticket_staff_role_id) if settings.ticket_staff_role_id else None
            )
            with contextlib.suppress(discord.HTTPException):
                await channel.edit(
                    name=ticket_channel_name(closed.number, closed=True),
                    overwrites=ticket_overwrites(guild, opener, staff_role, closed=True),
                    reason=f"Ticket #{closed.number:04d} closed by {closed_by}",
                )
            await self._closed_notice(channel, closed, closed_by)
            if path is not None:
                await self.deliver(guild, closed, path, fallback_channel=channel)

        await self._log(
            guild,
            settings,
            base_embed(
                f"Ticket **#{closed.number:04d}** closed by {closed_by.mention} (`{closed_by.id}`).\n"
                f"**Opened by:** <@{closed.user_id}>\n"
                f"**Reason:** {reason or '—'}",
                title="Ticket closed",
                colour=Brand.WARNING,
            ),
        )
        log.info("Ticket #%04d closed by %s", closed.number, closed_by.id)
        return closed

    async def _closed_notice(
        self, channel: discord.TextChannel, ticket: TicketEntry, closed_by: discord.abc.User
    ) -> None:
        from .ui.tickets import TicketControlsView

        try:
            await channel.send(
                embed=base_embed(
                    f"This ticket was closed by {closed_by.mention}.\n"
                    f"**Reason:** {ticket.close_reason or '—'}\n\n"
                    "The conversation is now read-only for the opener. Staff can delete the "
                    "channel once the transcript has been archived.",
                    title="Ticket closed",
                    colour=Brand.MUTED,
                ),
                view=TicketControlsView(self.bot),
            )
        except discord.HTTPException:  # pragma: no cover
            log.debug("Could not announce the closure in %s", channel.id)

    # -------------------------------------------------------------- transcripts
    async def export(
        self, guild: discord.Guild, ticket: TicketEntry, channel: discord.TextChannel
    ) -> Path:
        """Fetch the whole channel and write an HTML transcript to disk."""
        messages: list[Any] = []
        async for message in channel.history(limit=None, oldest_first=True):
            messages.append(message)
        document = build_transcript(ticket=ticket, guild_name=guild.name, messages=messages)
        path = self.transcript_dir(guild.id) / transcript_filename(ticket)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document, encoding="utf-8")
        return path

    async def deliver(
        self,
        guild: discord.Guild,
        ticket: TicketEntry,
        path: Path,
        *,
        fallback_channel: discord.TextChannel | None = None,
    ) -> None:
        """Send the transcript to the log channel and to the ticket opener."""
        settings = await self.settings(guild.id)
        channel_id = settings.ticket_log_channel or settings.mod_log_channel
        channel = guild.get_channel(channel_id) if channel_id else None
        file = discord.File(path, filename=path.name)
        embed = base_embed(
            f"Transcript for ticket **#{ticket.number:04d}** (opened by <@{ticket.user_id}>).",
            title="Ticket transcript",
            colour=Brand.INFO,
        )
        if channel is not None:
            await self.bot.send_embed(channel, embed, file=file)
        opener = guild.get_member(ticket.user_id)
        if opener is not None:
            try:
                await opener.send(
                    embed=base_embed(
                        f"Here is the transcript of your ticket **#{ticket.number:04d}**.",
                        title="Your ticket transcript",
                        colour=Brand.INFO,
                    ),
                    file=discord.File(path, filename=path.name),
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
        elif fallback_channel is not None:  # pragma: no cover - opener left
            log.debug("Opener %s left; transcript kept on disk at %s", ticket.user_id, path)

    # -------------------------------------------------------------- moderation
    async def claim(self, ticket: TicketEntry, member: discord.Member) -> TicketEntry:
        if not ticket.is_open:
            raise TicketError("This ticket is already closed.")
        if ticket.claimed_by == member.id:
            raise TicketError("You already claimed this ticket.")
        updated = await self.db.claim_ticket(ticket.id, member.id)
        assert updated is not None
        return updated

    async def unclaim(self, ticket: TicketEntry, member: discord.Member) -> TicketEntry:
        if ticket.claimed_by is None:
            raise TicketError("This ticket is not claimed.")
        if ticket.claimed_by != member.id and not member.guild_permissions.manage_channels:
            raise TicketError("Only the person who claimed it can release this ticket.")
        updated = await self.db.claim_ticket(ticket.id, None)
        assert updated is not None
        return updated

    async def add_member(
        self, channel: discord.TextChannel, member: discord.Member
    ) -> None:
        try:
            await channel.set_permissions(
                member,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                reason="Added to ticket",
            )
        except discord.HTTPException as exc:
            raise TicketError(f"Could not add {member.mention} (`{exc.status}`).") from exc

    async def remove_member(
        self, channel: discord.TextChannel, member: discord.Member, *, opener_id: int
    ) -> None:
        if member.id == opener_id:
            raise TicketError("The ticket opener cannot be removed.")
        try:
            await channel.set_permissions(member, overwrite=None, reason="Removed from ticket")
        except discord.HTTPException as exc:
            raise TicketError(f"Could not remove {member.mention} (`{exc.status}`).") from exc

    async def rename(self, channel: discord.TextChannel, name: str) -> str:
        cleaned = sanitize_channel_name(name)
        try:
            await channel.edit(name=cleaned, reason="Ticket renamed")
        except discord.HTTPException as exc:
            raise TicketError(f"Could not rename the channel (`{exc.status}`).") from exc
        return cleaned

    async def log_embed(self, guild: discord.Guild, embed: discord.Embed) -> None:
        settings = await self.settings(guild.id)
        await self._log(guild, settings, embed)

    async def _log(
        self, guild: discord.Guild, settings: GuildSettings, embed: discord.Embed
    ) -> None:
        channel_id = settings.ticket_log_channel or settings.mod_log_channel
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
        await self.bot.send_embed(channel, embed)


def summary_lines(tickets: Sequence[TicketEntry]) -> list[str]:
    """Compact one-line summaries used by ``~ticket list``."""
    lines = []
    for ticket in tickets:
        state = {"open": "🟢", "closed": "🔒"}.get(ticket.status, "•")
        claimed = f" · claimed by <@{ticket.claimed_by}>" if ticket.claimed_by else ""
        lines.append(
            f"{state} **#{ticket.number:04d}** <#{ticket.channel_id}> · <@{ticket.user_id}>"
            f"{claimed} · {ticket.subject[:60] or '—'}"
        )
    return lines
