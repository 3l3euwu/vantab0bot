# Vantab · command reference

Prefix: `~` (configurable per server with `~setprefix`). Most commands are
**hybrid**: they work both as `~command` and as `/command`, and appear in
Discord's slash-command picker.

Arguments in `<angle brackets>` are required, `[square brackets]` optional.
`<duration>` accepts `30s`, `10m`, `2h`, `1d`, `1w`, `1h30m` or a bare number
(minutes, seconds for slowmode).

---

## 🧠 AI

| Command | Permissions | Description |
| --- | --- | --- |
| `~<question>` / `~{<question>}` | everyone | Ask Vantab anything. `~{why is the sky blue}`, `~ why is the sky blue`, `~why is the sky blue`. |
| `@Vantab <question>` | everyone | Mention the bot to ask. |
| reply to a Vantab message | everyone | Continue the conversation. |
| `~ask <question>` / `/ask` | everyone | Explicit form. |
| `/ask-private <question>` | everyone | Answer visible only to you. |
| `~aireset` | Manage Messages | Forget the AI conversation in this channel. |
| `~aistatus` | bot owner | Model, endpoint, latency and health check. |
| `~persona` | everyone | Show the AI name, greeting, active style and prompt preview. |

DM the bot and every message is treated as a question.

The AI introduces itself as **Vantab0t** (`helper owo`). Its personality, greeting
and available styles live in `prompts/persona.json`; the shipped default style
`elee` writes in lowercase with `~` endings, kaomoji and `*action*` emotes, and
starts a new conversation with `hewoooooo~ cha vaaa~ ? :3`. Placeholders
`{bot}`, `{user}`, `{nom_utilisateur}`, `{discord user}`, `{role}` and
`{salutation}` are replaced at runtime.

---

## 🔨 Moderation

| Command | Permissions | Description |
| --- | --- | --- |
| `~ban <user> [reason]` | Ban Members | Ban and open a case. Add `--purge <days>` to wipe recent messages. |
| `~unban <user> [reason]` | Ban Members | Lift a ban. |
| `~tempban <user> <duration> [reason]` | Ban Members | Ban for a limited time; lifted automatically when it expires. |
| `~banlist [limit]` | Ban Members | Recent bans with reasons. |
| `~softban <user> [reason]` | Ban Members | Ban + unban to purge 7 days of messages. |
| `~kick <member> [reason]` | Kick Members | Kick and open a case. |
| `~timeout <member> [duration] [reason]` | Moderate Members | Alias: `mute`, `tempmute`. |
| `~untimeout <member> [reason]` | Moderate Members | Alias: `unmute`. |
| `~warn <member> <reason>` | Manage Messages | Warn, record a case, apply the escalation ladder. |
| `~warnings <member>` | Manage Messages | List active warnings (aliases: `warns`). |
| `~delwarn <id>` | Manage Messages | Delete one warning. |
| `~clearwarns <member>` | Administrator | Remove every warning. |
| `~purge <amount> [member] [contains]` | Manage Messages | Bulk delete 1–1000 messages (alias: `clear`). |
| `~slowmode [duration]` | Manage Channels | Set channel slowmode (`off` to disable). |
| `~lock [channel]` | Manage Channels | Prevent members from talking. |
| `~unlock [channel]` | Manage Channels | Undo `~lock`. |
| `~lockdown` | Administrator | Lock every text channel. |
| `~unlockdown` | Administrator | Restore channels locked by lockdown. |
| `~nuke [channel]` | Administrator | Clone + delete a channel after confirmation. |
| `~case <number>` | Manage Messages | Show a case. |
| `~cases <user>` | Manage Messages | Moderation history (alias: `history`). |
| `~reason <number> <reason>` | Manage Messages | Edit a case reason. |
| `~modstats` | Manage Messages | Case statistics for the server. |

Every action: creates a numbered case, DMs the member (if enabled in `.env`),
and mirrors to the moderation log channel.

---

## 🛡️ AutoMod

Configuration requires **Manage Server**.

| Command | Description |
| --- | --- |
| `~automod` / `~automod status` | Rule overview and current actions. |
| `~automod rules` | All 16 rules with defaults and thresholds. |
| `~automod enable <rule>` | Enable a rule. |
| `~automod disable <rule>` | Disable a rule. |
| `~automod set <rule> <action> [duration]` | Action: `none`, `delete`, `warn`, `timeout`, `kick`, `ban`. |
| `~automod threshold <rule> <value>` | Trigger threshold (messages, mentions, percent, …). |
| `~automod reset [rule]` | Restore defaults (one rule or all). |
| `~automod test <text>` | Dry-run: shows which rules would fire and the resulting action. |
| `~automod badword add <word>` | Add a banned word. `*` acts as a wildcard; leet-speak is normalised. |
| `~automod badword remove <word>` | Remove a banned word. |
| `~automod badword list` | List banned words. |
| `~automod allow add <domain>` | Allow a domain through the `links` rule. |
| `~automod allow remove <domain>` | Remove it. |
| `~automod allow list` | List allowed domains. |
| `~automod exempt add <channel\|role\|user> <mention\|id>` | Bypass auto-mod. |
| `~automod exempt remove <channel\|role\|user> <mention\|id>` | Remove an exemption. |
| `~automod exempt list` | List exemptions. |
| `~automod escalation` | Show the warning ladder. |
| `~automod escalation set <warns> <action> [duration]` | Add/replace a step (`timeout`, `kick`, `ban`). |
| `~automod escalation reset` | Restore `3→timeout, 5→kick, 7→ban`. |

### Rules

`spam`, `duplicate`, `crosschannel`, `mention_spam`, `everyone`, `invites`,
`links`, `phishing`, `scam`, `badwords`, `caps`, `zalgo`, `emoji`, `newlines`,
`attachments`, `sensitive`.

---

## 🔐 Security

| Command | Permissions | Description |
| --- | --- | --- |
| `~security` | Manage Server | Overview: anti-nuke, raid mode, verification, permissions. |
| `~antinuke` / `~antinuke status` | Administrator | Status, limits, whitelist. |
| `~antinuke enable` / `disable` | Administrator | Toggle protection. |
| `~antinuke whitelist add <user>` | Administrator | Trust an account with destructive permissions. |
| `~antinuke whitelist remove <user>` | Administrator | Untrust. |
| `~antinuke whitelist list` | Administrator | Who is trusted. |
| `~raidmode on` / `off` | Administrator | Manually engage/lift raid mode. |
| `~verify setup <#channel> <@role>` | Administrator | Create the verification gate. |
| `~verify disable` | Administrator | Turn verification off. |
| `~verify status` | Manage Server | Show verification settings. |

Anti-nuke limits (per 20 seconds, per account): 3 bans, 5 kicks, 2 channel
deletions, 2 role deletions, 3 webhook creations, 2 bot additions, 1 prune.
Crossing any limit strips the account's roles and bans it.

---

## 🎫 Tickets

| Command | Permissions | Description |
| --- | --- | --- |
| `~ticket` | everyone | Overview: status, category, staff role, counters. |
| `~ticket panel [#channel]` | Manage Server | Post the panel with the **Open a ticket** button. |
| `~ticket setup [#category] [@staff] [#log]` | Manage Server | Configure where tickets live and who handles them (creates the category if omitted). |
| `~ticket open [subject]` | everyone | Open a ticket without the panel (one per member by default). |
| `~ticket close [reason]` | opener / staff | Archive a transcript, lock the channel and mark the ticket closed. |
| `~ticket claim` / `~ticket unclaim` | staff | Take or release ownership of the ticket. |
| `~ticket add <member>` / `~ticket remove <member>` | staff | Give or revoke access for another member. |
| `~ticket rename <name>` | staff | Rename the ticket channel. |
| `~ticket transcript` | staff | Send yourself the HTML transcript (kept on disk too). |
| `~ticket info` | staff | Opener, claimer, subject, timestamps. |
| `~ticket list [open\|closed]` | Manage Server | Recent tickets with channel links. |
| `~ticket stats` | Manage Server | Open / closed / total counters. |
| `~ticket max <1-5>` | Manage Server | How many open tickets one member may have. |
| `~ticket enable` / `~ticket disable` | Manage Server | Allow or block new tickets (existing ones keep working). |

Ticket channels also carry persistent **Claim**, **Close**, **Transcript** and
**Delete** buttons. Transcripts are HTML files saved under
`data/transcripts/guild-<id>/`, mirrored to the ticket log channel and DM'd to
the opener.

---

## ⚙️ Settings

| Command | Permissions | Description |
| --- | --- | --- |
| `~config` | everyone | Show the server configuration (alias: `settings`). |
| `~setprefix <prefix>` | Manage Server | Change the command prefix (1–5 characters). |
| `~setlog [#channel]` | Manage Server | Mod log channel (omit to disable). |
| `~setautomodlog [#channel]` | Manage Server | Dedicated auto-mod log channel. |
| `~setaichannel [#channel]` | Manage Server | Restrict the AI to one channel (omit for everywhere). |
| `~toggleautomod on\|off` | Manage Server | Auto-moderation master switch. |
| `~toggleai on\|off` | Manage Server | AI switch for this server. |
| `~toggleantinuke on\|off` | Manage Server | Anti-nuke switch. |
| `~setstyle <style>` | Manage Server | Switch the AI style (`elee`, `normal`, …). |
| `~setaisystem <prompt>` | Manage Server | Custom AI persona (`reset` to restore). |
| `~setwarnedecay <days>` | Manage Server | Warning expiry (1–365 days). |
| `~exportconfig` | Manage Server | Download the configuration as JSON. |

---

## 🧰 Utility

| Command | Description |
| --- | --- |
| `~help [command]` | Interactive help menu / command details. |
| `~ping` | WebSocket, round-trip and database latency. |
| `~botinfo` | Servers, members, uptime, model, versions. |
| `~uptime` | Time since start-up. |
| `~invite` | Pre-filled invite link. |
| `~userinfo [member]` | Account, join date, roles, key permissions (alias: `whois`). |
| `~avatar [member]` | Full-size avatar. |
| `~banner [user]` | Profile banner. |
| `~serverinfo` | Guild details (alias: `guildinfo`). |
| `~membercount` | Humans / bots breakdown. |
| `~roleinfo <role>` | Role details and dangerous permissions. |
| `~tag <name>` | Show a saved tag. |
| `~tagadd <name> <content>` | Create/overwrite a tag (Manage Messages). |
| `~tagremove <name>` | Delete a tag (Manage Messages). |
| `~taglist` | List tags. |

---

## Permission summary

| Level | Commands |
| --- | --- |
| Everyone | AI, `help`, `ping`, `botinfo`, `userinfo`, `avatar`, `tag`, `taglist`, `config` |
| Manage Messages | `warn`, `warnings`, `delwarn`, `purge`, `cases`, `case`, `reason`, `modstats`, tag management |
| Moderate Members | `timeout`, `untimeout` |
| Kick / Ban Members | `kick`, `ban`, `unban`, `softban` |
| Manage Channels | `slowmode`, `lock`, `unlock` |
| Administrator | `clearwarns`, `lockdown`, `unlockdown`, `nuke`, anti-nuke, verification |
| Manage Server | `ticket setup`, `ticket panel`, `ticket list`, `ticket stats`, `ticket max`, `ticket enable/disable` |
| Ticket staff | `ticket claim/unclaim`, `ticket add/remove`, `ticket rename`, `ticket transcript`, `ticket info` |
| Bot owner | `aistatus` |
