"""End-to-end tests of the auto-mod pipeline using lightweight Discord fakes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import make_settings

from vantab.automod.engine import AutoModEngine
from vantab.database import Database


class FakeRole:
    def __init__(self, position: int, role_id: int = 1, name: str = "role") -> None:
        self.position = position
        self.id = role_id
        self.name = name

    def __ge__(self, other):  # noqa: D105
        return self.position >= other.position

    def __gt__(self, other):  # noqa: D105
        return self.position > other.position

    def __le__(self, other):  # noqa: D105
        return self.position <= other.position

    def __lt__(self, other):  # noqa: D105
        return self.position < other.position

    def __eq__(self, other):  # noqa: D105
        return isinstance(other, FakeRole) and self.position == other.position

    def __hash__(self):  # noqa: D105
        return hash(self.position)


class FakePermissions:
    def __init__(self, **perms) -> None:
        self.administrator = perms.get("administrator", False)
        self.manage_messages = perms.get("manage_messages", False)
        self.manage_guild = perms.get("manage_guild", False)
        self.moderate_members = perms.get("moderate_members", True)
        self.kick_members = perms.get("kick_members", True)
        self.ban_members = perms.get("ban_members", True)
        self.mention_everyone = perms.get("mention_everyone", False)


class FakeMember:
    def __init__(self, guild, *, member_id=3, position=1, **perms) -> None:
        self.guild = guild
        self.id = member_id
        self.bot = False
        self.roles = [FakeRole(position)]
        self.top_role = self.roles[0]
        self.guild_permissions = FakePermissions(**perms)
        self.mention = f"<@{member_id}>"
        self.display_avatar = SimpleNamespace(url="https://cdn.example/avatar.png")
        self.display_name = "Member"
        self.created_at = datetime.now(UTC) - timedelta(days=400)
        self.timed_out_until = None
        self.actions: list[tuple[str, object]] = []

    def __str__(self) -> str:
        return "Member#0001"

    async def timeout(self, until, *, reason=None):
        self.timed_out_until = until
        self.actions.append(("timeout", until))

    async def kick(self, *, reason=None):
        self.actions.append(("kick", reason))

    async def ban(self, *, reason=None, delete_message_seconds=0):
        self.actions.append(("ban", reason))

    async def send(self, *args, **kwargs):  # pragma: no cover - DM path disabled in tests
        return None


class FakeChannel:
    def __init__(self, guild, channel_id=2, nsfw=False) -> None:
        self.guild = guild
        self.id = channel_id
        self.nsfw = nsfw
        self.mention = f"<#{channel_id}>"
        self.sent: list[str] = []

    def permissions_for(self, member) -> FakePermissions:
        return getattr(member, "guild_permissions", FakePermissions())

    async def send(self, content=None, **kwargs):
        self.sent.append(str(content))
        return SimpleNamespace(id=999)


class FakeGuild:
    def __init__(self) -> None:
        self.id = 1
        self.owner_id = 999
        self.name = "Test Guild"
        self.member_count = 10
        self.channels: dict[int, FakeChannel] = {}
        self.me = SimpleNamespace(
            id=888,
            top_role=FakeRole(10),
            guild_permissions=FakePermissions(administrator=True),
            roles=[FakeRole(10)],
        )
        self.bans: list[tuple[int, str]] = []

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def get_member(self, member_id):
        return None

    async def ban(self, user, *, reason=None, delete_message_seconds=0):
        self.bans.append((getattr(user, "id", 0), reason or ""))


class FakeMessage:
    def __init__(
        self,
        guild: FakeGuild,
        channel: FakeChannel,
        author: FakeMember,
        content: str,
        *,
        created_at: datetime | None = None,
        mentions=(),
        role_mentions=(),
        everyone=False,
        attachments=(),
        webhook_id=None,
    ) -> None:
        self.guild = guild
        self.channel = channel
        self.author = author
        self.content = content
        self.created_at = created_at or datetime.now(UTC)
        self.mentions = list(mentions)
        self.role_mentions = list(role_mentions)
        self.mention_everyone = everyone
        self.attachments = list(attachments)
        self.embeds = []
        self.stickers = []
        self.webhook_id = webhook_id
        self.reference = None
        self.id = 12345
        self.jump_url = "https://discord.com/channels/1/2/12345"
        self.deleted = False

    def is_system(self) -> bool:
        return False

    async def delete(self):
        self.deleted = True


class FakeBot:
    def __init__(self, db: Database, settings) -> None:
        self.db = db
        self.settings = settings
        self.owner_ids: set[int] = set()
        self.user = SimpleNamespace(id=888)

    def get_cog(self, name):
        return None


@pytest.fixture()
async def engine(db: Database):
    settings = make_settings(dm_on_punish=False)
    bot = FakeBot(db, settings)
    guild = FakeGuild()
    channel = FakeChannel(guild)
    guild.channels[channel.id] = channel
    return AutoModEngine(bot), guild, channel  # type: ignore[arg-type]


def member(guild: FakeGuild, **kwargs) -> FakeMember:
    return FakeMember(guild, **kwargs)


def message(guild, channel, author, content, **kwargs) -> FakeMessage:
    return FakeMessage(guild, channel, author, content, **kwargs)


async def test_clean_message_is_ignored(engine) -> None:
    automod, guild, channel = engine
    result = await automod.process(message(guild, channel, member(guild), "hello everyone"))
    assert result is None


async def test_spam_triggers_timeout_and_case(engine) -> None:
    automod, guild, channel = engine
    author = member(guild)
    now = datetime.now(UTC)
    result = None
    for index in range(7):
        result = await automod.process(
            message(guild, channel, author, f"spam {index}", created_at=now + timedelta(seconds=index))
        )
    assert result is not None
    assert result.punished is True
    assert result.action in {"timeout", "kick", "ban"}
    assert any(action[0] == "timeout" for action in author.actions)
    assert result.case_number is not None
    cases = await automod.db.list_cases(guild.id, user_id=author.id)
    assert cases and cases[0].action == "timeout"
    assert (await automod.db.count_warnings(guild.id, author.id)) >= 1


async def test_phishing_link_gets_banned(engine) -> None:
    automod, guild, channel = engine
    author = member(guild)
    result = await automod.process(
        message(guild, channel, author, "free nitro https://discord-nitro.com/gift")
    )
    assert result is not None
    assert result.action == "ban"
    assert guild.bans and guild.bans[0][0] == author.id
    assert result.detections[0].rule == "phishing"


async def test_ban_action_for_user_outside_guild(engine) -> None:
    automod, guild, channel = engine
    author = member(guild)
    result = await automod.process(
        message(guild, channel, author, "claim your airdrop at https://dlscord-gift.com")
    )
    assert result is not None
    assert result.action == "ban"


async def test_administrators_bypass_automod(engine) -> None:
    automod, guild, channel = engine
    admin = member(guild, member_id=77, position=5, administrator=True)
    spammy = "X" * 40
    for index in range(10):
        result = await automod.process(
            message(guild, channel, admin, f"{spammy} {index}", created_at=datetime.now(UTC))
        )
        assert result is None


async def test_channel_exemption(engine) -> None:
    automod, guild, channel = engine
    await automod.db.add_exemption(guild.id, "channel", channel.id)
    automod.invalidate(guild.id)
    result = await automod.process(
        message(guild, channel, member(guild), "discord.gg/abcdef")
    )
    assert result is None


async def test_badword_delete_only(engine) -> None:
    automod, guild, channel = engine
    await automod.db.add_badword(guild.id, "bananas")
    automod.invalidate(guild.id)
    msg = message(guild, channel, member(guild), "I love bananas")
    result = await automod.process(msg)
    assert result is not None
    assert result.action == "delete"
    assert result.punished is False
    assert msg.deleted is True
    assert (await automod.db.list_cases(guild.id)) == []


async def test_disabled_automod(engine) -> None:
    automod, guild, channel = engine
    await automod.db.update_guild(guild.id, automod_enabled=False)
    automod.invalidate(guild.id)
    result = await automod.process(message(guild, channel, member(guild), "discord.gg/abc"))
    assert result is None


async def test_everyone_mention_punished(engine) -> None:
    automod, guild, channel = engine
    author = member(guild)
    result = await automod.process(
        message(guild, channel, author, "@everyone raid time", everyone=True)
    )
    assert result is not None
    assert result.action in {"timeout", "kick", "ban"}
    assert author.actions


async def test_bot_messages_are_ignored(engine) -> None:
    automod, guild, channel = engine
    bot_member = member(guild, member_id=555)
    bot_member.bot = True
    result = await automod.process(
        message(guild, channel, bot_member, "discord.gg/abc https://discord-nitro.com")
    )
    assert result is None


async def test_webhook_messages_are_ignored(engine) -> None:
    automod, guild, channel = engine
    result = await automod.process(
        message(guild, channel, member(guild), "discord.gg/abc", webhook_id=42)
    )
    assert result is None
