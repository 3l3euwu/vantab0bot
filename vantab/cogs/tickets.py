"""Support tickets: panel, private channels, claiming and transcripts.

Commands are intentionally thin — every Discord call goes through
:class:`vantab.tickets.TicketService` so the behaviour can be tested without a
gateway connection.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import defer_if_interaction, error_embed
from ..database import TicketEntry
from ..tickets import (
    TicketError,
    TicketService,
    panel_embed,
    staff_overwrite,
    summary_lines,
)
from ..ui.tickets import TicketPanelView
from ..ui.views import ConfirmView
from ..utils.embeds import Brand, base_embed

if TYPE_CHECKING:  # pragma: no cover
    from ..bot import Vantab

log = logging.getLogger("vantab.cogs.tickets")

MAX_TICKETS_PER_USER = 5


class Tickets(commands.Cog, name="Tickets"):
    """Private support tickets your members can open in one click."""

    def __init__(self, bot: Vantab) -> None:
        self.bot = bot
        self.service = TicketService(bot)

    # ------------------------------------------------------------------ helpers
    @property
    def db(self):
        return self.bot.db

    async def _current_ticket(self, ctx: commands.Context) -> TicketEntry | None:
        return await self.db.ticket_for_channel(ctx.channel.id)

    async def _respond(
        self, ctx: commands.Context, embed: discord.Embed, *, ephemeral: bool = True
    ) -> None:
        """Answer whether the command came from a prefix or a slash invocation."""
        interaction = ctx.interaction
        try:
            if interaction is not None:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
            else:
                await ctx.reply(embed=embed, mention_author=False)
        except discord.HTTPException:
            pass

    async def _require_staff(self, ctx: commands.Context) -> bool:
        settings = await self.bot.guild_settings(ctx.guild.id)
        if not TicketService.is_staff(ctx.author, settings):
            await self._respond(ctx, error_embed("Only staff can do that."))
            return False
        return True

    async def _ticket_channel(
        self, ctx: commands.Context, ticket: TicketEntry
    ) -> discord.TextChannel | None:
        channel = ctx.guild.get_channel(ticket.channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        await self._respond(ctx, error_embed("The ticket channel no longer exists."))
        return None

    # ----------------------------------------------------------- interaction UI
    async def prompt_ticket(self, interaction: discord.Interaction) -> None:
        """Panel button: validate, then ask for a subject."""
        from ..ui.tickets import TicketModal

        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Tickets only work inside a server.", ephemeral=True
            )
            return
        settings = await self.bot.guild_settings(guild.id)
        if not settings.tickets_enabled:
            await interaction.response.send_message(
                embed=error_embed("Tickets are disabled on this server."), ephemeral=True
            )
            return
        existing = await self.db.open_ticket_for(guild.id, interaction.user.id)
        if existing is not None:
            channel = guild.get_channel(existing.channel_id)
            where = channel.mention if channel is not None else f"<#{existing.channel_id}>"
            await interaction.response.send_message(
                embed=error_embed(f"You already have an open ticket: {where}"), ephemeral=True
            )
            return
        await interaction.response.send_modal(TicketModal(self.bot))

    async def create_ticket(
        self, interaction: discord.Interaction, *, subject: str, details: str = ""
    ) -> None:
        """Modal submission: create the private channel."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Tickets only work inside a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            _ticket, channel = await self.service.open_ticket(
                guild, interaction.user, subject=subject, details=details
            )
        except TicketError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return
        except Exception:  # pragma: no cover - unexpected Discord failure
            log.exception("Ticket creation failed in guild %s", guild.id)
            await interaction.followup.send(
                embed=error_embed("Something went wrong while creating your ticket."), ephemeral=True
            )
            return
        await interaction.followup.send(
            embed=base_embed(f"🎫 Your ticket is ready: {channel.mention}", colour=Brand.SUCCESS),
            ephemeral=True,
        )

    async def button_action(self, interaction: discord.Interaction, action: str) -> None:
        """Claim / close / transcript / delete, from the in-channel buttons."""
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Tickets only work in server text channels.", ephemeral=True
            )
            return
        ticket = await self.db.ticket_for_channel(channel.id)
        if ticket is None:
            await interaction.response.send_message(
                "This channel is not a ticket.", ephemeral=True
            )
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        settings = await self.bot.guild_settings(guild.id)
        if member is None or not TicketService.is_staff(member, settings):
            await interaction.response.send_message(
                embed=error_embed("Only staff can use these buttons."), ephemeral=True
            )
            return

        if action == "claim":
            try:
                updated = await self.service.claim(ticket, member)
            except TicketError as exc:
                await interaction.response.send_message(embed=error_embed(str(exc)), ephemeral=True)
                return
            await interaction.response.send_message(
                embed=base_embed(f"🙋 You claimed ticket **#{updated.number:04d}**.", colour=Brand.SUCCESS),
                ephemeral=True,
            )
            await self.bot.send_embed(
                channel,
                base_embed(f"{member.mention} is now handling this ticket.", colour=Brand.INFO),
            )
            return

        if action == "close":
            if not TicketService.can_close(member, ticket, settings):
                await interaction.response.send_message(
                    embed=error_embed("Only the opener or staff can close this ticket."), ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                closed = await self.service.close_ticket(ticket, guild, closed_by=member)
            except TicketError as exc:
                await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
                return
            await interaction.followup.send(
                embed=base_embed(
                    f"🔒 Ticket **#{closed.number:04d}** closed and archived.", colour=Brand.SUCCESS
                ),
                ephemeral=True,
            )
            return

        if action == "transcript":
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                path = await self.service.export(guild, ticket, channel)
            except discord.HTTPException as exc:
                await interaction.followup.send(
                    embed=error_embed(f"Could not read the history (`{exc.status}`)."), ephemeral=True
                )
                return
            await interaction.followup.send(
                embed=base_embed(
                    f"📄 Transcript of ticket **#{ticket.number:04d}**.", colour=Brand.INFO
                ),
                file=discord.File(path, filename=path.name),
                ephemeral=True,
            )
            return

        if action == "delete":
            if not member.guild_permissions.manage_channels:
                await interaction.response.send_message(
                    embed=error_embed("You need **Manage Channels** to delete a ticket."), ephemeral=True
                )
                return
            view = ConfirmView(member.id, confirm_label="Delete channel")
            await interaction.response.send_message(
                embed=error_embed(
                    "Delete this ticket channel? The transcript is archived in the log channel."
                ),
                view=view,
                ephemeral=True,
            )
            if not await view.wait() or not view.value:
                return
            if ticket.is_open:
                await self.db.close_ticket(ticket.id, closed_by=member.id, reason="deleted")
            try:
                await channel.delete(reason=f"Ticket #{ticket.number:04d} deleted by {member}")
            except discord.HTTPException:
                pass
            await self.service.log_embed(
                guild,
                base_embed(
                    f"Ticket **#{ticket.number:04d}** was deleted by {member.mention} (`{member.id}`).",
                    title="🎫 Ticket deleted",
                    colour=Brand.DANGER,
                ),
            )
            return

        await interaction.response.send_message(embed=error_embed("Unknown action."), ephemeral=True)

    # ------------------------------------------------------------------ listener
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        ticket = await self.db.ticket_for_channel(channel.id)
        if ticket is None or not ticket.is_open:
            return
        await self.db.close_ticket(ticket.id, closed_by=None, reason="channel deleted")
        await self.service.log_embed(
            channel.guild,
            base_embed(
                f"Ticket **#{ticket.number:04d}** channel was deleted before being closed.",
                title="🎫 Ticket channel deleted",
                colour=Brand.WARNING,
            ),
        )

    # ------------------------------------------------------------------- status
    @commands.hybrid_group(
        name="ticket",
        description="Support tickets: setup, panel and management.",
        invoke_without_command=True,
    )
    @commands.guild_only()
    async def ticket(self, ctx: commands.Context) -> None:
        await self._status(ctx)

    async def _status(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        assert guild is not None
        settings = await self.bot.guild_settings(guild.id)
        stats = await self.db.ticket_stats(guild.id)
        category = guild.get_channel(settings.ticket_category_id) if settings.ticket_category_id else None
        staff_role = guild.get_role(settings.ticket_staff_role_id) if settings.ticket_staff_role_id else None
        log_channel = guild.get_channel(settings.ticket_log_channel) if settings.ticket_log_channel else None
        embed = base_embed(
            f"Tickets are **{'enabled' if settings.tickets_enabled else 'disabled'}**.\n"
            f"Open: **{stats['open']}** · Closed: **{stats['closed']}** · Total: **{stats['total']}**",
            title="🎫 Tickets",
            colour=Brand.SUCCESS if settings.tickets_enabled else Brand.MUTED,
        )
        embed.add_field(name="Category", value=category.mention if category else "auto-created", inline=True)
        embed.add_field(name="Staff role", value=staff_role.mention if staff_role else "—", inline=True)
        embed.add_field(name="Log channel", value=log_channel.mention if log_channel else "—", inline=True)
        embed.add_field(name="Max per member", value=str(settings.ticket_max_open), inline=True)
        embed.add_field(
            name="Setup",
            value="`ticket setup [#category] [@staff] [#log]`\n`ticket panel [#channel]`",
            inline=False,
        )
        embed.set_footer(text="~ticket open · close · claim · add · transcript · list · stats")
        await self._respond(ctx, embed, ephemeral=False)

    # -------------------------------------------------------------------- setup
    @ticket.command(name="setup", description="Configure the category, staff role and log channel.")
    @app_commands.describe(
        category="Category that will hold the ticket channels",
        staff_role="Role that can see and answer every ticket",
        log_channel="Where ticket logs and transcripts are sent",
    )
    @commands.has_guild_permissions(manage_guild=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def ticket_setup(
        self,
        ctx: commands.Context,
        category: discord.CategoryChannel | None = None,
        staff_role: discord.Role | None = None,
        log_channel: discord.TextChannel | None = None,
    ) -> None:
        guild = ctx.guild
        assert guild is not None
        settings = await self.bot.guild_settings(guild.id)
        if category is None and not settings.ticket_category_id:
            try:
                category = await guild.create_category("Tickets", reason="Vantab ticket system")
            except discord.HTTPException as exc:
                await self._respond(ctx, error_embed(f"Could not create the category (`{exc.status}`)."))
                return
        fields: dict[str, object] = {"tickets_enabled": True}
        if category is not None:
            fields["ticket_category_id"] = category.id
        if staff_role is not None:
            fields["ticket_staff_role_id"] = staff_role.id
        if log_channel is not None:
            fields["ticket_log_channel"] = log_channel.id
        await self.db.update_guild(guild.id, **fields)
        self.bot.invalidate_settings(guild.id)

        updated = await self.bot.guild_settings(guild.id, force=True)
        final_category = guild.get_channel(updated.ticket_category_id or 0)
        final_role = guild.get_role(updated.ticket_staff_role_id or 0)
        final_log = guild.get_channel(updated.ticket_log_channel or 0)
        embed = base_embed(
            "Tickets are ready.\n\n"
            f"**Category:** {final_category.mention if final_category else '—'}\n"
            f"**Staff role:** {final_role.mention if final_role else '—'}\n"
            f"**Log channel:** {final_log.mention if final_log else '—'}\n\n"
            "Next: `ticket panel #channel` to post the button members will click.\n"
            "You can change the staff role later with `ticket staff @Role`.",
            title="✅ Ticket system configured",
            colour=Brand.SUCCESS,
        )
        await self._respond(ctx, embed, ephemeral=False)

    @ticket.command(name="staff", description="Show or change the role that handles tickets.")
    @app_commands.describe(
        role="Role that can see and answer every ticket (omit it to see the current one)"
    )
    @commands.has_guild_permissions(manage_guild=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def ticket_staff(self, ctx: commands.Context, role: discord.Role | None = None) -> None:
        guild = ctx.guild
        assert guild is not None

        if role is None:
            settings = await self.bot.guild_settings(guild.id)
            current = guild.get_role(settings.ticket_staff_role_id or 0)
            await self._respond(
                ctx,
                base_embed(
                    f"**Ticket staff role:** {current.mention if current else '— (not set)'}\n\n"
                    "Change it with `ticket staff @Role`. Administrators, Manage Server and "
                    "Manage Channels already count as staff."
                ),
            )
            return

        await self.db.update_guild(guild.id, ticket_staff_role_id=role.id)
        self.bot.invalidate_settings(guild.id)

        updated_channels = 0
        for ticket in await self.db.list_tickets(guild.id, status="open", limit=100):
            channel = guild.get_channel(ticket.channel_id)
            if channel is None:
                continue
            try:
                await channel.set_permissions(
                    role,
                    overwrite=staff_overwrite(),
                    reason=f"Ticket staff role set by {ctx.author}",
                )
                updated_channels += 1
            except discord.HTTPException:
                continue

        text = f"✅ Ticket staff role set to {role.mention}."
        if updated_channels:
            text += f" {updated_channels} open ticket(s) were updated too."
        text += (
            "\nThat role can now see every ticket, answer it and use "
            "`ticket claim/unclaim/add/remove/rename/transcript/info`."
        )
        await self._respond(ctx, base_embed(text, colour=Brand.SUCCESS))

    @ticket.command(name="panel", description="Post the panel members use to open tickets.")
    @app_commands.describe(channel="Where to post the panel (default: here)")
    @commands.has_guild_permissions(manage_guild=True)
    @commands.bot_has_permissions(send_messages=True, embed_links=True)
    async def ticket_panel(
        self, ctx: commands.Context, channel: discord.TextChannel | None = None
    ) -> None:
        guild = ctx.guild
        assert guild is not None
        target = channel or ctx.channel
        if not isinstance(target, discord.TextChannel):
            await self._respond(ctx, error_embed("Choose a text channel."))
            return
        settings = await self.bot.guild_settings(guild.id)
        staff_role = guild.get_role(settings.ticket_staff_role_id) if settings.ticket_staff_role_id else None
        try:
            await target.send(
                embed=panel_embed(guild, staff_role=staff_role), view=TicketPanelView(self.bot)
            )
        except discord.HTTPException as exc:
            await self._respond(ctx, error_embed(f"Could not post the panel (`{exc.status}`)."))
            return
        await self._respond(
            ctx,
            base_embed(f"Ticket panel posted in {target.mention}.", colour=Brand.SUCCESS),
            ephemeral=False,
        )

    @ticket.command(name="enable", description="Re-enable opening new tickets.")
    @commands.has_guild_permissions(manage_guild=True)
    async def ticket_enable(self, ctx: commands.Context) -> None:
        await self.db.update_guild(ctx.guild.id, tickets_enabled=True)
        self.bot.invalidate_settings(ctx.guild.id)
        await self._respond(ctx, base_embed("Tickets enabled.", colour=Brand.SUCCESS))

    @ticket.command(name="disable", description="Stop new tickets (existing ones keep working).")
    @commands.has_guild_permissions(manage_guild=True)
    async def ticket_disable(self, ctx: commands.Context) -> None:
        await self.db.update_guild(ctx.guild.id, tickets_enabled=False)
        self.bot.invalidate_settings(ctx.guild.id)
        await self._respond(ctx, base_embed("Tickets disabled.", colour=Brand.WARNING))

    @ticket.command(name="max", description="How many open tickets one member may have.")
    @app_commands.describe(amount="Between 1 and 5")
    @commands.has_guild_permissions(manage_guild=True)
    async def ticket_max(self, ctx: commands.Context, amount: int) -> None:
        if not 1 <= amount <= MAX_TICKETS_PER_USER:
            await self._respond(
                ctx, error_embed(f"Choose between **1** and **{MAX_TICKETS_PER_USER}**.")
            )
            return
        await self.db.update_guild(ctx.guild.id, ticket_max_open=amount)
        self.bot.invalidate_settings(ctx.guild.id)
        await self._respond(
            ctx,
            base_embed(f"Each member may now have **{amount}** open ticket(s).", colour=Brand.SUCCESS),
        )

    # --------------------------------------------------------------------- open
    @ticket.command(name="open", description="Open a support ticket without using the panel.")
    @app_commands.describe(subject="Short summary of your issue")
    @commands.guild_only()
    async def ticket_open(self, ctx: commands.Context, *, subject: str | None = None) -> None:
        guild = ctx.guild
        assert guild is not None
        await defer_if_interaction(ctx)
        try:
            ticket, channel = await self.service.open_ticket(
                guild, ctx.author, subject=(subject or "").strip()
            )
        except TicketError as exc:
            await self._respond(ctx, error_embed(str(exc)))
            return
        await self._respond(
            ctx,
            base_embed(
                f"🎫 Ticket **#{ticket.number:04d}** is open: {channel.mention}",
                colour=Brand.SUCCESS,
            ),
        )

    # ---------------------------------------------------------------- management
    @ticket.command(name="close", description="Close the ticket in this channel and archive it.")
    @app_commands.describe(reason="Why the ticket is being closed")
    @commands.guild_only()
    async def ticket_close(self, ctx: commands.Context, *, reason: str | None = None) -> None:
        guild = ctx.guild
        assert guild is not None
        ticket = await self._current_ticket(ctx)
        if ticket is None:
            await self._respond(ctx, error_embed("Run this command inside a ticket channel."))
            return
        settings = await self.bot.guild_settings(guild.id)
        if not TicketService.can_close(ctx.author, ticket, settings):
            await self._respond(ctx, error_embed("Only the opener or staff can close this ticket."))
            return
        await defer_if_interaction(ctx)
        try:
            closed = await self.service.close_ticket(
                ticket, guild, closed_by=ctx.author, reason=(reason or "").strip()
            )
        except TicketError as exc:
            await self._respond(ctx, error_embed(str(exc)))
            return
        await self._respond(
            ctx,
            base_embed(f"🔒 Ticket **#{closed.number:04d}** closed and archived.", colour=Brand.SUCCESS),
        )

    @ticket.command(name="claim", description="Take ownership of this ticket.")
    @commands.guild_only()
    async def ticket_claim(self, ctx: commands.Context) -> None:
        ticket = await self._current_ticket(ctx)
        if ticket is None:
            await self._respond(ctx, error_embed("Run this command inside a ticket channel."))
            return
        if not await self._require_staff(ctx):
            return
        try:
            updated = await self.service.claim(ticket, ctx.author)
        except TicketError as exc:
            await self._respond(ctx, error_embed(str(exc)))
            return
        await self._respond(
            ctx,
            base_embed(f"🙋 You claimed ticket **#{updated.number:04d}**.", colour=Brand.SUCCESS),
        )

    @ticket.command(name="unclaim", description="Release this ticket back to the team.")
    @commands.guild_only()
    async def ticket_unclaim(self, ctx: commands.Context) -> None:
        ticket = await self._current_ticket(ctx)
        if ticket is None:
            await self._respond(ctx, error_embed("Run this command inside a ticket channel."))
            return
        if not await self._require_staff(ctx):
            return
        try:
            updated = await self.service.unclaim(ticket, ctx.author)
        except TicketError as exc:
            await self._respond(ctx, error_embed(str(exc)))
            return
        await self._respond(
            ctx,
            base_embed(f"Ticket **#{updated.number:04d}** is unclaimed.", colour=Brand.MUTED),
        )

    @ticket.command(name="add", description="Give another member access to this ticket.")
    @app_commands.describe(member="Who to add")
    @commands.guild_only()
    async def ticket_add(self, ctx: commands.Context, member: discord.Member) -> None:
        ticket = await self._current_ticket(ctx)
        if ticket is None:
            await self._respond(ctx, error_embed("Run this command inside a ticket channel."))
            return
        if not await self._require_staff(ctx):
            return
        channel = await self._ticket_channel(ctx, ticket)
        if channel is None:
            return
        try:
            await self.service.add_member(channel, member)
        except TicketError as exc:
            await self._respond(ctx, error_embed(str(exc)))
            return
        await self._respond(
            ctx, base_embed(f"Added {member.mention} to the ticket.", colour=Brand.SUCCESS)
        )

    @ticket.command(name="remove", description="Remove a member's access to this ticket.")
    @app_commands.describe(member="Who to remove")
    @commands.guild_only()
    async def ticket_remove(self, ctx: commands.Context, member: discord.Member) -> None:
        ticket = await self._current_ticket(ctx)
        if ticket is None:
            await self._respond(ctx, error_embed("Run this command inside a ticket channel."))
            return
        if not await self._require_staff(ctx):
            return
        channel = await self._ticket_channel(ctx, ticket)
        if channel is None:
            return
        try:
            await self.service.remove_member(channel, member, opener_id=ticket.user_id)
        except TicketError as exc:
            await self._respond(ctx, error_embed(str(exc)))
            return
        await self._respond(
            ctx, base_embed(f"Removed {member.mention} from the ticket.", colour=Brand.SUCCESS)
        )

    @ticket.command(name="rename", description="Rename this ticket channel.")
    @app_commands.describe(name="New channel name")
    @commands.guild_only()
    async def ticket_rename(self, ctx: commands.Context, *, name: str) -> None:
        ticket = await self._current_ticket(ctx)
        if ticket is None:
            await self._respond(ctx, error_embed("Run this command inside a ticket channel."))
            return
        if not await self._require_staff(ctx):
            return
        channel = await self._ticket_channel(ctx, ticket)
        if channel is None:
            return
        try:
            cleaned = await self.service.rename(channel, name)
        except TicketError as exc:
            await self._respond(ctx, error_embed(str(exc)))
            return
        await self._respond(ctx, base_embed(f"Renamed to `{cleaned}`.", colour=Brand.SUCCESS))

    @ticket.command(name="transcript", description="Archive this ticket and send yourself the HTML file.")
    @commands.guild_only()
    async def ticket_transcript(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        assert guild is not None
        ticket = await self._current_ticket(ctx)
        if ticket is None:
            await self._respond(ctx, error_embed("Run this command inside a ticket channel."))
            return
        if not await self._require_staff(ctx):
            return
        channel = await self._ticket_channel(ctx, ticket)
        if channel is None:
            return
        await defer_if_interaction(ctx)
        try:
            path = await self.service.export(guild, ticket, channel)
        except discord.HTTPException as exc:
            await self._respond(ctx, error_embed(f"Could not read the history (`{exc.status}`)."))
            return
        embed = base_embed(
            f"📄 Transcript of ticket **#{ticket.number:04d}** ({path.name}).", colour=Brand.INFO
        )
        interaction = ctx.interaction
        try:
            if interaction is not None:
                await interaction.followup.send(
                    embed=embed, file=discord.File(path, filename=path.name), ephemeral=True
                )
            else:
                await ctx.reply(
                    embed=embed,
                    file=discord.File(path, filename=path.name),
                    mention_author=False,
                )
        except discord.HTTPException:
            pass
        await self.service.log_embed(
            guild,
            base_embed(
                f"Transcript of ticket **#{ticket.number:04d}** exported by {ctx.author.mention}.",
                title="📄 Ticket transcript",
                colour=Brand.INFO,
            ),
        )

    @ticket.command(name="info", description="Show who opened and claimed this ticket.")
    @commands.guild_only()
    async def ticket_info(self, ctx: commands.Context) -> None:
        ticket = await self._current_ticket(ctx)
        if ticket is None:
            await self._respond(ctx, error_embed("Run this command inside a ticket channel."))
            return
        if not await self._require_staff(ctx):
            return
        embed = base_embed(
            f"**Subject:** {ticket.subject or '—'}\n"
            f"**Opened by:** <@{ticket.user_id}>\n"
            f"**Claimed by:** {f'<@{ticket.claimed_by}>' if ticket.claimed_by else '—'}\n"
            f"**Created:** {discord.utils.format_dt(ticket.created_at, 'R')}\n"
            f"**Status:** {ticket.status}",
            title=f"🎫 Ticket #{ticket.number:04d}",
            colour=Brand.PRIMARY if ticket.is_open else Brand.MUTED,
        )
        if ticket.closed_at is not None:
            embed.add_field(
                name="Closed",
                value=f"{discord.utils.format_dt(ticket.closed_at, 'R')} by <@{ticket.closed_by}>",
                inline=False,
            )
        await self._respond(ctx, embed, ephemeral=False)

    @ticket.command(name="list", description="List recent tickets (open or closed).")
    @app_commands.describe(status="open or closed (default: everything)")
    @commands.has_guild_permissions(manage_guild=True)
    async def ticket_list(self, ctx: commands.Context, status: str | None = None) -> None:
        state = (status or "").strip().lower() or None
        if state not in (None, "open", "closed"):
            await self._respond(ctx, error_embed("Status must be `open` or `closed`."))
            return
        tickets = await self.db.list_tickets(ctx.guild.id, status=state, limit=20)
        if not tickets:
            await self._respond(ctx, base_embed("No tickets yet.", colour=Brand.MUTED))
            return
        embed = base_embed(
            "\n".join(summary_lines(tickets)),
            title=f"🎫 Tickets{' · ' + state if state else ''}",
            colour=Brand.PRIMARY,
        )
        await self._respond(ctx, embed, ephemeral=False)

    @ticket.command(name="stats", description="Ticket counters for this server.")
    @commands.has_guild_permissions(manage_guild=True)
    async def ticket_stats(self, ctx: commands.Context) -> None:
        stats = await self.db.ticket_stats(ctx.guild.id)
        recent = await self.db.list_tickets(ctx.guild.id, limit=5)
        embed = base_embed(
            f"Open: **{stats['open']}** · Closed: **{stats['closed']}** · Total: **{stats['total']}**",
            title="📊 Ticket stats",
            colour=Brand.INFO,
        )
        if recent:
            embed.add_field(name="Latest", value="\n".join(summary_lines(recent)), inline=False)
        await self._respond(ctx, embed, ephemeral=False)


async def setup(bot: Vantab) -> None:
    await bot.add_cog(Tickets(bot))
