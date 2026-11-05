# Hosting Vantab for free

Vantab is a **gateway** bot: it opens one outbound HTTPS connection to Discord and
never listens on a port. That means almost anything that can keep a Python 3.11+
process alive 24/7 will host it — no domain, no TLS certificate, no open port.

Sizing: the whole process sits around **80–150 MB of RAM** and idles near 0 % CPU.
A single-core 512 MB machine is enough; anything bigger is comfort, not necessity.

---

## The short answer

| Option | Cost | Always on? | Verdict |
| --- | --- | --- | --- |
| **Your own PC** (`deploy/windows/`) | 0 €, **no card** | only while it runs | best no-card option — see the section below |
| **Old phone (Termux) / Raspberry Pi** | 0 €, **no card** | yes | great if you already own the hardware |
| **Free bot panels** (Bot-Hosting, Silly…) | 0 €, **no card** | usually | they hold your token, manual renewals — read the warnings |
| **Oracle Cloud Always Free** (Arm VM) | 0 € (card needed at signup) | yes | best free 24/7 cloud VM, persistent disk |
| **Google Cloud free tier** (e2-micro) | 0 € (card needed at signup) | yes | same idea, smaller, 1 GB/month egress |
| Raspberry Pi / old laptop at home | electricity | yes | great if you already own one |
| Render / Railway / Koyeb / Fly.io free tiers | 0 € | **no** (sleep, credits, quotas) | not made for a long-lived gateway connection |
| GitHub Actions, Replit, Glitch | 0 € | no | job timeouts / dead free tiers, do not bother |

Two pitfalls matter more than the provider itself:

1. **Ephemeral filesystems.** Vantab keeps everything in SQLite (`data/vantab.db`:
   cases, warnings, tickets, tags, config). On app hosts that wipe the disk at each
   deploy you lose all of it. A VM with a normal disk, or a mounted volume, avoids
   the problem entirely.
2. **Idle reclamation.** Oracle may reclaim an Always Free instance whose
   95th-percentile CPU/network/memory all stay under 20 % for 7 days. Vantab is
   idle by nature — see the keep-alive note below.

---

## No credit card? Host it at home (recommended)

Every big free tier (Oracle, Google Cloud, AWS, Azure) asks for a card for identity
verification, even when the resources stay free. If you do not want to hand over
card details at all, the reliable options are local ones — and for a bot this small
they are genuinely enough.

### Your Windows PC, properly set up

The repo ships three helpers in `deploy/windows/`:

| File | Role |
| --- | --- |
| `run-vantab.bat` | starts `bot.py`, logs to `logs\bot.log`, restarts it 10 s after any crash |
| `run-hidden.vbs` | same, without the console window |
| `manage-vantab.ps1` | installs/removes a Scheduled Task that starts the bot at every logon |

```powershell
# one-time install (creates the task and starts the bot now)
powershell -ExecutionPolicy Bypass -File deploy\windows\manage-vantab.ps1

# then, whenever you need it
powershell -ExecutionPolicy Bypass -File deploy\windows\manage-vantab.ps1 -Status
powershell -ExecutionPolicy Bypass -File deploy\windows\manage-vantab.ps1 -Stop
powershell -ExecutionPolicy Bypass -File deploy\windows\manage-vantab.ps1 -Start
powershell -ExecutionPolicy Bypass -File deploy\windows\manage-vantab.ps1 -Uninstall
```

Keep the machine awake while the bot must answer — the screen may sleep, the
system must not:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
# laptops: "closing the lid" must be set to "do nothing" in the power options
```

The bot is offline while the PC is off, asleep, or without internet. That is the
only real trade-off of this option, and it costs nothing and leaks no token.

### An old laptop, a Raspberry Pi, or an old Android phone

- **Old laptop / Raspberry Pi** — install Debian/Ubuntu and use
  `deploy/setup-vps.sh` exactly as on a VPS; the bot needs ~150 MB of RAM.
- **Android phone with Termux** (no root, no card):

  ```bash
  pkg update && pkg install python git tmux
  git clone https://github.com/3l3eewu/vantab0bot && cd vantab0bot
  python -m venv .venv && .venv/bin/pip install -r requirements.txt
  # credentials: cp tkn.example.txt tkn.txt, then edit it
  termux-wake-lock          # keeps Android from killing the process
  tmux new -s vantab        # then: .venv/bin/python bot.py  (Ctrl+B, D to detach)
  ```

  Disable battery optimisation for Termux, keep the phone charging and on Wi-Fi.

### Free Discord-bot panels (cloud, no card)

A handful of small hosts run Discord bots 24/7 on a free plan without asking for a
card — for example [Bot-Hosting.net](https://bot-hosting.net/) (256 MB, 1 deployment,
manual renewal every 4 days), [Silly Development](https://sillydev.co.uk/) (256 MB,
Node/Python/Bun, no renewals) or [fps.ms](https://fps.ms/free-discord-bot-hosting/)
(128 MB, 25 % CPU, 24 h renewal). They deploy from Git or a zip and keep the process
alive, which is exactly what Vantab needs.

Read this before using one:

- **They hold your bot token.** Anyone with panel access to that token can read every
  message and act as the bot in your server. Use a token you are willing to rotate,
  change it in the Discord Developer Portal the moment you leave, and never give the
  panel your Discord account password — invite-only OAuth, `bot` scope.
- **Renewals.** Several free plans require clicking "renew" every few days; miss it
  and the bot stops. No renewal means no uptime.
- **Storage may be wiped.** Keep your `data/vantab.db` backup (see below); if the
  panel loses the disk you lose warns, cases and tickets.
- **Small RAM.** 128–256 MB is enough for Vantab, but the AI answer path plus a
  large guild can get close. Watch the panel's memory graph.

Use them as a stop-gap; the home setup above is both safer and more reliable.

### What does not work

| Option | Why not |
| --- | --- |
| GitHub Actions | 6-hour job limit, cron gaps, and using runners as a 24/7 runtime breaks GitHub's acceptable-use rules |
| GitHub Codespaces | free hours stop when the browser closes / idles out |
| Replit, Glitch | no free always-on runtime any more |
| Render free web service | sleeps without traffic; a gateway bot has no traffic to keep it awake |

---

## Oracle Cloud Always Free (with a card, best 24/7 cloud option)

> Specs move around: the Ampere A1 allowance used to be 4 OCPU / 24 GB and was cut
> to **2 OCPU / 12 GB** in mid-2026. The AMD micro shapes (1/8 OCPU, 1 GB) are still
> free and are plenty for Vantab. Always Free resources have no end date, but Oracle
> can reclaim idle instances and suspend accounts unused for 30 days.

1. **Create an account** on <https://www.oracle.com/cloud/free/> and pick a **home
   region** (Always Free instances can only be created there). A card is used for
   identity verification only; stay on Always Free shapes to keep the bill at 0.
2. **Create the instance** — Compute → Instances → Create:
   - Image: **Ubuntu 24.04** (or Oracle Linux 9)
   - Shape: **VM.Standard.A1.Flex** with 1 OCPU / 6 GB, or the free AMD
     `VM.Standard.E2.1.Micro` if you get `Out of host capacity` on Arm
   - Networking: keep the default VCN, **no ingress rule needed** (the bot only
     dials out). Leave 22/tcp open for SSH.
   - Add your **SSH public key**.
3. **Connect**: `ssh ubuntu@<public-ip>` (Oracle Linux: `opc@…`).
4. **Install** — one command:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/3l3eewu/vantab0bot/main/deploy/setup-vps.sh | sudo bash
   ```

   The script installs Python, clones the repo into `/opt/vantab`, builds the
   virtualenv, creates `data/` and `.env`, installs `deploy/vantab.service` and starts it.
5. **Add the credentials**:

   ```bash
   sudo nano /opt/vantab/tkn.txt     # discord-token=… / provider=gemini / api-key=…
   sudo chmod 600 /opt/vantab/tkn.txt
   sudo systemctl restart vantab
   ```
6. **Check**:

   ```bash
   systemctl status vantab --no-pager
   journalctl -u vantab -f
   ```

   You should see `Vantab 1.0.0 · code loaded from /opt/vantab` and
   `Logged in as Vantab Bot#7445`.

### Keep the instance from being flagged as idle

Oracle's rule looks at a 7-day window. A cheap trick is to keep a little CPU busy —
for example a systemd timer that hashes a file every 5 minutes:

```bash
sudo tee /etc/systemd/system/vantab-keepalive.service >/dev/null <<'EOF'
[Unit]
Description=Vantab keep-alive (avoid idle reclamation)

[Service]
Type=oneshot
ExecStart=/usr/bin/env bash -c 'df -h >/dev/null; dd if=/dev/urandom of=/dev/null bs=1M count=200 status=none'
EOF

sudo tee /etc/systemd/system/vantab-keepalive.timer >/dev/null <<'EOF'
[Unit]
Description=Run the Vantab keep-alive job

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

sudo systemctl enable --now vantab-keepalive.timer
```

A bot that actually answers questions every day already counts as usage; the timer
is only there in case the server stays quiet for a week.

---

## Google Cloud free tier (e2-micro)

1. Create a project, enable Compute Engine, then create an **e2-micro** instance in
   `us-west1`, `us-central1` or `us-east1` (that is what stays free), Ubuntu 24.04,
   30 GB standard disk.
2. Allow HTTP/SSH as prompted — the bot needs neither, but SSH yes.
3. Run the same installer:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/3l3eewu/vantab0bot/main/deploy/setup-vps.sh | sudo bash
   ```

4. Put the credentials in `/opt/vantab/tkn.txt`, then
   `sudo systemctl restart vantab`.

Note the free egress allowance (about **1 GB/month** outside North America). Vantab's
own traffic is a few MB per day; `git pull` + `pip install` during installs is what
actually consumes it, so avoid re-running the installer in a loop.

---

## Linux at home (old laptop, Raspberry Pi)

Same commands as a VPS, without the cloud:

```bash
git clone https://github.com/3l3eewu/vantab0bot && cd vantab0bot
sudo bash deploy/setup-vps.sh          # installs the systemd service
sudo nano /opt/vantab/tkn.txt          # credentials
sudo systemctl restart vantab
```

Or, without installing anything system-wide: `python3 -m venv .venv`,
`.venv/bin/pip install -r requirements.txt`, then run `.venv/bin/python bot.py` inside
`tmux`. Downside: the bot is offline whenever the machine sleeps or loses its link.

---

## Outgrowing the free options

If you ever need more headroom than a free tier, a 4–5 €/month VPS (Hetzner CX22,
Contabo, OVH) runs the same `deploy/setup-vps.sh` and keeps `data/vantab.db` on a
normal disk.

---

## Day-to-day operations

```bash
# update the bot
cd /opt/vantab && sudo git pull && sudo systemctl restart vantab

# live logs
journalctl -u vantab -f

# back up the database (cases, tickets, tags, config)
sudo sqlite3 /opt/vantab/data/vantab.db ".backup '/root/vantab-$(date +%F).db'"
# or, without sqlite3 installed:
sudo systemctl stop vantab && sudo cp /opt/vantab/data/vantab.db /root/ && sudo systemctl start vantab

# the credentials file is the only real secret on the box
sudo chmod 600 /opt/vantab/tkn.txt /opt/vantab/.env
```

Keep the machine patched (`sudo apt upgrade` monthly); nothing else is exposed thanks
to `ProtectSystem=strict` and the fact that no port is opened.

---

## Checklist before you call it done

- [ ] `systemctl status vantab` shows `active (running)`, restarts automatically
- [ ] `journalctl -u vantab` shows `Logged in as …` and no `session invalidated`
- [ ] `tkn.txt` is **not** in git (`git status` clean, it is in `.gitignore`)
- [ ] `data/vantab.db` lives on a disk that survives reboots
- [ ] a restore of that file was tested once
- [ ] you know how to read the logs when a command fails
