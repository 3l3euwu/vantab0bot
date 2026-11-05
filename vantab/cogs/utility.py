"""Help, information and tags."""

from __future__ import annotations

import platform
import time
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import error_embed
from ..utils.embeds import Brand, base_embed, progress_bar

if TYPE_CHECKING:  # pragma: no cover
    from ..bot import Vantab

log = "vantab.cogs.utility"

CATEGORY_ORDER = ["AutoMod", "Moderation", "Security", "Tickets", "Settings", "AI", "Utility"]
CATEGORY_ICONS = {
    "AutoMod": "🛡️",
    "Moderation": "🔨",
    "Security": "🔐",
    "Tickets": "🎫",
    "Settings": "⚙️",
    "AI": "🧠",
    "Utility": "🧰",
}


def command_summary(command: commands.Command) -> str:
    """Short description for hybrid commands (which carry it as ``description``)."""
    return (
        command.short_doc
        or getattr(command, "description", "")
        or (command.help.split("\n", 1)[0] if command.help else "")
        or "No description."
    )


def command_usage(prefix: str, command: commands.Command) -> str:
    usage = f"{prefix}{command.qualified_name}"
    if command.signature:
        usage += f" {command.signature}"
    return usage


class HelpView(discord.ui.View):
    def __init__(self, bot: Vantab, prefix: str, author_id: int) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.prefix = prefix
        self.author_id = author_id
        self.message: discord.Message | None = None
        options = [
            discord.SelectOption(label=name, value=name, emoji=CATEGORY_ICONS.get(name, "•"))
            for name in self.categories()
        ][:25]
        select = discord.ui.Select(placeholder="Browse a category…", options=options)
        select.callback = self._on_select  # type: ignore[assignment]
        self.select = select
        self.add_item(select)

    def categories(self) -> list[str]:
        found = {
            cog.qualified_name
            for cog in self.bot.cogs.values()
            if any(not command.hidden for command in cog.get_commands())
        }
        ordered = [name for name in CATEGORY_ORDER if name in found]
        ordered.extend(sorted(found - set(ordered)))
        return ordered

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This help menu isn't for you — run `help`.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction) -> None:
        category = self.select.values[0]
        embed = category_embed(self.bot, self.prefix, category)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


def category_embed(bot: Vantab, prefix: str, category: str) -> discord.Embed:
    cog = bot.get_cog(category)
    commands_list = sorted(
        (command for command in (cog.get_commands() if cog else []) if not command.hidden),
        key=lambda c: c.name,
    )
    embed = base_embed(
        f"Use `{prefix}help <command>` for details. Slash commands mirror the prefix commands.",
        title=f"{CATEGORY_ICONS.get(category, '•')} {category}",
        colour=Brand.PRIMARY,
    )
    for command in commands_list:
        aliases = f" · aliases: {', '.join(command.aliases)}" if command.aliases else ""
        embed.add_field(
            name=f"`{prefix}{command.qualified_name}`",
            value=f"{command_summary(command)}{aliases}",
            inline=False,
        )
    return embed


class Utility(commands.Cog, name="Utility"):
    """Help, information, and saved tags."""

    def __init__(self, bot: Vantab) -> None:
        self.bot = bot

    async def _prefix(self, ctx: commands.Context) -> str:
        if ctx.guild is None:
            return self.bot.settings.default_prefix
        settings = await self.bot.guild_settings(ctx.guild.id)
        return settings.prefix or self.bot.settings.default_prefix

    # ---------------------------------------------------------------------- help
    @commands.hybrid_command(name="help", description="List every command or get details on one.")
    @app_commands.describe(command="A command name for detailed help")
    async def help(self, ctx: commands.Context, *, command: str | None = None) -> None:
        prefix = await self._prefix(ctx)
        if command:
            target = self.bot.get_command(command.strip().removeprefix(prefix).strip())
            if target is None:
                await ctx.reply(embed=error_embed(f"No command called `{command}`."), mention_author=False)
                return
            embed = base_embed(
                target.help or command_summary(target),
                title=f"{prefix}{target.qualified_name}",
                colour=Brand.PRIMARY,
            )
            embed.add_field(name="Usage", value=f"`{command_usage(prefix, target)}`", inline=False)
            if target.aliases:
                embed.add_field(name="Aliases", value=", ".join(f"`{a}`" for a in target.aliases), inline=True)
            if isinstance(target, commands.HybridCommand):
                embed.add_field(name="Slash", value=f"`/{target.qualified_name}`", inline=True)
            for parent in target.parents:
                embed.add_field(name="Group", value=f"`{parent.qualified_name}`", inline=True)
            await ctx.reply(embed=embed, mention_author=False)
            return

        total = len([c for c in self.bot.commands if not c.hidden])
        embed = base_embed(
            f"**{self.bot.user.mention if self.bot.user else 'Vantab'}** — autonomous moderation + unrestricted AI.\n\n"
            f"**{total}** commands available · prefix `{prefix}` · slash commands enabled.\n\n"
            f"**Ask the AI:**  `{prefix}{{your question}}`\n"
            f"**Menu:** select a category below.",
            title="🧠 Vantab",
            colour=Brand.PRIMARY,
        )
        view = HelpView(self.bot, prefix, ctx.author.id)
        message = await ctx.reply(embed=embed, view=view, mention_author=False)
        view.message = message

    # ------------------------------------------------------------------ diagnostics
    @commands.hybrid_command(name="ping", description="Check Vantab's latency.")
    async def ping(self, ctx: commands.Context) -> None:
        start = time.perf_counter()
        latency = (time.perf_counter() - start) * 1000
        db_start = time.perf_counter()
        try:
            await self.bot.db.guild_count()
            db_ms = (time.perf_counter() - db_start) * 1000
        except Exception:
            db_ms = -1
        embed = base_embed(
            f"WebSocket: **{self.bot.latency * 1000:.0f} ms**\n"
            f"Round trip: **{latency:.0f} ms**\n"
            f"Database: **{db_ms:.1f} ms**" if db_ms >= 0 else f"WebSocket: **{self.bot.latency * 1000:.0f} ms**",
            title="🏓 Pong",
            colour=Brand.SUCCESS,
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="botinfo", aliases=["info"], description="About Vantab.")
    async def botinfo(self, ctx: commands.Context) -> None:
        guilds = len(self.bot.guilds)
        members = sum(g.member_count or 0 for g in self.bot.guilds)
        embed = base_embed(
            "Autonomous Discord moderation with an unrestricted AI on top.\n"
            "Ask anything with `~{your question}`.",
            title="Vantab",
            colour=Brand.PRIMARY,
        )
        embed.add_field(name="Servers", value=f"{guilds:,}", inline=True)
        embed.add_field(name="Members", value=f"{members:,}", inline=True)
        embed.add_field(name="Uptime", value=self.bot.uptime, inline=True)
        embed.add_field(name="discord.py", value=discord.__version__, inline=True)
        embed.add_field(name="Python", value=platform.python_version(), inline=True)
        embed.add_field(name="AI model", value=f"`{self.bot.settings.ai_model}`", inline=True)
        embed.add_field(name="Latency", value=f"{self.bot.latency * 1000:.0f} ms", inline=True)
        embed.add_field(name="Cases", value=f"{await self.bot.db.case_count():,}", inline=True)
        embed.add_field(name="Commands", value=str(len(self.bot.commands)), inline=True)
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="uptime", description="How long Vantab has been online.")
    async def uptime(self, ctx: commands.Context) -> None:
        await ctx.reply(
            embed=base_embed(
                f"Online for **{self.bot.uptime}** since {discord.utils.format_dt(self.bot.started_at, 'R')}.",
                colour=Brand.SUCCESS,
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="invite", description="Get the link to add Vantab to your server.")
    async def invite(self, ctx: commands.Context) -> None:
        permissions = discord.Permissions(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            add_reactions=True,
            manage_messages=True,
            manage_channels=True,
            manage_roles=True,
            manage_guild=True,
            kick_members=True,
            ban_members=True,
            moderate_members=True,
            view_audit_log=True,
            manage_webhooks=True,
            mention_everyone=False,
        )
        url = discord.utils.oauth_url(
            self.bot.user.id if self.bot.user else 0,
            permissions=permissions,
            scopes=("bot", "applications.commands"),
        )
        await ctx.reply(
            embed=base_embed(f"[Add Vantab to your server]({url})", colour=Brand.PRIMARY),
            mention_author=False,
        )

    # ---------------------------------------------------------------------- users
    @commands.hybrid_command(name="userinfo", aliases=["whois"], description="Detailed info about a user.")
    @app_commands.describe(member="Whose info to show")
    async def userinfo(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or (ctx.author if isinstance(ctx.author, discord.Member) else None)
        if target is None:
            await ctx.reply(embed=error_embed("Run this in a server."), mention_author=False)
            return
        embed = base_embed(title=f"👤 {target}", colour=target.colour.value if target.colour.value else Brand.PRIMARY)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Mention", value=target.mention, inline=True)
        embed.add_field(name="ID", value=f"`{target.id}`", inline=True)
        embed.add_field(name="Bot", value="yes" if target.bot else "no", inline=True)
        embed.add_field(name="Account created", value=discord.utils.format_dt(target.created_at, "F"), inline=False)
        if target.joined_at:
            embed.add_field(name="Joined server", value=discord.utils.format_dt(target.joined_at, "F"), inline=False)
        if target.timed_out_until:
            embed.add_field(name="Timed out until", value=discord.utils.format_dt(target.timed_out_until, "R"), inline=False)
        roles = [role.mention for role in target.roles if not role.is_default()]
        embed.add_field(
            name=f"Roles ({len(roles)})",
            value=" ".join(roles[:20]) if roles else "none",
            inline=False,
        )
        key_perms = [
            name.replace("_", " ").title()
            for name, value in target.guild_permissions
            if value and name in {"administrator", "manage_guild", "manage_messages", "ban_members", "kick_members", "moderate_members", "manage_channels", "manage_roles"}
        ]
        if key_perms:
            embed.add_field(name="Key permissions", value=", ".join(key_perms), inline=False)
        created_days = (discord.utils.utcnow() - target.created_at).days
        embed.set_footer(text=f"Account is {created_days} day(s) old · requested by {ctx.author}")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="avatar", description="Show a user's avatar.")
    @app_commands.describe(member="Whose avatar to show")
    async def avatar(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        embed = base_embed(title=f"🖼️ {target.display_name}'s avatar", colour=Brand.PRIMARY)
        embed.set_image(url=target.display_avatar.with_size(1024).url)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="banner", description="Show a user's profile banner.")
    @app_commands.describe(member="Whose banner to show")
    async def banner(self, ctx: commands.Context, member: discord.User | None = None) -> None:
        target = member or ctx.author
        try:
            user = await self.bot.fetch_user(target.id)
        except discord.HTTPException:
            user = target  # type: ignore[assignment]
        banner = getattr(user, "banner", None)
        if banner is None:
            await ctx.reply(embed=error_embed("That user has no banner."), mention_author=False)
            return
        embed = base_embed(title=f"🎏 {user.display_name}'s banner", colour=Brand.PRIMARY)
        embed.set_image(url=banner.with_size(1024).url)
        await ctx.reply(embed=embed, mention_author=False)

    # -------------------------------------------------------------------- server
    @commands.hybrid_command(name="serverinfo", aliases=["guildinfo"], description="Detailed server information.")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        assert guild is not None
        humans = sum(1 for m in guild.members if not m.bot)
        bots = sum(1 for m in guild.members if m.bot)
        text = len(guild.text_channels)
        voice = len(guild.voice_channels)
        embed = base_embed(title=f"🏰 {guild.name}", colour=Brand.PRIMARY)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=f"<@{guild.owner_id}>", inline=True)
        embed.add_field(name="ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "R"), inline=True)
        embed.add_field(name="Members", value=f"👥 {humans:,} humans\n🤖 {bots:,} bots", inline=True)
        embed.add_field(name="Channels", value=f"💬 {text}\n🔊 {voice}", inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="Boosts", value=f"{guild.premium_subscription_count} (level {guild.premium_tier})", inline=True)
        embed.add_field(name="Emojis", value=str(len(guild.emojis)), inline=True)
        embed.add_field(name="Verification", value=str(guild.verification_level).title(), inline=True)
        if guild.features:
            embed.add_field(
                name="Features",
                value=", ".join(feature.replace("_", " ").title() for feature in guild.features[:12]),
                inline=False,
            )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="membercount", description="Count humans and bots in the server.")
    @commands.guild_only()
    async def membercount(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        assert guild is not None
        humans = sum(1 for m in guild.members if not m.bot)
        bots = sum(1 for m in guild.members if m.bot)
        total = humans + bots
        embed = base_embed(
            f"👤 Humans: **{humans:,}**\n🤖 Bots: **{bots:,}**\n📊 Total: **{total:,}**",
            title=f"Member count · {guild.name}",
            colour=Brand.PRIMARY,
        )
        embed.add_field(name="Bots ratio", value=progress_bar(bots, max(1, total)), inline=False)
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="roleinfo", description="Information about a role.")
    @app_commands.describe(role="The role to inspect")
    @commands.guild_only()
    async def roleinfo(self, ctx: commands.Context, *, role: discord.Role) -> None:
        guild = ctx.guild
        assert guild is not None
        embed = base_embed(title=f"🎭 {role.name}", colour=role.colour.value or Brand.PRIMARY)
        embed.add_field(name="ID", value=f"`{role.id}`", inline=True)
        embed.add_field(name="Members", value=str(len(role.members)), inline=True)
        embed.add_field(name="Position", value=str(role.position), inline=True)
        embed.add_field(name="Colour", value=f"#{role.colour.value:06x}" if role.colour.value else "none", inline=True)
        embed.add_field(name="Mentionable", value="yes" if role.mentionable else "no", inline=True)
        embed.add_field(name="Hoisted", value="yes" if role.hoist else "no", inline=True)
        dangerous = [
            name.replace("_", " ").title()
            for name, value in role.permissions
            if value and name in {"administrator", "manage_guild", "manage_roles", "manage_channels", "ban_members", "kick_members", "manage_messages", "moderate_members", "manage_webhooks"}
        ]
        embed.add_field(name="Key permissions", value=", ".join(dangerous) or "none", inline=False)
        await ctx.reply(embed=embed, mention_author=False)

    # ---------------------------------------------------------------------- tags
    @commands.hybrid_command(name="tag", description="Show a saved tag.")
    @app_commands.describe(name="Tag name")
    @commands.guild_only()
    async def tag(self, ctx: commands.Context, *, name: str) -> None:
        result = await self.bot.db.get_tag(ctx.guild.id, name)
        if result is None:
            await ctx.reply(
                embed=error_embed(f"No tag called `{name}`. Create one with `~tagadd {name} <content>`."),
                mention_author=False,
            )
            return
        content, owner_id, uses = result
        embed = base_embed(content, title=f"🏷️ {name}", colour=Brand.PRIMARY)
        embed.set_footer(text=f"By <@{owner_id}> · used {uses} time(s)")
        await ctx.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(name="tagadd", description="Create or overwrite a tag.")
    @app_commands.describe(name="Tag name", content="What the tag should say")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def tagadd(self, ctx: commands.Context, name: str, *, content: str) -> None:
        name = name.lower().strip()
        if not name.isalnum() and not all(c.isalnum() or c in "-_" for c in name):
            await ctx.reply(embed=error_embed("Tag names can only contain letters, numbers, `-` and `_`."), mention_author=False)
            return
        if len(content) > 1900:
            await ctx.reply(embed=error_embed("Tag content is too long (max 1900 characters)."), mention_author=False)
            return
        await self.bot.db.add_tag(ctx.guild.id, name, content, ctx.author.id)
        await ctx.reply(
            embed=base_embed(f"Tag **{name}** saved. Use `~tag {name}` to show it.", colour=Brand.SUCCESS),
            mention_author=False,
        )

    @commands.hybrid_command(name="tagremove", aliases=["tagdelete"], description="Delete a tag.")
    @app_commands.describe(name="Tag name")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def tagremove(self, ctx: commands.Context, name: str) -> None:
        removed = await self.bot.db.delete_tag(ctx.guild.id, name)
        await ctx.reply(
            embed=base_embed(
                f"Tag **{name}** deleted." if removed else f"No tag called `{name}`.",
                colour=Brand.SUCCESS if removed else Brand.MUTED,
            ),
            mention_author=False,
        )

    @commands.hybrid_command(name="taglist", aliases=["tags"], description="List every tag.")
    @commands.guild_only()
    async def taglist(self, ctx: commands.Context) -> None:
        tags = await self.bot.db.list_tags(ctx.guild.id)
        if not tags:
            await ctx.reply(embed=base_embed("No tags yet.", colour=Brand.MUTED), mention_author=False)
            return
        embed = base_embed(
            ", ".join(f"`{name}`" for name, _ in tags[:100]),
            title=f"🏷️ Tags ({len(tags)})",
            colour=Brand.PRIMARY,
        )
        await ctx.reply(embed=embed, mention_author=False)


async def setup(bot: Vantab) -> None:
    await bot.add_cog(Utility(bot))
