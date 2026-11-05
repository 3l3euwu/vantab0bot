"""Channel locks: threads must be blocked too, special roles included."""

from __future__ import annotations

from types import SimpleNamespace

import discord

from vantab.cogs.moderation import LockOutcome, Moderation
from vantab.utils.locks import LOCK_PERMISSIONS, apply_lock, is_locked, snapshot_lock


class FakeRole:
    def __init__(
        self,
        role_id: int,
        *,
        name: str = "role",
        administrator: bool = False,
        managed: bool = False,
        default: bool = False,
    ) -> None:
        self.id = role_id
        self.name = name
        self.managed = managed
        self._default = default
        self.permissions = SimpleNamespace(administrator=administrator)
        self.mention = f"<@&{role_id}>"

    def is_default(self) -> bool:
        return self._default

    def __hash__(self) -> int:  # noqa: D105
        return hash(self.id)

    def __eq__(self, other) -> bool:  # noqa: D105
        return isinstance(other, FakeRole) and other.id == self.id


class FakeThread:
    def __init__(self, thread_id: int, *, locked: bool = False) -> None:
        self.id = thread_id
        self.locked = locked
        self.edits = 0

    async def edit(self, *, locked: bool, reason: str | None = None) -> None:
        self.locked = locked
        self.edits += 1


class FakeMember:
    def __init__(self, member_id: int) -> None:
        self.id = member_id
        self.mention = f"<@{member_id}>"


class FakeGuild:
    def __init__(self) -> None:
        self.id = 10
        self.name = "Guild"
        self._everyone = FakeRole(10, name="@everyone", default=True)
        self.default_role = self._everyone
        self.roles: list[FakeRole] = [self._everyone]
        self.me = FakeMember(999)

    def add_role(self, role_id: int, **kwargs) -> FakeRole:
        role = FakeRole(role_id, **kwargs)
        self.roles.append(role)
        return role

    def get_role(self, role_id: int) -> FakeRole | None:
        return next((role for role in self.roles if role.id == role_id), None)


class FakeChannel:
    def __init__(self, guild: FakeGuild, *, channel_id: int = 1, threads=()) -> None:
        self.guild = guild
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self.threads = list(threads)
        self._overwrites: dict[int, discord.PermissionOverwrite] = {}
        self.applied: list[int] = []

    def overwrites_for(self, target) -> discord.PermissionOverwrite:
        key = getattr(target, "id", id(target))
        return self._overwrites.setdefault(key, discord.PermissionOverwrite())

    async def set_permissions(self, target, *, overwrite, reason: str | None = None) -> None:
        key = getattr(target, "id", id(target))
        if overwrite is None:
            self._overwrites.pop(key, None)
        else:
            self._overwrites[key] = overwrite
        self.applied.append(key)

    def allow(self, target, **permissions) -> None:
        overwrite = self.overwrites_for(target)
        for name, value in permissions.items():
            setattr(overwrite, name, value)


def _scene(**kwargs):
    guild = FakeGuild()
    thread = FakeThread(500, locked=False)
    channel = FakeChannel(guild, threads=[thread] if kwargs.get("with_thread", True) else [])
    return guild, channel, thread


async def test_lock_denies_messages_and_threads_for_everyone() -> None:
    guild, channel, thread = _scene()
    cog = Moderation(None)  # type: ignore[arg-type]

    outcome = await cog._set_lock(channel, True, reason="test")

    overwrite = channel.overwrites_for(guild.default_role)
    for permission in LOCK_PERMISSIONS:
        assert getattr(overwrite, permission) is False
    assert outcome.threads == 1
    assert thread.locked is True
    assert isinstance(outcome, LockOutcome)


async def test_lock_covers_roles_allowed_in_the_channel() -> None:
    guild, channel, _thread = _scene()
    staff = guild.add_role(20, name="Staff")
    channel.allow(staff, send_messages=True)
    cog = Moderation(None)  # type: ignore[arg-type]

    outcome = await cog._set_lock(channel, True, reason="test")

    assert staff in outcome.roles
    overwrite = channel.overwrites_for(staff)
    assert all(getattr(overwrite, permission) is False for permission in LOCK_PERMISSIONS)


async def test_lock_covers_roles_that_only_allowed_threads() -> None:
    """A role allowed to create threads can bypass a plain send_messages lock."""
    guild, channel, _thread = _scene()
    role = guild.add_role(21, name="Threaders")
    channel.allow(role, create_public_threads=True)
    cog = Moderation(None)  # type: ignore[arg-type]

    outcome = await cog._set_lock(channel, True, reason="test")

    assert role in outcome.roles
    overwrite = channel.overwrites_for(role)
    assert overwrite.create_public_threads is False
    assert overwrite.send_messages is False


async def test_managed_roles_are_left_alone() -> None:
    guild, channel, _thread = _scene()
    integration = guild.add_role(22, name="Integration", managed=True)
    channel.allow(integration, send_messages=True)
    cog = Moderation(None)  # type: ignore[arg-type]

    await cog._set_lock(channel, True, reason="test")

    assert channel.overwrites_for(integration).send_messages is True


async def test_administrator_roles_are_reported() -> None:
    guild, channel, _thread = _scene()
    admin = guild.add_role(23, name="Admins", administrator=True)
    cog = Moderation(None)  # type: ignore[arg-type]

    outcome = await cog._set_lock(channel, True, reason="test")

    assert admin in outcome.admins


async def test_unlock_restores_previous_permissions() -> None:
    guild, channel, thread = _scene()
    staff = guild.add_role(24, name="Staff")
    channel.allow(staff, send_messages=True, send_messages_in_threads=True)
    cog = Moderation(None)  # type: ignore[arg-type]

    await cog._set_lock(channel, True, reason="lock")
    await cog._set_lock(channel, False, reason="unlock")

    everyone = channel.overwrites_for(guild.default_role)
    assert everyone.send_messages is None
    assert everyone.create_public_threads is None
    staff_overwrite = channel.overwrites_for(staff)
    assert staff_overwrite.send_messages is True
    assert staff_overwrite.send_messages_in_threads is True
    assert staff_overwrite.create_public_threads is None
    assert thread.locked is False


async def test_relocking_keeps_the_original_snapshot() -> None:
    guild, channel, _thread = _scene(with_thread=False)
    staff = guild.add_role(25, name="Staff")
    channel.allow(staff, send_messages=True)
    cog = Moderation(None)  # type: ignore[arg-type]

    await cog._set_lock(channel, True, reason="first")
    await cog._set_lock(channel, True, reason="second")
    await cog._set_lock(channel, False, reason="unlock")

    assert channel.overwrites_for(staff).send_messages is True


async def test_bot_keeps_talking_while_locked() -> None:
    guild, channel, _thread = _scene(with_thread=False)
    cog = Moderation(None)  # type: ignore[arg-type]

    await cog._set_lock(channel, True, reason="lock")
    bot_overwrite = channel.overwrites_for(guild.me)
    assert bot_overwrite.send_messages is True
    assert bot_overwrite.create_public_threads is True

    await cog._set_lock(channel, False, reason="unlock")
    assert channel.overwrites_for(guild.me).send_messages is None


async def test_unlock_without_snapshot_clears_the_denies() -> None:
    """After a restart the in-memory snapshot is gone; unlock must still work."""
    guild, channel, thread = _scene()
    thread.locked = True
    channel.allow(
        guild.default_role,
        send_messages=False,
        send_messages_in_threads=False,
        create_public_threads=False,
        create_private_threads=False,
    )
    cog = Moderation(None)  # type: ignore[arg-type]

    await cog._set_lock(channel, False, reason="unlock")

    overwrite = channel.overwrites_for(guild.default_role)
    assert all(getattr(overwrite, permission) is None for permission in LOCK_PERMISSIONS)


async def test_only_threads_frozen_by_the_lock_are_released() -> None:
    guild, channel, _thread = _scene()
    pre_locked = FakeThread(501, locked=True)
    channel.threads.append(pre_locked)
    cog = Moderation(None)  # type: ignore[arg-type]

    await cog._set_lock(channel, True, reason="lock")
    await cog._set_lock(channel, False, reason="unlock")

    assert pre_locked.locked is True
    assert pre_locked.edits == 0


def test_lock_helpers_roundtrip() -> None:
    overwrite = discord.PermissionOverwrite(send_messages=True, create_private_threads=True)
    previous = snapshot_lock(overwrite)
    assert previous["send_messages"] is True
    assert previous["create_public_threads"] is None

    apply_lock(overwrite, locked=True)
    assert is_locked(overwrite)

    for permission, value in previous.items():
        setattr(overwrite, permission, value)
    assert overwrite.send_messages is True
    assert not is_locked(overwrite)
