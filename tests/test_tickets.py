"""Ticket system: database, permissions, transcripts and the open/close flow."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from vantab.database import Database, TicketEntry
from vantab.tickets import (
    TicketError,
    TicketService,
    build_transcript,
    panel_embed,
    sanitize_channel_name,
    summary_lines,
    ticket_channel_name,
    ticket_overwrites,
    transcript_filename,
)


# --------------------------------------------------------------------- fakes
class FakeEntity:
    """Hashable stand-in for Discord members/roles (dict keys in overwrites)."""

    def __init__(self, **attributes) -> None:
        self.__dict__.update(attributes)

    def __hash__(self) -> int:
        return hash(getattr(self, "id", id(self)))

    def __eq__(self, other):  # noqa: D105
        return isinstance(other, FakeEntity) and self.id == other.id


class FakeBot:
    def __init__(self, db: Database, tmp_path) -> None:
        self.db = db
        self.settings = SimpleNamespace(database_path=tmp_path / "vantab.db")
        self.invalidated: list[int] = []
        self.sent: list[tuple[object, object, dict]] = []

    async def guild_settings(self, guild_id: int, *, force: bool = False):
        return await self.db.get_guild(guild_id)

    def invalidate_settings(self, guild_id: int) -> None:
        self.invalidated.append(guild_id)

    async def send_embed(self, channel, embed, **kwargs):
        self.sent.append((channel, embed, kwargs))
        return None


class FakeCategory:
    def __init__(self, category_id: int) -> None:
        self.id = category_id
        self.mention = "<#category>"


class FakeChannel:
    def __init__(self, channel_id: int, *, messages=None) -> None:
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.messages = messages or []
        self.send = AsyncMock()
        self.edit = AsyncMock()
        self.delete = AsyncMock()
        self.set_permissions = AsyncMock()

    async def history(self, **kwargs):
        for message in self.messages:
            yield message


class FakeGuild:
    id = 10
    name = "Test Guild"
    owner_id = 1

    def __init__(self) -> None:
        self.default_role = FakeEntity(id=1000, mention="@everyone")
        self.me = FakeEntity(
            id=999,
            roles=[],
            guild_permissions=SimpleNamespace(
                manage_channels=True, administrator=True, manage_guild=True
            ),
        )
        self.roles: dict[int, SimpleNamespace] = {}
        self.channels: dict[int, object] = {}
        self.members: dict[int, object] = {}
        self.create_text_channel = AsyncMock(side_effect=self._create_channel)
        self.create_category = AsyncMock(side_effect=lambda name, reason=None: FakeCategory(555))
        self._next_id = 700

    async def _create_channel(self, **kwargs):
        channel = FakeChannel(self._next_id)
        self._next_id += 1
        self.channels[channel.id] = channel
        return channel

    def get_role(self, role_id: int):
        return self.roles.get(role_id)

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)

    def get_member(self, member_id: int):
        return self.members.get(member_id)


def _member(member_id: int = 42, *, roles=(), admin=False, manage=False) -> FakeEntity:
    return FakeEntity(
        id=member_id,
        display_name=f"user{member_id}",
        mention=f"<@{member_id}>",
        bot=False,
        roles=[FakeEntity(id=role) for role in roles],
        guild_permissions=SimpleNamespace(
            administrator=admin, manage_channels=manage, manage_guild=False
        ),
    )


def _message(text: str, *, author: str = "Alice", attachments=()) -> SimpleNamespace:
    return SimpleNamespace(
        author=FakeEntity(display_name=author, id=5, bot=False),
        content=text,
        created_at=datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
        attachments=list(attachments),
        embeds=[],
    )


@pytest.fixture()
async def service(tmp_path):
    db = Database(tmp_path / "vantab.db")
    await db.connect()
    bot = FakeBot(db, tmp_path)
    try:
        yield TicketService(bot), db, bot  # type: ignore[arg-type]
    finally:
        await db.close()


# ------------------------------------------------------------------- helpers
def test_sanitize_channel_name() -> None:
    assert sanitize_channel_name("Hello World!") == "hello-world"
    assert sanitize_channel_name("...") == "ticket"
    assert sanitize_channel_name("A" * 200).startswith("a" * 50)


def test_ticket_channel_name() -> None:
    assert ticket_channel_name(7) == "ticket-0007"
    assert ticket_channel_name(7, closed=True) == "closed-0007"
    assert ticket_channel_name(12, custom="Payment issue") == "payment-issue-0012"


def test_transcript_filename() -> None:
    ticket = TicketEntry(
        id=1, guild_id=10, channel_id=5, number=42, user_id=3, subject="",
        status="open", claimed_by=None, created_at=datetime.now(UTC),
        closed_at=None, closed_by=None, close_reason="",
    )
    assert transcript_filename(ticket) == "ticket-0042.html"


def test_ticket_overwrites_are_private() -> None:
    guild = FakeGuild()
    user = _member()
    staff = FakeEntity(id=2000, mention="@staff")
    overwrites = ticket_overwrites(guild, user, staff)  # type: ignore[arg-type]
    assert overwrites[guild.default_role].view_channel is False
    assert overwrites[user].view_channel is True
    assert overwrites[user].send_messages is True
    assert overwrites[staff].view_channel is True
    assert overwrites[guild.me].manage_channels is True


def test_closed_overwrites_block_the_opener() -> None:
    guild = FakeGuild()
    user = _member()
    overwrites = ticket_overwrites(guild, user, None, closed=True)  # type: ignore[arg-type]
    assert overwrites[user].view_channel is True
    assert overwrites[user].send_messages is False
    assert overwrites[user].attach_files is False


def test_panel_embed_mentions_staff() -> None:
    guild = SimpleNamespace(name="Guild")
    embed = panel_embed(guild, staff_role=SimpleNamespace(mention="<@&1>"))  # type: ignore[arg-type]
    assert "Open a ticket" in embed.description
    assert "<@&1>" in embed.description


# ----------------------------------------------------------------- transcript
def test_transcript_escapes_and_lists_attachments() -> None:
    ticket = TicketEntry(
        id=1, guild_id=10, channel_id=5, number=3, user_id=42, subject="Refund <3",
        status="closed", claimed_by=9, created_at=datetime(2026, 1, 1, tzinfo=UTC),
        closed_at=datetime(2026, 1, 2, tzinfo=UTC), closed_by=9, close_reason="solved",
    )
    attachment = SimpleNamespace(filename="proof.png", url="https://cdn.example/x.png")
    document = build_transcript(
        ticket=ticket,
        guild_name="Test <Guild>",
        messages=[_message("<script>alert(1)</script>", attachments=[attachment])],
        closed_by=FakeEntity(display_name="Mod", id=9),
    )
    assert document.startswith("<!DOCTYPE html>")
    assert "Ticket #0003" in document
    assert "Refund &lt;3" in document
    assert "&lt;script&gt;" in document
    assert "<script>alert" not in document
    assert "proof.png" in document
    assert "closed by Mod" in document
    assert "solved" in document


def test_transcript_without_messages() -> None:
    ticket = TicketEntry(
        id=1, guild_id=10, channel_id=5, number=1, user_id=42, subject="",
        status="open", claimed_by=None, created_at=datetime.now(UTC),
        closed_at=None, closed_by=None, close_reason="",
    )
    document = build_transcript(ticket=ticket, guild_name="G", messages=[])
    assert "No messages were exchanged." in document
    assert "No subject" in document


# -------------------------------------------------------------------- service
def test_staff_detection() -> None:
    settings = SimpleNamespace(ticket_staff_role_id=77)
    assert TicketService.is_staff(_member(admin=True), settings) is True
    assert TicketService.is_staff(_member(roles=(77,)), settings) is True
    assert TicketService.is_staff(_member(roles=(1,)), settings) is False


def test_can_close_rules() -> None:
    settings = SimpleNamespace(ticket_staff_role_id=77)
    ticket = SimpleNamespace(user_id=42)
    assert TicketService.can_close(_member(42), ticket, settings) is True
    assert TicketService.can_close(_member(43, roles=(77,)), ticket, settings) is True
    assert TicketService.can_close(_member(43), ticket, settings) is False


async def test_open_ticket_creates_channel_and_record(service) -> None:
    svc, db, bot = service
    guild = FakeGuild()
    user = _member()
    guild.channels[4242] = FakeChannel(4242)
    await db.update_guild(guild.id, ticket_log_channel=4242)

    ticket, channel = await svc.open_ticket(guild, user, subject="Help me")  # type: ignore[arg-type]

    assert ticket.number == 1
    assert ticket.status == "open"
    assert ticket.subject == "Help me"
    assert channel.id == ticket.channel_id
    guild.create_text_channel.assert_awaited_once()
    kwargs = guild.create_text_channel.await_args.kwargs
    assert kwargs["name"] == "ticket-0001"
    assert "Help me" in kwargs["topic"]
    # A category is auto-provisioned and remembered.
    settings = await db.get_guild(guild.id)
    assert settings.ticket_category_id == 555
    assert bot.invalidated == [guild.id]
    # Welcome message + log entry.
    assert channel.send.await_count == 1
    assert bot.sent, "the open event should be logged"


async def test_open_ticket_rejects_duplicates(service) -> None:
    svc, _db, _bot = service
    guild = FakeGuild()
    user = _member()
    await svc.open_ticket(guild, user, subject="first")  # type: ignore[arg-type]
    with pytest.raises(TicketError, match="already have an open ticket"):
        await svc.open_ticket(guild, user, subject="second")  # type: ignore[arg-type]


async def test_open_ticket_respects_disabled_flag(service) -> None:
    svc, db, _bot = service
    guild = FakeGuild()
    await db.update_guild(guild.id, tickets_enabled=False)
    with pytest.raises(TicketError, match="disabled"):
        await svc.open_ticket(guild, _member(), subject="hi")  # type: ignore[arg-type]


async def test_close_ticket_archives_and_locks(service, tmp_path) -> None:
    svc, db, bot = service
    guild = FakeGuild()
    log_channel = FakeChannel(4242)
    guild.channels[4242] = log_channel
    await db.update_guild(guild.id, ticket_log_channel=4242, ticket_staff_role_id=None)

    ticket, channel = await svc.open_ticket(guild, _member(), subject="broken")  # type: ignore[arg-type]
    channel.messages = [_message("hello"), _message("fixed?", author="Mod")]

    closed = await svc.close_ticket(ticket, guild, closed_by=_member(7), reason="done")  # type: ignore[arg-type]

    assert closed.status == "closed"
    assert closed.closed_by == 7
    assert closed.close_reason == "done"
    # Relabelled and locked.
    assert channel.edit.await_count == 1
    assert channel.edit.await_args.kwargs["name"] == "closed-0001"
    # Transcript written to disk and delivered to the log channel.
    path = tmp_path / "transcripts" / f"guild-{guild.id}" / "ticket-0001.html"
    assert path.exists()
    assert "hello" in path.read_text(encoding="utf-8")
    assert bot.sent, "closing should write to the log channel"


async def test_close_twice_is_refused(service) -> None:
    svc, _db, _bot = service
    guild = FakeGuild()
    ticket, _channel = await svc.open_ticket(guild, _member(), subject="x")  # type: ignore[arg-type]
    guild.channels[ticket.channel_id] = FakeChannel(ticket.channel_id)
    await svc.close_ticket(ticket, guild, closed_by=_member(7))  # type: ignore[arg-type]
    fresh = await _db.get_ticket(ticket.id)
    with pytest.raises(TicketError, match="already closed"):
        await svc.close_ticket(fresh, guild, closed_by=_member(7))  # type: ignore[arg-type]


async def test_claim_and_unclaim(service) -> None:
    svc, _db, _bot = service
    guild = FakeGuild()
    ticket, _channel = await svc.open_ticket(guild, _member(), subject="x")  # type: ignore[arg-type]
    staff = _member(90, admin=True)

    claimed = await svc.claim(ticket, staff)  # type: ignore[arg-type]
    assert claimed.claimed_by == 90

    released = await svc.unclaim(claimed, staff)  # type: ignore[arg-type]
    assert released.claimed_by is None


async def test_summary_lines() -> None:
    ticket = TicketEntry(
        id=1, guild_id=10, channel_id=5, number=7, user_id=42, subject="Hi",
        status="open", claimed_by=9, created_at=datetime.now(UTC),
        closed_at=None, closed_by=None, close_reason="",
    )
    lines = summary_lines([ticket])
    assert "#0007" in lines[0]
    assert "claimed by <@9>" in lines[0]


async def test_claim_buttons_resolve_the_ticket(service) -> None:
    svc, db, _bot = service
    guild = FakeGuild()
    ticket, _channel = await svc.open_ticket(guild, _member(), subject="x")  # type: ignore[arg-type]
    found = await db.ticket_for_channel(ticket.channel_id)
    assert found is not None and found.id == ticket.id
    assert await db.count_open_tickets(guild.id) == 1
    assert await db.count_open_tickets(guild.id, ticket.user_id) == 1
    assert await db.count_open_tickets(guild.id, 999) == 0


def test_ticket_numbers_are_per_guild() -> None:
    import asyncio

    async def scenario() -> None:
        db = Database("/tmp/vantab-ticket-numbers.db")
        await db.connect()
        try:
            assert await db.reserve_ticket_number(1) == 1
            assert await db.reserve_ticket_number(1) == 2
            assert await db.reserve_ticket_number(2) == 1
        finally:
            await db.close()
            import contextlib
            import pathlib

            with contextlib.suppress(OSError):
                for suffix in ("", "-wal", "-shm"):
                    pathlib.Path(f"/tmp/vantab-ticket-numbers.db{suffix}").unlink()

    asyncio.run(scenario())


def test_permission_overwrite_accepts_objects() -> None:
    guild = FakeGuild()
    overwrites = ticket_overwrites(guild, _member(), None)  # type: ignore[arg-type]
    assert all(isinstance(value, discord.PermissionOverwrite) for value in overwrites.values())


def test_persistent_view_custom_ids_are_unique() -> None:
    from vantab.ui.tickets import TicketControlsView, TicketPanelView

    ids = [child.custom_id for child in TicketPanelView(None).children]  # type: ignore[arg-type]
    ids += [child.custom_id for child in TicketControlsView(None).children]  # type: ignore[arg-type]
    assert len(ids) == len(set(ids))
    assert all(custom_id.startswith("vantab:ticket:") for custom_id in ids)
    assert len(ids) == 5


async def test_ticket_status_panel_replies(bot, tmp_path) -> None:
    """The cog must answer ``~ticket`` (catches unwired helpers)."""
    bot.db.path = tmp_path / "cog.db"
    await bot.db.connect()
    try:
        cog = bot.get_cog("Tickets")
        assert cog is not None
        reply = AsyncMock()
        ctx = SimpleNamespace(
            guild=SimpleNamespace(
                id=1,
                owner_id=1,
                get_channel=lambda channel_id: None,
                get_role=lambda role_id: None,
            ),
            author=_member(1),
            channel=SimpleNamespace(id=2),
            interaction=None,
            reply=reply,
        )
        await cog._status(ctx)  # type: ignore[attr-defined]
        assert reply.await_count == 1
        embed = reply.await_args.kwargs["embed"]
        assert "Tickets are" in embed.description
        assert any(field.name == "Max per member" for field in embed.fields)
    finally:
        await bot.db.close()
