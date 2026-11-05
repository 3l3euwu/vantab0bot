"""Ticket panel, opening modal and in-channel controls."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from ..tickets import panel_embed

if TYPE_CHECKING:  # pragma: no cover
    from ..bot import Vantab

__all__ = ["TicketControlsView", "TicketModal", "TicketPanelView"]

log = logging.getLogger("vantab.ui.tickets")


class TicketModal(discord.ui.Modal, title="Open a ticket"):
    """Asks for a subject before the private channel is created."""

    subject = discord.ui.TextInput(
        label="Subject",
        placeholder="What do you need help with?",
        max_length=200,
        required=True,
    )
    details = discord.ui.TextInput(
        label="Details (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="Anything the staff should know before replying…",
        max_length=1000,
        required=False,
    )

    def __init__(self, bot: Vantab) -> None:
        super().__init__(timeout=600)
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("Tickets")
        if cog is None:  # pragma: no cover - cog unloaded
            await interaction.response.send_message(
                "Tickets are unavailable right now.", ephemeral=True
            )
            return
        await cog.create_ticket(  # type: ignore[attr-defined]
            interaction,
            subject=str(self.subject).strip(),
            details=str(self.details or "").strip(),
        )


class TicketPanelView(discord.ui.View):
    """Persistent panel posted by ``~ticket panel``."""

    def __init__(self, bot: Vantab) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Open a ticket",
        emoji="🎫",
        style=discord.ButtonStyle.success,
        custom_id="vantab:ticket:open",
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = self.bot.get_cog("Tickets")
        if cog is None:  # pragma: no cover - cog unloaded
            await interaction.response.send_message(
                "Tickets are unavailable right now.", ephemeral=True
            )
            return
        await cog.prompt_ticket(interaction)  # type: ignore[attr-defined]


class TicketControlsView(discord.ui.View):
    """Buttons living inside a ticket channel (survive restarts)."""

    def __init__(self, bot: Vantab) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    async def _dispatch(self, interaction: discord.Interaction, action: str) -> None:
        cog = self.bot.get_cog("Tickets")
        if cog is None:  # pragma: no cover - cog unloaded
            await interaction.response.send_message(
                "Tickets are unavailable right now.", ephemeral=True
            )
            return
        await cog.button_action(interaction, action)  # type: ignore[attr-defined]

    @discord.ui.button(
        label="Claim", emoji="🙋", style=discord.ButtonStyle.primary, custom_id="vantab:ticket:claim"
    )
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._dispatch(interaction, "claim")

    @discord.ui.button(
        label="Close", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="vantab:ticket:close"
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._dispatch(interaction, "close")

    @discord.ui.button(
        label="Transcript",
        emoji="📄",
        style=discord.ButtonStyle.secondary,
        custom_id="vantab:ticket:transcript",
    )
    async def transcript(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._dispatch(interaction, "transcript")

    @discord.ui.button(
        label="Delete", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="vantab:ticket:delete"
    )
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._dispatch(interaction, "delete")


def build_panel(guild: discord.Guild | None, *, staff_role: discord.Role | None = None) -> discord.Embed:
    """Backwards-friendly alias used by the cog."""
    return panel_embed(guild, staff_role=staff_role)
