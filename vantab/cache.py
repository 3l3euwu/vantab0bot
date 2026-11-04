"""A small in-memory cache so deleted/edited messages can still be logged."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import discord

__all__ = ["CachedMessage", "MessageCache"]


@dataclass(slots=True)
class CachedMessage:
    id: int
    guild_id: int
    channel_id: int
    author_id: int
    author_name: str
    author_display: str
    author_bot: bool
    content: str
    created_at: datetime
    attachments: tuple[str, ...] = ()

    @classmethod
    def from_message(cls, message: discord.Message) -> CachedMessage:
        return cls(
            id=message.id,
            guild_id=message.guild.id if message.guild else 0,
            channel_id=message.channel.id,
            author_id=message.author.id,
            author_name=str(message.author),
            author_display=message.author.display_name,
            author_bot=message.author.bot,
            content=message.content or "",
            created_at=message.created_at,
            attachments=tuple(a.filename for a in message.attachments),
        )


class MessageCache:
    """LRU cache of recent messages, capped to keep memory flat."""

    def __init__(self, max_size: int = 5000) -> None:
        self.max_size = max_size
        self._items: OrderedDict[int, CachedMessage] = OrderedDict()

    def add(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        self._items[message.id] = CachedMessage.from_message(message)
        self._items.move_to_end(message.id)
        while len(self._items) > self.max_size:
            self._items.popitem(last=False)

    def get(self, message_id: int) -> CachedMessage | None:
        item = self._items.get(message_id)
        if item is not None:
            self._items.move_to_end(message_id)
        return item

    def update_content(self, message_id: int, content: str) -> CachedMessage | None:
        item = self._items.get(message_id)
        if item is None:
            return None
        item.content = content
        return item

    def remove(self, message_id: int) -> CachedMessage | None:
        return self._items.pop(message_id, None)

    def purge_channel(self, channel_id: int) -> None:
        for key in [k for k, v in self._items.items() if v.channel_id == channel_id]:
            self._items.pop(key, None)

    def purge_author(self, guild_id: int, author_id: int) -> int:
        keys: Iterable[int] = [k for k, v in self._items.items() if v.guild_id == guild_id and v.author_id == author_id]
        removed = 0
        for key in list(keys):
            self._items.pop(key, None)
            removed += 1
        return removed

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:  # pragma: no cover
        return len(self._items)
