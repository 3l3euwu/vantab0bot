"""Configure and inspect the autonomous auto-moderation system."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..automod.rules import RULES, SEVERITY, MessageContext, evaluate, rule_config_dict
from ..checks import error_embed
from ..database import DEFAULT_ESCALATION
from ..utils.duration import DurationError, format_seconds, parse_duration
from ..utils.embeds import Brand, base_embed

if TYPE_CHECKING:  # pragma: no cover
    from ..bot import Vantab

log = logging.getLogger("vantab.cogs.automod_config")

VALID_ACTIONS = ("none", "delete", "warn", "timeout", "kick", "ban")

#: Human wording for why someone is not filtered.
EXEMPTION_LABELS = {
    "owner": "you are the server owner or a bot owner",
    "moderator": "you have Administrator / Manage Messages / Manage Server",
    "channel-exempt": "this channel is exempt",
    "user-exempt": "you are exempt",
    "role-exempt": "one of your roles is exempt",
    "bot": "bots are never filtered",
    "webhook": "webhooks are never filtered",
}


def config_only(func: Callable) -> Callable:
    """Guild-only + Manage Server, applied to every automod command."""
    func = commands.guild_only()(func)
    func = commands.has_guild_permissions(manage_guild=True)(func)
    return func


async def rule_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    current = current.lower()
    return [
        app_commands.Choice(name=f"{name} · {spec.label}", value=name)
        for name, spec in RULES.items()
        if current in name or current in spec.label.lower()
    ][:25]


async def action_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=action, value=action)
        for action in VALID_ACTIONS
        if current.lower() in action
    ][:25]


class AutoModConfig(commands.Cog, name="AutoMod"):
    """Tune every auto-moderation rule without touching the code."""

    def __init__(self, bot: Vantab) -> None:
        self.bot = bot

    async def _config(self, ctx: commands.Context):
        guild = ctx.guild
        assert guild is not None
        self.bot.automod.invalidate(guild.id)
        self.bot.invalidate_settings(guild.id)
        return await self.bot.automod.config(guild.id, force=True)

    # ------------------------------------------------------------------- status
    @commands.hybrid_group(
        name="automod",
        description="Show or change the auto-moderation configuration.",
        invoke_without_command=True,
    )
    @config_only
    async def automod(self, ctx: commands.Context) -> None:
        await self._status(ctx)

    async def _status(self, ctx: commands.Context) -> None:
        config = await self._config(ctx)
        embed = base_embed(
            f"Auto-moderation is **{'enabled' if config.settings.automod_enabled else 'disabled'}**.\n"
            f"Log channel: {ctx.guild.get_channel(config.settings.automod_log_channel).mention if config.settings.automod_log_channel and ctx.guild.get_channel(config.settings.automod_log_channel) else '—'}",
            title="🛡️ AutoMod",
            colour=Brand.SUCCESS if config.settings.automod_enabled else Brand.MUTED,
        )
        lines = []
        for name, spec in RULES.items():
            rule = config.rules.get(name)
            enabled = rule.enabled if rule else spec.enabled_by_default
            action = rule.action if rule else spec.default_action
            if not enabled:
                lines.append(f"⚪ `{name}` — off")
            else:
                emoji = {"delete": "🗑️", "warn": "⚠️", "timeout": "🔇", "kick": "👢", "ban": "🔨", "none": "👀"}.get(action, "•")
                lines.append(f"{emoji} `{name}` → **{action}**")
        embed.add_field(name="Rules", value="\n".join(lines), inline=False)
        embed.add_field(
            name="Exemptions",
            value=(
                f"channels: {len(config.exemptions.get('channel', set()))} · "
                f"roles: {len(config.exemptions.get('role', set()))} · "
                f"users: {len(config.exemptions.get('user', set()))}"
            ),
            inline=True,
        )
        embed.add_field(name="Banned words", value=str(len(config.badwords)), inline=True)
        embed.add_field(name="Allowed domains", value=str(len(config.allowlist)), inline=True)
        exempt = self._exemption(ctx, config)
        if exempt:
            embed.description = (
                f"{embed.description}\n\n"
                f"⚠️ **You bypass auto-moderation here** ({EXEMPTION_LABELS.get(exempt, exempt)}). "
                "Your own messages are never checked — preview a rule with `~automod test <text>`, "
                "or try from an account without moderator permissions."
            )
        embed.set_footer(text="~automod rules · ~automod set <rule> <action> · ~automod test <text>")
        await ctx.reply(embed=embed, mention_author=False)

    @automod.command(name="status", description="Show a summary of every rule.")
    @config_only
    async def automod_status(self, ctx: commands.Context) -> None:
        await self._status(ctx)

    @automod.command(name="rules", description="List every rule with its defaults.")
    @config_only
    async def automod_rules(self, ctx: commands.Context) -> None:
        embed = base_embed(
            "Use `~automod set <rule> <action> [duration]` to change the response.",
            title="📜 Available rules",
            colour=Brand.PRIMARY,
        )
        for name, spec in RULES.items():
            embed.add_field(
                name=f"`{name}` · {spec.label}",
                value=(
                    f"{spec.description}\n"
                    f"default: **{spec.default_action}**"
                    + (f" · threshold: {spec.default_threshold}" if spec.default_threshold else "")
                    + (f" · {spec.threshold_hint}" if spec.threshold_hint else "")
                ),
                inline=False,
            )
        await ctx.reply(embed=embed, mention_author=False)

    @automod.command(name="enable", description="Enable a rule.")
    @app_commands.describe(rule="Rule name")
    @app_commands.autocomplete(rule=rule_autocomplete)
    @config_only
    async def automod_enable(self, ctx: commands.Context, rule: str) -> None:
        await self._toggle(ctx, rule, True)

    @automod.command(name="disable", description="Disable a rule.")
    @app_commands.describe(rule="Rule name")
    @app_commands.autocomplete(rule=rule_autocomplete)
    @config_only
    async def automod_disable(self, ctx: commands.Context, rule: str) -> None:
        await self._toggle(ctx, rule, False)

    async def _toggle(self, ctx: commands.Context, rule: str, enabled: bool) -> None:
        rule = rule.lower().strip()
        if rule not in RULES:
            await ctx.reply(embed=error_embed(f"Unknown rule `{rule}`. Try `~automod rules`."), mention_author=False)
            return
        spec = RULES[rule]
        await self.bot.db.set_rule(ctx.guild.id, rule, enabled=enabled)
        self.bot.automod.invalidate(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(
                f"Rule **{rule}** ({spec.label}) is now **{'enabled' if enabled else 'disabled'}**.",
                colour=Brand.SUCCESS if enabled else Brand.MUTED,
            ),
            mention_author=False,
        )

    @automod.command(name="set", description="Set the action (and duration) for a rule.")
    @app_commands.describe(rule="Rule name", action="none/delete/warn/timeout/kick/ban", duration="e.g. 10m or 2h (timeout/ban)")
    @app_commands.autocomplete(rule=rule_autocomplete, action=action_autocomplete)
    @config_only
    async def automod_set(
        self, ctx: commands.Context, rule: str, action: str, duration: str = "0"
    ) -> None:
        rule = rule.lower().strip()
        action = action.lower().strip()
        if rule not in RULES:
            await ctx.reply(embed=error_embed(f"Unknown rule `{rule}`. Try `~automod rules`."), mention_author=False)
            return
        if action not in VALID_ACTIONS:
            await ctx.reply(
                embed=error_embed(f"Action must be one of: {', '.join(f'`{a}`' for a in VALID_ACTIONS)}."),
                mention_author=False,
            )
            return
        seconds = 0
        if duration not in {"0", "off", "none"}:
            try:
                seconds = int(parse_duration(duration, default_unit="m").total_seconds())
            except DurationError:
                await ctx.reply(embed=error_embed("Invalid duration. Examples: `10m`, `2h`, `1d`."), mention_author=False)
                return
        await self.bot.db.set_rule(ctx.guild.id, rule, action=action, duration=seconds, enabled=True)
        self.bot.automod.invalidate(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(
                f"`{rule}` now triggers **{action}**"
                + (f" for **{format_seconds(seconds)}**" if seconds else "")
                + ".",
                colour=Brand.SUCCESS,
            ),
            mention_author=False,
        )

    @automod.command(name="threshold", description="Set the numeric threshold for a rule.")
    @app_commands.describe(rule="Rule name", value="New threshold (messages, mentions, percent…)")
    @app_commands.autocomplete(rule=rule_autocomplete)
    @config_only
    async def automod_threshold(self, ctx: commands.Context, rule: str, value: int) -> None:
        rule = rule.lower().strip()
        if rule not in RULES:
            await ctx.reply(embed=error_embed(f"Unknown rule `{rule}`."), mention_author=False)
            return
        if not 0 < value <= 1000:
            await ctx.reply(embed=error_embed("Threshold must be between **1** and **1000**."), mention_author=False)
            return
        await self.bot.db.set_rule(ctx.guild.id, rule, threshold=value)
        self.bot.automod.invalidate(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(f"`{rule}` threshold set to **{value}**.", colour=Brand.SUCCESS),
            mention_author=False,
        )

    @automod.command(name="reset", description="Restore a rule's default settings.")
    @app_commands.describe(rule="Rule name (omit for all rules)")
    @app_commands.autocomplete(rule=rule_autocomplete)
    @config_only
    async def automod_reset(self, ctx: commands.Context, rule: str | None = None) -> None:
        if rule:
            rule = rule.lower().strip()
            if rule not in RULES:
                await ctx.reply(embed=error_embed(f"Unknown rule `{rule}`."), mention_author=False)
                return
            await self.bot.db.delete_rule(ctx.guild.id, rule)
            self.bot.automod.invalidate(ctx.guild.id)
            await ctx.reply(
                embed=base_embed(f"`{rule}` reset to defaults.", colour=Brand.SUCCESS),
                mention_author=False,
            )
            return
        for name in RULES:
            await self.bot.db.delete_rule(ctx.guild.id, name)
        self.bot.automod.invalidate(ctx.guild.id)
        await ctx.reply(
            embed=base_embed("All auto-mod rules reset to their defaults.", colour=Brand.SUCCESS),
            mention_author=False,
        )

    @automod.command(name="test", description="Preview which rules would fire on a message.")
    @app_commands.describe(text="The message to test")
    @config_only
    async def automod_test(self, ctx: commands.Context, *, text: str) -> None:
        config = await self._config(ctx)
        fake = MessageContext(
            guild_id=ctx.guild.id,
            channel_id=ctx.channel.id,
            author_id=0,
            content=text,
            created_at=discord.utils.utcnow(),
        )
        detections = evaluate(
            fake,
            rules={name: rule_config_dict(rule) for name, rule in config.rules.items()},
            badwords=config.badwords,
            allowlist=config.allowlist,
            tracker=self.bot.automod.tracker,
        )
        if not detections:
            await ctx.reply(
                embed=base_embed("✅ No rule would trigger on that message.", colour=Brand.SUCCESS),
                mention_author=False,
            )
            return
        embed = base_embed(
            "\n".join(f"• `{d.rule}` — {d.reason}" for d in detections),
            title="🧪 AutoMod preview",
            colour=Brand.WARNING,
        )
        embed.add_field(
            name="Resulting action",
            value=max(
                (self.bot.automod.rule_action(config, d.rule) for d in detections),
                key=lambda a: SEVERITY.get(a, 0),
            ),
            inline=True,
        )
        exempt = self._exemption(ctx, config)
        if exempt:
            embed.set_footer(
                text=(
                    f"Preview only — in a real message you would be exempt "
                    f"({EXEMPTION_LABELS.get(exempt, exempt)})."
                )
            )
        await ctx.reply(embed=embed, mention_author=False)

    # ----------------------------------------------------------------- badwords
    @automod.group(name="badword", description="Manage the banned word list.", invoke_without_command=True)
    @config_only
    async def badword(self, ctx: commands.Context) -> None:
        await self._badword_list(ctx)

    @badword.command(name="add", description="Add a banned word (supports * wildcards).")
    @app_commands.describe(word="Word or pattern, e.g. sl*r")
    @config_only
    async def badword_add(self, ctx: commands.Context, *, word: str) -> None:
        if len(word) > 100:
            await ctx.reply(embed=error_embed("That word is too long."), mention_author=False)
            return
        added = await self.bot.db.add_badword(ctx.guild.id, word)
        self.bot.automod.invalidate(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(
                f"{'Added' if added else 'Already present:'} `{word}` to the banned word list.",
                colour=Brand.SUCCESS if added else Brand.MUTED,
            ),
            mention_author=False,
        )

    @badword.command(name="remove", description="Remove a banned word.")
    @app_commands.describe(word="The word to remove")
    @config_only
    async def badword_remove(self, ctx: commands.Context, *, word: str) -> None:
        removed = await self.bot.db.remove_badword(ctx.guild.id, word)
        self.bot.automod.invalidate(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(
                f"Removed `{word}`." if removed else "That word is not on the list.",
                colour=Brand.SUCCESS if removed else Brand.MUTED,
            ),
            mention_author=False,
        )

    @badword.command(name="list", description="List the banned words.")
    @config_only
    async def badword_list(self, ctx: commands.Context) -> None:
        await self._badword_list(ctx)

    async def _badword_list(self, ctx: commands.Context) -> None:
        words = sorted(await self.bot.db.get_badwords(ctx.guild.id))
        embed = base_embed(
            ", ".join(f"`{w}`" for w in words[:100]) if words else "No banned words yet. Use `~automod badword add <word>`.",
            title=f"🚫 Banned words ({len(words)})",
            colour=Brand.PRIMARY,
        )
        await ctx.reply(embed=embed, mention_author=False)

    # ------------------------------------------------------------------- links
    @automod.group(name="allow", description="Domains the link filter permits.", invoke_without_command=True)
    @config_only
    async def allow(self, ctx: commands.Context) -> None:
        await self._allow_list(ctx)

    @allow.command(name="add", description="Allow a domain through the link filter.")
    @app_commands.describe(domain="e.g. youtube.com")
    @config_only
    async def allow_add(self, ctx: commands.Context, domain: str) -> None:
        try:
            added = await self.bot.db.add_domain(ctx.guild.id, domain)
        except ValueError as exc:
            await ctx.reply(embed=error_embed(str(exc)), mention_author=False)
            return
        self.bot.automod.invalidate(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(
                f"{'Allowed' if added else 'Already allowed:'} `{domain.lower()}`.",
                colour=Brand.SUCCESS if added else Brand.MUTED,
            ),
            mention_author=False,
        )

    @allow.command(name="remove", description="Remove a domain from the allowlist.")
    @app_commands.describe(domain="e.g. youtube.com")
    @config_only
    async def allow_remove(self, ctx: commands.Context, domain: str) -> None:
        removed = await self.bot.db.remove_domain(ctx.guild.id, domain)
        self.bot.automod.invalidate(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(
                f"Removed `{domain.lower()}`." if removed else "That domain was not allowed.",
                colour=Brand.SUCCESS if removed else Brand.MUTED,
            ),
            mention_author=False,
        )

    @allow.command(name="list", description="List allowed domains.")
    @config_only
    async def allow_list(self, ctx: commands.Context) -> None:
        await self._allow_list(ctx)

    async def _allow_list(self, ctx: commands.Context) -> None:
        domains = sorted(await self.bot.db.get_allowlist(ctx.guild.id))
        embed = base_embed(
            ", ".join(f"`{d}`" for d in domains) if domains else "No domains allowlisted. Only relevant when the `links` rule is enabled.",
            title=f"🔗 Allowed domains ({len(domains)})",
            colour=Brand.PRIMARY,
        )
        await ctx.reply(embed=embed, mention_author=False)

    # -------------------------------------------------------------- exemptions
    @automod.group(name="exempt", description="Bypass auto-mod for a channel, role or user.", invoke_without_command=True)
    @config_only
    async def exempt(self, ctx: commands.Context) -> None:
        await self._exempt_list(ctx)

    @exempt.command(name="add", description="Exempt a channel, role or user from auto-mod.")
    @app_commands.describe(kind="channel / role / user", target="Mention, ID or #channel")
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="channel", value="channel"),
            app_commands.Choice(name="role", value="role"),
            app_commands.Choice(name="user", value="user"),
        ]
    )
    @config_only
    async def exempt_add(self, ctx: commands.Context, kind: str, target: str) -> None:
        kind = kind.lower()
        target_id = self._extract_id(target)
        if target_id is None:
            await ctx.reply(embed=error_embed("Give me a mention or an ID."), mention_author=False)
            return
        added = await self.bot.db.add_exemption(ctx.guild.id, kind, target_id)
        self.bot.automod.invalidate(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(
                f"{'Exempted' if added else 'Already exempt:'} {kind} `{target_id}`.",
                colour=Brand.SUCCESS if added else Brand.MUTED,
            ),
            mention_author=False,
        )

    @exempt.command(name="remove", description="Remove an auto-mod exemption.")
    @app_commands.describe(kind="channel / role / user", target="Mention or ID")
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="channel", value="channel"),
            app_commands.Choice(name="role", value="role"),
            app_commands.Choice(name="user", value="user"),
        ]
    )
    @config_only
    async def exempt_remove(self, ctx: commands.Context, kind: str, target: str) -> None:
        kind = kind.lower()
        target_id = self._extract_id(target)
        if target_id is None:
            await ctx.reply(embed=error_embed("Give me a mention or an ID."), mention_author=False)
            return
        removed = await self.bot.db.remove_exemption(ctx.guild.id, kind, target_id)
        self.bot.automod.invalidate(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(
                f"Removed exemption {kind} `{target_id}`." if removed else "No such exemption.",
                colour=Brand.SUCCESS if removed else Brand.MUTED,
            ),
            mention_author=False,
        )

    @exempt.command(name="list", description="List auto-mod exemptions.")
    @config_only
    async def exempt_list(self, ctx: commands.Context) -> None:
        await self._exempt_list(ctx)

    async def _exempt_list(self, ctx: commands.Context) -> None:
        exemptions = await self.bot.db.get_exemptions(ctx.guild.id)
        embed = base_embed(title="🛡️ AutoMod exemptions", colour=Brand.PRIMARY)
        for kind in ("channel", "role", "user"):
            values = exemptions.get(kind, set())
            label = {"channel": "Channels", "role": "Roles", "user": "Users"}[kind]
            mention = {
                "channel": lambda i: f"<#{i}>",
                "role": lambda i: f"<@&{i}>",
                "user": lambda i: f"<@{i}>",
            }[kind]
            embed.add_field(
                name=label,
                value=", ".join(mention(i) for i in sorted(values)) if values else "—",
                inline=False,
            )
        await ctx.reply(embed=embed, mention_author=False)

    @staticmethod
    def _extract_id(raw: str) -> int | None:
        digits = "".join(c for c in raw if c.isdigit())
        return int(digits) if digits else None

    # --------------------------------------------------------------- escalation
    @automod.group(name="escalation", description="The warning ladder.", invoke_without_command=True)
    @config_only
    async def escalation(self, ctx: commands.Context) -> None:
        await self._escalation_show(ctx)

    @escalation.command(name="show", description="Show the warning escalation ladder.")
    @config_only
    async def escalation_show(self, ctx: commands.Context) -> None:
        await self._escalation_show(ctx)

    async def _escalation_show(self, ctx: commands.Context) -> None:
        settings = await self.bot.db.get_guild(ctx.guild.id)
        embed = base_embed(
            "\n".join(
                f"**{step.get('warns')} warnings** → `{step.get('action')}`"
                + (f" for {format_seconds(int(step.get('duration', 0)))}" if step.get("duration") else "")
                for step in settings.escalation
            )
            or "No escalation configured.",
            title="📈 Warning escalation",
            colour=Brand.PRIMARY,
        )
        embed.set_footer(text="~automod escalation set <warns> <action> [duration]")
        await ctx.reply(embed=embed, mention_author=False)

    @escalation.command(name="set", description="Add or replace an escalation step.")
    @app_commands.describe(warns="Number of warnings", action="timeout/kick/ban", duration="Required for timeout")
    @app_commands.autocomplete(action=action_autocomplete)
    @config_only
    async def escalation_set(
        self, ctx: commands.Context, warns: int, action: str, duration: str = "0"
    ) -> None:
        action = action.lower()
        if action not in {"timeout", "kick", "ban"}:
            await ctx.reply(embed=error_embed("Action must be `timeout`, `kick` or `ban`."), mention_author=False)
            return
        if not 1 <= warns <= 100:
            await ctx.reply(embed=error_embed("Warn count must be between 1 and 100."), mention_author=False)
            return
        seconds = 0
        if action == "timeout":
            try:
                seconds = int(parse_duration(duration, default_unit="m").total_seconds())
            except DurationError:
                seconds = 3600
            if seconds <= 0:
                seconds = 3600
        settings = await self.bot.db.get_guild(ctx.guild.id)
        ladder = [step for step in settings.escalation if int(step.get("warns", 0)) != warns]
        ladder.append({"warns": warns, "action": action, "duration": seconds})
        ladder.sort(key=lambda step: int(step["warns"]))
        await self.bot.db.update_guild(ctx.guild.id, escalation=ladder)
        self.bot.invalidate_settings(ctx.guild.id)
        await ctx.reply(
            embed=base_embed(
                f"Escalation updated: **{warns}** warnings → **{action}**"
                + (f" for {format_seconds(seconds)}" if seconds else "")
                + ".",
                colour=Brand.SUCCESS,
            ),
            mention_author=False,
        )

    @escalation.command(name="reset", description="Restore the default escalation ladder.")
    @config_only
    async def escalation_reset(self, ctx: commands.Context) -> None:
        await self.bot.db.update_guild(ctx.guild.id, escalation=[dict(e) for e in DEFAULT_ESCALATION])
        self.bot.invalidate_settings(ctx.guild.id)
        await ctx.reply(
            embed=base_embed("Escalation ladder restored to defaults.", colour=Brand.SUCCESS),
            mention_author=False,
        )


async def setup(bot: Vantab) -> None:
    await bot.add_cog(AutoModConfig(bot))
