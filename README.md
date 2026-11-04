# Vantab

**An autonomous Discord moderation bot with an AI that answers everything.**

Powered by **Gemini 3.1 Flash-Lite** by default (`gemini-3.1-flash-lite`), configured
from a single `tkn.txt` file.

Vantab watches every message, catches spam, raids, scams, phishing links, leaked
secrets and banned words, escalates punishments on repeat offenders, guards the
server against nukers, runs a verification gate — and answers any question you
throw at it with `~{your question}`.

- 🛡️ **16 auto-mod rules**, enabled out of the box, all tunable per server
- 🔨 **Full manual moderation**: ban, kick, timeout, warn, purge, lock, nuke, cases
- 🧠 **Unrestricted AI**: any topic, streaming answers, vision, per-channel memory
- 🎀 **Persona styles**: `elee` (cute, lowercase, kaomoji — the default) or `normal`, editable in `prompts/persona.json`
- 🚨 **Anti-nuke + raid mode** that act on their own, no staff online required
- 🧾 **Audit-grade logging** for messages, members, roles, channels and voice
- 💾 **SQLite persistence** — warnings, cases, config and AI memory survive restarts

---

## Table of contents

1. [Quick start](#quick-start)
2. [The AI (`~{question}`)](#the-ai-question)
3. [What "autonomous" means](#what-autonomous-means)
4. [Auto-mod rules](#auto-mod-rules)
5. [Commands](#commands)
6. [Permissions Vantab needs](#permissions-vantab-needs)
7. [Configuration](#configuration)
8. [Deployment](#deployment)
9. [Development](#development)
10. [Project layout](#project-layout)
11. [Troubleshooting](#troubleshooting)

---

## Quick start

### 1. Create the application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application** → name it **Vantab**.
2. **Bot** → **Reset Token** → copy the token.
3. Still on **Bot**, enable both privileged intents:
   - ✅ **Server Members Intent**
   - ✅ **Message Content Intent**

### 2. Install

```bash
git clone https://github.com/3l3euwu/vantab0bot.git
cd vantab0bot

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure — `tkn.txt`

Vantab reads its credentials from **`tkn.txt`** in the project root:

```bash
cp tkn.example.txt tkn.txt
```

```ini
discord-token=your-discord-bot-token
provider=gemini
model=gemini-3.1-flash-lite
api-key=your-google-ai-studio-key
```

That is all it needs. `tkn.txt` is git-ignored, and its values take precedence
over `.env`. Key aliases are forgiving (`api-key`, `apikey`, `api_key`,
`discord-token`, `bot-token`, `model`, `model-name`, `provider`,
`base-url`, `style`, `greeting`, `temperature`, `max-tokens`, `owner-ids`,
`prefix`), and `#` comments are allowed.

Prefer environment variables? `.env` still works — copy `.env.example` to `.env`
and set `DISCORD_TOKEN`, `AI_API_KEY`, `AI_MODEL` there instead. A bare
single-line `tkn.txt` containing only the Discord token is still accepted for
backwards compatibility.

### 4. Run

```bash
python bot.py
```

You should see `Logged in as Vantab#0000 · N guilds`. Slash commands sync
automatically on start.

### 5. Invite

Use `~invite` to get a pre-filled OAuth link (or build one with the
`bot` + `applications.commands` scopes and the permissions listed
[below](#permissions-vantab-needs)).

Then, in your server:

```
~setlog #mod-logs        # where to send logs
~automod status          # check the protection
~{how do I set up a raid shield?}
```

---

## The AI (`~{question}`)

All of these work:

| You type | What happens |
| --- | --- |
| `~{why is the sky blue}` | Vantab answers |
| `~ what is a quasar` | Vantab answers |
| `~what is a quasar` | Vantab answers (unless `what` is a command name) |
| `@Vantab explain entropy` | Vantab answers |
| reply to a Vantab message | Continues the conversation |
| `/ask question: …` | Slash command equivalent |
| `/ask-private question: …` | Only you see the answer |
| DM the bot | Every message is a question |

Features:

- **No topic filter.** Vantab adds no content policy of its own — the persona
  simply asks the model to answer everything and hold nothing back.
- **Persona & styles.** The AI introduces itself as **Vantab0t** (`helper owo`)
  and ships with two styles, described in [`prompts/persona.json`](prompts/persona.json):

  | Style | Behaviour |
  | --- | --- |
  | `elee` *(default)* | cute girlfriend energy, everything in lowercase, `~` at the end of sentences, lots of kaomoji (`uwu`, `owo`, `iwi`, `:3`, `>.<`), `*action*` emotes when you ask for a hug |
  | `normal` | direct, precise assistant, same "no limits" answer policy |

  Switch per server with `~setstyle normal` (or `/setstyle`), inspect with
  `~persona`. The first message of a new conversation opens with the persona
  greeting — `hewoooooo~ cha vaaa~ ? :3` by default.
- **Placeholders.** In any style or custom prompt, `{bot}`/`{bot_name}` become
  the AI's name, `{user}`/`{nom_utilisateur}`/`{discord user}` become the
  speaker's display name, `{role}` is the short description and `{salutation}`
  is the greeting.
- **Streaming.** Answers appear as they are generated (disable with `AI_STREAM=false`).
- **Memory.** The last N messages per channel are kept in SQLite
  (`AI_HISTORY_LIMIT`, `AI_HISTORY_TTL_DAYS`) and sent as context.
- **Vision.** Attach an image and ask about it (`AI_VISION=true`).
- **Custom persona.** `~setaisystem <prompt>` sets a server-specific system
  prompt (placeholders work there too); `~setaisystem reset` restores the
  persona style.
- **Gemini 3.1 Flash-Lite by default.** Google's OpenAI-compatible endpoint
  (`https://generativelanguage.googleapis.com/v1beta/openai`) with the
  `gemini-3.1-flash-lite` model — 1M-token context, fast and cheap.
- **Any provider.** OpenAI, OpenRouter, Groq, Together, DeepInfra, Mistral,
  Ollama, LM Studio, vLLM, or anything exposing `/v1/chat/completions`.
- **One credential file.** `tkn.txt` holds `discord-token`, `provider`, `model`
  and `api-key`; `~aistatus` shows which file was loaded.
- **Health check.** `~aistatus` (bot owner) shows model, latency and errors.

> **Honest note about "unrestricted":** Vantab itself applies no filters,
> refuses nothing and logs nothing to third parties. The *model provider* still
> enforces its own policy on its own servers — no client-side setting can remove
> that. For genuinely zero-restriction behaviour, point `AI_PROVIDER=ollama` at a
> local model; then nothing leaves your machine.

### Supported providers

| `AI_PROVIDER` | Default base URL | Default model |
| --- | --- | --- |
| `gemini` *(default)* | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-3.1-flash-lite` |
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini` |
| `openrouter` | `https://openrouter.ai/api/v1` | `openai/gpt-4o-mini` |
| `groq` | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| `together` | `https://api.together.xyz/v1` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` |
| `deepinfra` | `https://api.deepinfra.com/v1/openai` | `meta-llama/Llama-3.3-70B-Instruct` |
| `mistral` | `https://api.mistral.ai/v1` | `mistral-large-latest` |
| `ollama` | `http://127.0.0.1:11434/v1` | `llama3.1` |
| `lmstudio` | `http://127.0.0.1:1234/v1` | `local-model` |
| `custom` | whatever `AI_BASE_URL` says | whatever `AI_MODEL` says |

---

## What "autonomous" means

Vantab keeps working when nobody is watching:

| Behaviour | Detail |
| --- | --- |
| **Auto-moderation** | Every message is scored by 16 rules; the configured action (delete → warn → timeout → kick → ban) is applied immediately. |
| **Escalation** | 3 punishments in an hour bumps the response one level; 6 bumps two. Accounts younger than 7 days get escalated faster. |
| **Warning ladder** | `3 warns → 1h timeout`, `5 → kick`, `7 → ban` by default (`~automod escalation`). |
| **Raid mode** | 8 joins in 20 seconds, or 5 members punished in 60 seconds, locks every channel and alerts the owner. `~raidmode off` restores it. |
| **Anti-nuke** | Watches the audit log: 3 bans / 2 channel deletions / 3 webhook creations in 20s from one account → roles stripped and account banned. |
| **Tempban expiry** | Temporary bans are lifted automatically when they expire. |
| **Warning decay** | Warnings stop counting after N days (default 30). |
| **Verification** | One-click role gate; unverified new accounts are kicked after the timeout during raids. |
| **Logging** | Deletes, edits, joins, leaves, nicknames, timeouts, roles, channels and voice moves. |
| **Clean-up** | Old AI memory and security events are pruned automatically. |

Everything is stored in `data/vantab.db`, so restarts do not reset cases,
warnings or configuration.

---

## Auto-mod rules

| Rule | Default | Catches |
| --- | --- | --- |
| `spam` | timeout 5m | > 6 messages in 8s |
| `duplicate` | timeout 5m | same message 3× in 45s |
| `crosschannel` | timeout 15m | same message in 3+ channels in 25s |
| `mention_spam` | timeout 15m | 5+ mentions in one message |
| `everyone` | timeout 15m | unauthorised `@everyone` / `@here` |
| `invites` | delete | `discord.gg/…`, obfuscated invites |
| `links` | delete (off by default) | any link not on the allowlist |
| `phishing` | **ban** | nitro/steam look-alikes, punycode, IP hosts |
| `scam` | timeout 1h | "free nitro", "double your crypto", seed phrases… |
| `badwords` | delete | your list, `*` wildcards, leet-speak aware |
| `caps` | delete | > 70 % capitals (12+ letters) |
| `zalgo` | delete | glitch text / combining-character abuse |
| `emoji` | delete | 12+ emojis in one message |
| `newlines` | delete | 20+ line breaks |
| `attachments` | delete | 5+ attachments, or `.exe`/`.scr`/`.bat`/… |
| `sensitive` | delete | credit cards (Luhn-checked), private keys, tokens |

Tune them without restarting:

```bash
~automod status                       # overview
~automod rules                        # every rule + defaults
~automod disable invites              # turn one off
~automod set spam timeout 10m         # change the response
~automod threshold mention_spam 8     # change the trigger
~automod test free nitro at discord-nitro.com   # dry run
~automod badword add sl*r
~automod allow add youtube.com
~automod exempt add role 123456789012345678
```

Administrators, the server owner, exempt roles/users/channels and bot owners
bypass auto-mod. During **raid mode** that bypass is suspended for everyone but
the owner, so compromised moderator accounts cannot abuse it.

---

## Commands

Prefix: `~` (change per server with `~setprefix`). Every command also exists as a
slash command where Discord allows it. Full reference: **[docs/COMMANDS.md](docs/COMMANDS.md)**.

| Category | Commands |
| --- | --- |
| **AI** | `ask` / `ai`, `/ask-private`, `aireset`, `aistatus` |
| **Moderation** | `ban`, `tempban`, `unban`, `banlist`, `softban`, `kick`, `timeout` / `mute`, `untimeout`, `warn`, `warnings`, `delwarn`, `clearwarns`, `purge`, `slowmode`, `lock`, `unlock`, `lockdown`, `unlockdown`, `nuke`, `case`, `cases`, `reason`, `modstats` |
| **AutoMod** | `automod` + `status`, `rules`, `enable`, `disable`, `set`, `threshold`, `reset`, `test`, `badword`, `allow`, `exempt`, `escalation` |
| **Security** | `antinuke` + `status`, `enable`, `disable`, `whitelist`, `raidmode`, `verify` + `setup`, `disable`, `status`, `security` |
| **Settings** | `config`, `setprefix`, `setlog`, `setautomodlog`, `setaichannel`, `toggleautomod`, `toggleai`, `toggleantinuke`, `setaisystem`, `setstyle`, `persona`, `setwarnedecay`, `exportconfig` |
| **Utility** | `help`, `ping`, `botinfo`, `uptime`, `invite`, `userinfo`, `avatar`, `banner`, `serverinfo`, `membercount`, `roleinfo`, `tag`, `tagadd`, `tagremove`, `taglist` |

Examples:

```bash
~ban @user --purge 3 spamming invites
~tempban @user 7d raiding
~timeout @user 1h chill out
~purge 100 @user
~warn @user rude to members
~cases @user
~lockdown
~verify setup #verify @Member
```

---

## Permissions Vantab needs

Recommended invite permissions (all requested by `~invite`):

| Permission | Used for |
| --- | --- |
| View Channels, Send Messages, Embed Links, Attach Files, Read History | talking and logs |
| Manage Messages | purging, deleting auto-mod hits |
| Manage Channels | lock/unlock, slowmode, nuke, raid mode |
| Manage Roles | verification, anti-nuke role stripping |
| Kick Members / Ban Members / Moderate Members | punishments |
| View Audit Log | anti-nuke |
| Manage Server | configuration commands |

Also **move Vantab's role above the roles it must moderate**, otherwise it
cannot timeout, kick or strip them (it will tell you so).

---

## Configuration

All settings are environment variables (`.env` or the process environment).

| Variable | Default | Description |
| --- | --- | --- |
| `CREDENTIALS_FILE` | `tkn.txt` | File to read `discord-token` / `api-key` / `model` / `provider` from. |
| `DISCORD_TOKEN` | — | **Required** unless set in `tkn.txt`. Bot token. |
| `OWNER_IDS` | — | Comma-separated user IDs allowed to use owner commands. |
| `DEFAULT_PREFIX` | `~` | Default command prefix. |
| `DEV_GUILD_ID` | — | Sync slash commands to one guild instantly. |
| `DATABASE_PATH` | `data/vantab.db` | SQLite file. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `STATUS_TEXT` | `over {guilds} servers · ~{question}` | Bot presence. |
| `ACTIVITY_TYPE` | `watching` | `watching`, `playing`, `listening`, `competing`. |
| `AI_ENABLED` | `true` | Master switch for the AI. |
| `AI_PROVIDER` | `gemini` | See the provider table. |
| `AI_BASE_URL` | provider default | OpenAI-compatible endpoint. |
| `AI_API_KEY` | — | Provider key (not needed for local models). Use `api-key=` in `tkn.txt` instead. |
| `AI_MODEL` | `gemini-3.1-flash-lite` | Model identifier. |
| `AI_TEMPERATURE` | `0.8` | 0 – 2. |
| `AI_MAX_TOKENS` | `1200` | Max response length. |
| `AI_TIMEOUT` | `120` | Request timeout in seconds. |
| `AI_STREAM` | `true` | Stream answers token by token. |
| `AI_VISION` | `true` | Send image attachments to the model. |
| `AI_HISTORY_LIMIT` | `24` | Messages of context per channel. |
| `AI_HISTORY_TTL_DAYS` | `14` | Forget conversations older than this. |
| `AI_COOLDOWN` | `2` | Seconds between AI requests per user. |
| `AI_SYSTEM_PROMPT` | — | Inline system prompt (highest priority). |
| `PERSONA_FILE` | `prompts/persona.json` | Persona, styles and greeting. |
| `AI_STYLE` | file default (`elee`) | Active style. |
| `AI_GREETING` | file `salutation` | Override the opening line. |
| `AI_SYSTEM_PROMPT_FILE` | — | Replace the persona with a raw prompt file. |
| `AI_EXTRA_HEADERS` | — | Extra HTTP headers as JSON (OpenRouter headers are added automatically). |
| `DEFAULT_MUTE_MINUTES` | `10` | Default timeout length. |
| `MAX_TIMEOUT_MINUTES` | `40320` | 28 days (Discord maximum). |
| `DM_ON_PUNISH` | `true` | DM members when they are punished. |
| `ANTINUKE_POLL_SECONDS` | `6` | Audit log scan interval. |

**Never commit `.env` or `tkn.txt`** — both are in `.gitignore`.

---

## Deployment

### Docker

```bash
docker build -t vantab .
docker run -d --name vantab --restart unless-stopped \
  --env-file .env -v "$PWD/data:/app/data" \
  -v "$PWD/tkn.txt:/app/tkn.txt:ro" vantab
```

`tkn.txt` is **not** baked into the image (it is in `.dockerignore`) — mount it
read-only as above, or configure everything through `.env`.

### Docker Compose

```bash
docker compose up -d --build
docker compose logs -f vantab
```

Uncomment the `./tkn.txt:/app/tkn.txt:ro` line in `docker-compose.yml` if you
keep your credentials in `tkn.txt`.

### systemd (VPS)

```bash
sudo cp deploy/vantab.service /etc/systemd/system/vantab.service
sudo nano /etc/systemd/system/vantab.service   # adjust paths/user
sudo systemctl daemon-reload
sudo systemctl enable --now vantab
sudo journalctl -u vantab -f
```

### Railway / Fly.io / Render

- Build from the `Dockerfile` (or use the Nixpacks Python builder).
- Add the environment variables from `.env.example`.
- Attach a volume at `/app/data` so `vantab.db` survives redeploys.

Any platform that can run a long-lived Python process works — Vantab needs no
inbound ports, webhooks or public URL.

---

## Development

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt

python -m pytest -q          # 173 tests
ruff check .                 # lint
python bot.py                # run locally
```

The test-suite covers every auto-mod rule, duration parsing, the database layer,
the AI client (with a fake HTTP session) and a full cog load.

---

## Project layout

```
bot.py                     entry point
prompts/persona.json       AI name, greeting, styles (edit me!)
tkn.example.txt            template for tkn.txt credentials
vantab/
  bot.py                   Vantab client: intents, prefix, errors, logging hooks
  config.py                environment configuration + provider presets
  database.py              SQLite schema and typed accessors
  ai.py                    OpenAI-compatible client (streaming, retries, vision)
  persona.py               persona files, style resolution, placeholders
  credentials.py           tkn.txt parsing (discord-token, provider, model, api-key…)
  checks.py                permission helpers and error embeds
  cache.py                 recent-message cache for delete/edit logs
  automod/
    rules.py               pure detection rules + violation tracker
    engine.py              per-message pipeline, escalation, raid signals
    actions.py             safe kick/ban/timeout wrappers
  cogs/
    ai.py                  ~{question}, mentions, /ask, memory
    moderation.py          manual moderation commands
    automod_config.py      rule configuration commands
    settings.py            per-server settings
    logging.py             audit logging
    security.py            anti-nuke, raid mode, verification
    utility.py             help, info, tags
    tasks.py               background loops
  ui/views.py              confirmations, paginator, persistent verify button
  utils/                   durations, embeds, text helpers
tests/                     pytest suite (173 tests)
docs/COMMANDS.md           full command reference
```

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Missing Discord token` | Add `discord-token=...` to `tkn.txt`, or set `DISCORD_TOKEN` in `.env`. |
| `PrivilegedIntentsRequired` | Enable **Server Members** + **Message Content** in the Developer Portal. |
| Slash commands missing | Wait up to an hour for global sync, or set `DEV_GUILD_ID` for instant sync. Re-invite with the `applications.commands` scope. |
| AI says "backend returned 401" | Wrong key. Check `api-key=` in `tkn.txt` (Gemini keys come from [Google AI Studio](https://aistudio.google.com/apikey)). |
| AI says "backend returned 403" | The key lacks access to that model, or the Gemini API is not enabled for the project. |
| AI says "backend returned 404" | Model name or base URL is wrong. Check the provider's docs. |
| Auto-mod does nothing | `~automod status`; check the channel isn't exempt and that Vantab has **Manage Messages**. |
| "That member's highest role is above mine" | Drag Vantab's role higher in **Server Settings → Roles**. |
| Anti-nuke fired at a trusted admin | `~antinuke whitelist add @user`, then re-invite them. |
| AI answers in the wrong tone | `~persona` to check the style, `~setstyle normal` or edit `prompts/persona.json`. |

---

Built for Discord, powered by whatever model you point it at.
