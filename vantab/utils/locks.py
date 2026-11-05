"""Shared channel-lock semantics.

Discord treats threads as channels with their own permissions, so locking a
text channel for messages alone still lets members create a thread and keep
talking. Every lock in Vantab therefore denies this set of permissions.
"""

from __future__ import annotations

from typing import Any

import discord

__all__ = ["LOCK_PERMISSIONS", "apply_lock", "is_locked", "snapshot_lock"]

#: Permissions removed by a channel lock (messages + every thread permission).
LOCK_PERMISSIONS: tuple[str, ...] = (
    "send_messages",
    "send_messages_in_threads",
    "create_public_threads",
    "create_private_threads",
)


def snapshot_lock(overwrite: discord.PermissionOverwrite) -> dict[str, bool | None]:
    """Remember what the lock permissions looked like before we touch them."""
    return {permission: getattr(overwrite, permission) for permission in LOCK_PERMISSIONS}


def apply_lock(overwrite: discord.PermissionOverwrite, *, locked: bool) -> None:
    """Deny (or release) the lock permissions on an overwrite, in place."""
    for permission in LOCK_PERMISSIONS:
        setattr(overwrite, permission, False if locked else None)


def is_locked(overwrite: Any) -> bool:
    """True when every lock permission is explicitly denied."""
    return all(getattr(overwrite, permission) is False for permission in LOCK_PERMISSIONS)
