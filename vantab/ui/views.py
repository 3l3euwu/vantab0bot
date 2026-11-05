"""Buttons, confirmations and the persistent verification gate."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import discord

from ..utils.embeds import Brand, base_embed

if TYPE_CHECKING:  # pragma: no cover
    from ..bot import Vantab

__all__ = ["ConfirmView", "Paginator", "VerifyView"]

log = logging.getLogger("vantab.ui")


class ConfirmView(discord.ui.View):
    """A two-button confirmation bound to the invoking user."""

    def __init__(self, author_id: int, *, timeout: float = 60.0, confirm_label: str = "Confirm") -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: bool | None = None
        self.interaction: discord.Interaction | None = None
        self.confirm.label = confirm_label

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This confirmation isn't for you.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, emoji="✔️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = True
        self.interaction = interaction
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.value = False
        self.interaction = interaction
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self) -> None:
        self.value = None
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]


class Paginator(discord.ui.View):
    """Page through a list of embeds."""

    def __init__(self, embeds: Sequence[discord.Embed], author_id: int, *, timeout: float = 120.0) -> None:
        super().__init__(timeout=timeout)
        if not embeds:
            raise ValueError("Paginator requires at least one embed")
        self.embeds = list(embeds)
        self.author_id = author_id
        self.index = 0
        self.message: discord.Message | None = None
        self._sync()

    def _sync(self) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.embeds) - 1
        self.counter.label = f"{self.index + 1}/{len(self.embeds)}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("These pages aren't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = max(0, self.index - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.primary, disabled=True)
    async def counter(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # pragma: no cover
        pass

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = min(len(self.embeds) - 1, self.index + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass

    async def start(self, destination: discord.abc.Messageable) -> discord.Message:
        self.message = await destination.send(embed=self.embeds[0], view=self)
        return self.message


class VerifyView(discord.ui.View):
    """Persistent one-click verification gate."""

    def __init__(self, bot: Vantab) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="vantab:verify",
    )
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Verification only works in a server.", ephemeral=True)
            return
        settings = await self.bot.db.get_guild(interaction.guild.id)
        if not settings.verify_role_id:
            await interaction.response.send_message(
                "Verification is not configured on this server.", ephemeral=True
            )
            return
        role = interaction.guild.get_role(settings.verify_role_id)
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if role is None:
            await interaction.response.send_message(
                "The verification role no longer exists — tell an admin.", ephemeral=True
            )
            return
        if member is None:
            await interaction.response.send_message("Could not resolve your member object.", ephemeral=True)
            return
        if role in member.roles:
            await interaction.response.send_message("You are already verified.", ephemeral=True)
            return
        if not interaction.guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "I cannot assign roles right now — please contact staff.", ephemeral=True
            )
            return
        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                "My role is below the verification role, so I can't assign it.", ephemeral=True
            )
            return
        try:
            await member.add_roles(role, reason="Vantab verification")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Discord refused to give you the role — contact staff.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"✅ Verified — welcome to **{interaction.guild.name}**!", ephemeral=True
        )
        log.info("Verified %s (%s) in %s", member, member.id, interaction.guild.id)

    @staticmethod
    def build_embed(guild: discord.Guild) -> discord.Embed:
        return base_embed(
            f"Welcome to **{guild.name}**!\n\n"
            "Tap the **Verify** button below to unlock the server.\n"
            "This confirms you are human and keeps raids out.",
            title="Verification",
            colour=Brand.PRIMARY,
        )
