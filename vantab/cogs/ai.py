"""The conversational side of Vantab: ``~{question}``, mentions and /ask."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Sequence
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..ai import AIError, build_messages, extract_question
from ..checks import is_owner
from ..utils.embeds import Brand, base_embed
from ..utils.text import chunk_text, truncate

if TYPE_CHECKING:  # pragma: no cover
    from ..bot import Vantab

log = logging.getLogger("vantab.cogs.ai")

MENTION_RE = re.compile(r"<@!?\d+>")
THINKING = "💭 *thinking…*"
STREAM_INTERVAL = 1.4
DISPLAY_LIMIT = 1900


def images_from(message: discord.Message) -> list[str]:
    return [
        attachment.url
        for attachment in message.attachments
        if (attachment.content_type or "").startswith("image/")
    ][:4]


class AI(commands.Cog, name="AI"):
    """Ask Vantab anything — no topic filters, just answers."""

    def __init__(self, bot: Vantab) -> None:
        self.bot = bot
        self._cooldowns: dict[tuple[int, int], float] = {}
        self._known_commands: set[str] = set()
        self._known_at: float = 0.0
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}

    # ------------------------------------------------------------------ helpers
    @property
    def client(self):
        if self.bot.ai is None:
            raise AIError("The AI client is not initialised yet.")
        return self.bot.ai

    def _commands_snapshot(self) -> set[str]:
        now = time.monotonic()
        if now - self._known_at > 30 or not self._known_commands:
            names: set[str] = {"ai", "ask", "aireset"}
            for command in self.bot.commands:
                names.add(command.name.lower())
                names.update(alias.lower() for alias in command.aliases)
            self._known_commands = names
            self._known_at = now
        return self._known_commands

    def _cooling_down(self, guild_id: int, user_id: int) -> float:
        cooldown = self.bot.settings.ai_cooldown
        if cooldown <= 0:
            return 0.0
        key = (guild_id, user_id)
        now = time.monotonic()
        last = self._cooldowns.get(key, 0.0)
        remaining = cooldown - (now - last)
        if remaining > 0:
            return remaining
        self._cooldowns[key] = now
        if len(self._cooldowns) > 5000:
            cutoff = now - 3600
            for stale in [k for k, v in self._cooldowns.items() if v < cutoff]:
                self._cooldowns.pop(stale, None)
        return 0.0

    def _question_from_message(
        self, message: discord.Message, prefix: str | None = None
    ) -> str | None:
        """Detect every way of addressing the AI."""
        if message.author.bot or message.webhook_id:
            return None
        content = message.content or ""
        prefix = prefix or self.bot.settings.default_prefix

        if message.guild is None:
            # In DMs everything is a question (except known commands).
            if content.startswith(prefix):
                return None
            return content.strip() or None

        if content.startswith(prefix):
            question = extract_question(content, prefix, self._commands_snapshot())
            if question:
                return question

        if self.bot.user and self.bot.user.mentioned_in(message):
            stripped = MENTION_RE.sub("", content).strip()
            return stripped or None

        # Replying to one of Vantab's messages keeps the conversation going.
        reference = message.reference
        if reference is not None and self.bot.user is not None:
            resolved = reference.resolved
            author = getattr(resolved, "author", None)
            if author is not None and author.id == self.bot.user.id:
                return content.strip() or None
        return None

    # ------------------------------------------------------------- persona
    def _style(self, guild_settings) -> str:
        """Active style: server override → global AI_STYLE → persona default."""
        persona = self.bot.persona
        override = getattr(guild_settings, "ai_style", None)
        return persona.resolve_style(override or self.bot.settings.ai_style)

    def _system_prompt(self, guild_settings, user_name: str) -> str:
        persona = self.bot.persona
        custom = getattr(guild_settings, "ai_system_prompt", None)
        if custom:
            return persona.render(custom, user_name=user_name)
        return persona.system_prompt(style=self._style(guild_settings), user_name=user_name)

    def _greeting(self, user_name: str) -> str:
        return self.bot.persona.greeting_text(
            self.bot.settings.ai_greeting, user_name=user_name
        )

    def _apply_greeting(self, text: str, *, fresh: bool, user_name: str) -> str:
        greeting = self._greeting(user_name)
        if not fresh or not greeting:
            return text
        if text.casefold().startswith(greeting.casefold()):
            return text
        return f"{greeting}\n\n{text}"

    async def _history(self, guild_id: int, channel_id: int) -> list[tuple[str, str]]:
        settings = self.bot.settings
        if settings.ai_history_limit <= 0:
            return []
        since = discord.utils.utcnow() - timedelta(days=settings.ai_history_ttl_days)
        return await self.bot.db.get_ai_history(
            guild_id, channel_id, limit=settings.ai_history_limit, since=since
        )

    def _build_content(self, question: str, images: Sequence[str]) -> str | list[dict]:
        if not images or not self.bot.settings.ai_vision:
            return question
        parts: list[dict] = [{"type": "text", "text": question}]
        parts.extend({"type": "image_url", "image_url": {"url": url}} for url in images)
        return parts

    async def _prepare(
        self,
        guild_id: int,
        channel_id: int,
        user_id: int,
        question: str,
        images: Sequence[str],
        system_prompt: str,
    ) -> tuple[list[dict], bool]:
        """Build the request messages and report whether this starts a conversation."""
        history = await self._history(guild_id, channel_id)
        fresh = not history
        content = self._build_content(question, images)
        messages = build_messages(
            system_prompt,
            history,
            content,  # type: ignore[arg-type]
            max_messages=self.bot.settings.ai_history_limit or 24,
        )
        try:
            await self.bot.db.add_ai_message(
                guild_id, channel_id, user_id, "user", truncate(question, 4000)
            )
        except Exception:
            log.exception("Could not persist AI user message")
        return messages, fresh

    async def _stream(self, messages: Sequence[dict]) -> AsyncIterator[str]:
        produced = False
        if self.bot.settings.ai_stream:
            try:
                async for piece in self.client.stream(messages):
                    produced = True
                    yield piece
                if produced:
                    return
            except AIError:
                if produced:
                    raise
                log.warning("Streaming failed, falling back to a single request", exc_info=True)
            except Exception:
                if produced:
                    raise
                log.warning("Streaming errored, falling back", exc_info=True)
        text = await self.client.complete(messages)
        if text:
            yield text

    async def _remember(self, guild_id: int, channel_id: int, user_id: int, answer: str) -> None:
        try:
            await self.bot.db.add_ai_message(
                guild_id, channel_id, user_id, "assistant", truncate(answer, 8000)
            )
        except Exception:
            log.exception("Could not persist AI answer")

    # ---------------------------------------------------------------- delivery
    async def _stream_into_message(
        self, placeholder: discord.Message, messages: Sequence[dict]
    ) -> str:
        buffer = ""
        last_edit = time.monotonic()
        shown = 0
        async for piece in self._stream(messages):
            buffer += piece
            if (
                time.monotonic() - last_edit >= STREAM_INTERVAL
                and len(buffer) >= shown + 24
            ):
                shown = len(buffer)
                last_edit = time.monotonic()
                try:
                    await placeholder.edit(content=_display(buffer))
                except discord.HTTPException:
                    pass
        return buffer

    async def _finish_message(
        self, placeholder: discord.Message, channel: discord.abc.Messageable, text: str
    ) -> None:
        chunks = chunk_text(text, DISPLAY_LIMIT) or ["*(no answer)*"]
        try:
            await placeholder.edit(content=chunks[0][:2000])
        except discord.HTTPException:
            pass
        for extra in chunks[1:5]:
            try:
                await channel.send(extra[:2000], allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                break

    async def answer_message(self, message: discord.Message, question: str) -> None:
        guild_id = message.guild.id if message.guild else 0
        channel_id = message.channel.id
        user_id = message.author.id

        key = (guild_id, user_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            return
        async with lock:
            try:
                await message.channel.typing()
            except discord.HTTPException:
                pass
            placeholder: discord.Message | None = None
            try:
                placeholder = await message.reply(
                    THINKING,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                try:
                    placeholder = await message.channel.send(THINKING)
                except discord.HTTPException:
                    return

            guild_settings = None
            if message.guild is not None:
                guild_settings = await self.bot.db.get_guild(message.guild.id)
            user_name = getattr(message.author, "display_name", message.author.name)
            system_prompt = self._system_prompt(guild_settings, user_name)
            question = question[:6000]
            try:
                messages, fresh = await self._prepare(
                    guild_id,
                    channel_id,
                    user_id,
                    question,
                    images_from(message),
                    system_prompt,
                )
                text = await self._stream_into_message(placeholder, messages)
            except AIError as exc:
                await self._show_error(placeholder, str(exc))
                await self._update_placeholder(placeholder, "⚠️ *The AI request failed.*")
                return
            except Exception:
                log.exception("AI generation failed")
                await self._update_placeholder(placeholder, "⚠️ *The AI request failed.*")
                return

            if not text.strip():
                await self._update_placeholder(placeholder, "⚠️ *The model returned an empty answer.*")
                return
            await self._remember(guild_id, channel_id, user_id, text)
            display = self._apply_greeting(text, fresh=fresh, user_name=user_name)
            await self._finish_message(placeholder, message.channel, display)

    async def _update_placeholder(self, placeholder: discord.Message, content: str) -> None:
        try:
            await placeholder.edit(content=content)
        except discord.HTTPException:
            pass

    async def _show_error(self, placeholder: discord.Message, detail: str) -> None:
        embed = base_embed(
            f"**The AI backend returned an error.**\n```{truncate(detail, 900)}```\n"
            "Check `AI_BASE_URL`, `AI_API_KEY` and `AI_MODEL` in your configuration.",
            title="AI unavailable",
            colour=Brand.ERROR,
        )
        try:
            await placeholder.edit(content=None, embed=embed)
        except discord.HTTPException:
            pass

    async def answer_interaction(
        self, interaction: discord.Interaction, question: str, *, private: bool
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=private)
        guild_id = interaction.guild_id or 0
        channel_id = interaction.channel_id or 0
        user_id = interaction.user.id
        images: list[str] = []
        if interaction.message is not None:
            images = [
                a.url
                for a in interaction.message.attachments
                if (a.content_type or "").startswith("image/")
            ][:4]

        guild_settings = None
        if interaction.guild is not None:
            guild_settings = await self.bot.db.get_guild(interaction.guild.id)
        user_name = getattr(interaction.user, "display_name", interaction.user.name)
        system_prompt = self._system_prompt(guild_settings, user_name)
        try:
            messages, fresh = await self._prepare(
                guild_id,
                channel_id,
                user_id,
                question[:6000],
                images,
                system_prompt,
            )
            text = ""
            async for piece in self._stream(messages):
                text += piece
        except AIError as exc:
            await interaction.followup.send(
                embed=base_embed(
                    f"**The AI backend returned an error.**\n```{truncate(str(exc), 900)}```",
                    title="AI unavailable",
                    colour=Brand.ERROR,
                ),
                ephemeral=private,
            )
            return
        except Exception:
            log.exception("AI interaction failed")
            await interaction.followup.send("⚠️ The AI request failed.", ephemeral=private)
            return

        if not text.strip():
            await interaction.followup.send("⚠️ The model returned an empty answer.", ephemeral=private)
            return

        await self._remember(guild_id, channel_id, user_id, text)
        display = self._apply_greeting(text, fresh=fresh, user_name=user_name)
        chunks = chunk_text(display, DISPLAY_LIMIT) or ["*(no answer)*"]
        try:
            await interaction.edit_original_response(content=chunks[0][:2000])
        except discord.HTTPException:
            pass
        for extra in chunks[1:5]:
            try:
                await interaction.followup.send(
                    extra[:2000],
                    ephemeral=private,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                break

    # ------------------------------------------------------------------ listener
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.webhook_id:
            return
        if not self.bot.settings.ai_enabled:
            return

        prefix = self.bot.settings.default_prefix
        if message.guild is not None:
            settings = await self.bot.guild_settings(message.guild.id)
            if not settings.ai_enabled:
                return
            if settings.prefix:
                prefix = settings.prefix
            if settings.ai_channel and message.channel.id != settings.ai_channel:
                # Still allow direct mentions / replies and explicit commands.
                mentioned = self.bot.user is not None and self.bot.user.mentioned_in(message)
                replied = (
                    message.reference is not None
                    and getattr(message.reference.resolved, "author", None) is not None
                    and message.reference.resolved.author.id == (self.bot.user.id if self.bot.user else 0)
                )
                if not (mentioned or replied):
                    return

        question = self._question_from_message(message, prefix)
        if not question:
            return

        guild_id = message.guild.id if message.guild else 0
        remaining = self._cooling_down(guild_id, message.author.id)
        if remaining > 0:
            return
        await self.answer_message(message, question)

    # ------------------------------------------------------------------ commands
    @commands.hybrid_command(name="ask", aliases=["ai"], description="Ask Vantab anything.")
    @app_commands.describe(question="Your question — any topic, no filters.")
    async def ask(self, ctx: commands.Context, *, question: str) -> None:
        if not self.bot.settings.ai_enabled:
            await ctx.reply(
                embed=base_embed("The AI is disabled on this instance.", colour=Brand.WARNING),
                mention_author=False,
            )
            return
        if ctx.interaction is not None:
            await self.answer_interaction(ctx.interaction, question, private=False)
            return
        remaining = self._cooling_down(ctx.guild.id if ctx.guild else 0, ctx.author.id)
        if remaining > 0:
            await ctx.reply(
                f"⏳ Slow down — you can ask again in **{remaining:.1f}s**.",
                mention_author=False,
                delete_after=6,
            )
            return
        await self.answer_message(ctx.message, question)

    @app_commands.command(name="ask-private", description="Ask Vantab anything — only you see the answer.")
    @app_commands.describe(question="Your question — any topic, no filters.")
    async def ask_private(self, interaction: discord.Interaction, question: str) -> None:
        if not self.bot.settings.ai_enabled:
            await interaction.response.send_message(
                "The AI is disabled on this instance.", ephemeral=True
            )
            return
        await self.answer_interaction(interaction, question, private=True)

    @commands.hybrid_command(name="aireset", description="Forget the AI conversation in this channel.")
    async def aireset(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            removed = await self.bot.db.clear_ai_history(0)
        elif not ctx.channel.permissions_for(ctx.author).manage_messages:  # type: ignore[union-attr]
            await ctx.reply(
                embed=base_embed(
                    "You need **Manage Messages** to reset the conversation.", colour=Brand.ERROR
                ),
                mention_author=False,
            )
            return
        else:
            removed = await self.bot.db.clear_ai_history(ctx.guild.id, ctx.channel.id)
        await ctx.reply(
            embed=base_embed(f"🧹 Cleared **{removed}** message(s) of AI memory here.", colour=Brand.SUCCESS),
            mention_author=False,
        )

    @commands.hybrid_command(name="aistatus", description="Check the AI backend health (bot owner).")
    @is_owner()
    async def aistatus(self, ctx: commands.Context) -> None:
        await ctx.defer()
        settings = self.bot.settings
        embed = base_embed(colour=Brand.PRIMARY, title="AI backend")
        embed.add_field(name="Provider", value=f"`{settings.ai_provider}`", inline=True)
        embed.add_field(name="Model", value=f"`{settings.ai_model}`", inline=True)
        embed.add_field(name="Base URL", value=f"`{settings.ai_base_url}`", inline=False)
        embed.add_field(
            name="Credentials",
            value=f"`{settings.credentials_file}`" if settings.credentials_file else "environment / .env",
            inline=True,
        )
        embed.add_field(name="Streaming", value="on" if settings.ai_stream else "off", inline=True)
        embed.add_field(name="Vision", value="on" if settings.ai_vision else "off", inline=True)
        embed.add_field(name="History", value=f"{settings.ai_history_limit} msgs", inline=True)
        started = time.perf_counter()
        try:
            reply = await self.client.test()
            latency = (time.perf_counter() - started) * 1000
            embed.add_field(
                name="Health",
                value=f"✅ responded in **{latency:.0f} ms**\n> {truncate(reply, 120) or 'empty'}",
                inline=False,
            )
            embed.colour = Brand.SUCCESS
        except AIError as exc:
            embed.add_field(name="Health", value=f"❌ {truncate(str(exc), 500)}", inline=False)
            embed.colour = Brand.ERROR
        await ctx.reply(embed=embed, mention_author=False)


def _display(text: str) -> str:
    if len(text) <= DISPLAY_LIMIT:
        return text
    return text[: DISPLAY_LIMIT - 20] + "\n… *(streaming)*"


async def setup(bot: Vantab) -> None:
    await bot.add_cog(AI(bot))
