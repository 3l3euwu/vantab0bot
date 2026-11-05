"""Audit-style logging: messages, members, channels, roles and voice activity."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from ..utils.embeds import Brand, base_embed
from ..utils.text import truncate

if TYPE_CHECKING:  # pragma: no cover
    from ..bot import Vantab

log = logging.getLogger("vantab.cogs.logging")

NEW_ACCOUNT_DAYS = 7


class Logging(commands.Cog, name="Logging"):
    """Mirror server activity into the moderation log channel."""

    def __init__(self, bot: Vantab) -> None:
        self.bot = bot

    async def _channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        try:
            settings = await self.bot.guild_settings(guild.id)
        except Exception:
            return None
        channel_id = settings.mod_log_channel
        if not channel_id:
            return None
        channel = guild.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _send(self, guild: discord.Guild, embed: discord.Embed) -> None:
        channel = await self._channel(guild)
        if channel is None:
            return
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ------------------------------------------------------------------ messages
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is not None and not message.author.bot:
            self.bot.messages.add(message)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        cached = self.bot.messages.remove(message.id)
        content = message.content or (cached.content if cached else "")
        attachments = [a.filename for a in message.attachments] or (list(cached.attachments) if cached else [])
        embed = base_embed(
            f"**{message.author.mention}**'s message in {message.channel.mention} was deleted.",
            title="🗑️ Message deleted",
            colour=Brand.MUTED,
        )
        embed.add_field(
            name="Content",
            value=truncate(content, 1500) or "*(no text)*",
            inline=False,
        )
        if attachments:
            embed.add_field(name="Attachments", value="\n".join(attachments[:10]), inline=False)
        embed.set_footer(text=f"User ID {message.author.id} · Message ID {message.id}")
        await self._send(message.guild, embed)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]) -> None:
        if not messages or messages[0].guild is None:
            return
        for message in messages:
            self.bot.messages.remove(message.id)
        embed = base_embed(
            f"**{len(messages)}** messages were bulk deleted in {messages[0].channel.mention}.",
            title="🧹 Bulk delete",
            colour=Brand.MUTED,
        )
        await self._send(messages[0].guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot or before.content == after.content:
            return
        old = before.content or (self.bot.messages.get(before.id).content if self.bot.messages.get(before.id) else "")
        self.bot.messages.update_content(after.id, after.content)
        embed = base_embed(
            f"**{before.author.mention}** edited a message in {before.channel.mention}.\n"
            f"[Jump to message]({after.jump_url})",
            title="✏️ Message edited",
            colour=Brand.INFO,
        )
        embed.add_field(name="Before", value=truncate(old, 900) or "*(unknown)*", inline=False)
        embed.add_field(name="After", value=truncate(after.content, 900) or "*(empty)*", inline=False)
        await self._send(before.guild, embed)

    # ------------------------------------------------------------------- members
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        age = discord.utils.utcnow() - member.created_at
        new_account = age < timedelta(days=NEW_ACCOUNT_DAYS)
        embed = base_embed(
            f"{member.mention} joined **{member.guild.name}**.",
            title="📥 Member joined",
            colour=Brand.WARNING if new_account else Brand.SUCCESS,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Account", value=f"created {discord.utils.format_dt(member.created_at, 'R')}", inline=True)
        embed.add_field(name="Members", value=f"{member.guild.member_count:,}", inline=True)
        if new_account:
            embed.add_field(name="⚠️ New account", value=f"less than {NEW_ACCOUNT_DAYS} days old", inline=False)
        embed.set_footer(text=f"User ID {member.id}")
        await self._send(member.guild, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        embed = base_embed(
            f"**{member}** ({member.mention}) left **{member.guild.name}**.",
            title="📤 Member left",
            colour=Brand.MUTED,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        if member.joined_at:
            embed.add_field(name="Joined", value=discord.utils.format_dt(member.joined_at, "R"), inline=True)
        roles = [role.mention for role in member.roles if not role.is_default()][:15]
        if roles:
            embed.add_field(name="Roles", value=" ".join(roles), inline=False)
        embed.set_footer(text=f"User ID {member.id}")
        await self._send(member.guild, embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.bot:
            return
        if before.nick != after.nick:
            embed = base_embed(
                f"**{after}**'s nickname changed.",
                title="📝 Nickname changed",
                colour=Brand.INFO,
            )
            embed.add_field(name="Before", value=before.nick or "*none*", inline=True)
            embed.add_field(name="After", value=after.nick or "*none*", inline=True)
            embed.set_footer(text=f"User ID {after.id}")
            await self._send(after.guild, embed)

        if before.timed_out_until != after.timed_out_until:
            applied = after.timed_out_until is not None and (
                before.timed_out_until is None or after.timed_out_until > before.timed_out_until
            )
            embed = base_embed(
                f"{after.mention} was **{'timed out' if applied else 'un-timed out'}**.",
                title="🔇 Timeout changed",
                colour=Brand.WARNING if applied else Brand.SUCCESS,
            )
            if after.timed_out_until:
                embed.add_field(
                    name="Until", value=discord.utils.format_dt(after.timed_out_until, "F"), inline=True
                )
            embed.set_footer(text=f"User ID {after.id}")
            await self._send(after.guild, embed)

        added = [role for role in after.roles if role not in before.roles]
        removed = [role for role in before.roles if role not in after.roles]
        if added or removed:
            embed = base_embed(
                f"Roles updated for {after.mention}.",
                title="🎭 Roles changed",
                colour=Brand.INFO,
            )
            if added:
                embed.add_field(name="Added", value=" ".join(r.mention for r in added[:15]), inline=False)
            if removed:
                embed.add_field(name="Removed", value=" ".join(r.mention for r in removed[:15]), inline=False)
            embed.set_footer(text=f"User ID {after.id}")
            await self._send(after.guild, embed)

    # ------------------------------------------------------------------ channels
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        embed = base_embed(
            f"Channel {channel.mention if hasattr(channel, 'mention') else channel.name} created.",
            title="📁 Channel created",
            colour=Brand.SUCCESS,
        )
        embed.set_footer(text=f"Channel ID {channel.id}")
        await self._send(channel.guild, embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        embed = base_embed(
            f"Channel **#{channel.name}** was deleted.",
            title="📁 Channel deleted",
            colour=Brand.DANGER,
        )
        embed.set_footer(text=f"Channel ID {channel.id}")
        await self._send(channel.guild, embed)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        embed = base_embed(
            f"Role {role.mention} was created.",
            title="🎭 Role created",
            colour=Brand.SUCCESS,
        )
        await self._send(role.guild, embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        embed = base_embed(
            f"Role **{role.name}** was deleted.",
            title="🎭 Role deleted",
            colour=Brand.DANGER,
        )
        embed.set_footer(text=f"Role ID {role.id}")
        await self._send(role.guild, embed)

    # --------------------------------------------------------------------- voice
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        if before.channel == after.channel:
            return
        if before.channel is None and after.channel is not None:
            title, colour, detail = "🔊 Voice join", Brand.SUCCESS, f"joined **{after.channel.name}**"
        elif before.channel is not None and after.channel is None:
            title, colour, detail = "🔈 Voice leave", Brand.MUTED, f"left **{before.channel.name}**"
        else:
            title, colour = "🔀 Voice move", Brand.INFO
            detail = f"moved from **{before.channel.name}** to **{after.channel.name}**"  # type: ignore[union-attr]
        embed = base_embed(f"{member.mention} {detail}.", title=title, colour=colour)
        embed.set_footer(text=f"User ID {member.id}")
        await self._send(member.guild, embed)


async def setup(bot: Vantab) -> None:
    await bot.add_cog(Logging(bot))
