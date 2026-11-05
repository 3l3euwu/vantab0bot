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
| **Your own PC** | 0 € | only while it runs | fine to develop, bad if the PC sleeps |
| **Oracle Cloud Always Free** (Arm VM) | 0 € | yes | **best free 24/7 option** — a real VM, persistent disk |
| **Google Cloud free tier** (e2-micro) | 0 € | yes | same idea, smaller, 1 GB/month egress |
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

## Oracle Cloud Always Free (recommended)

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

## Home machine, Raspberry Pi or old laptop

The simplest free option if the machine stays on anyway:

```bash
git clone https://github.com/3l3eewu/vantab0bot && cd vantab0bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # or tkn.txt
python bot.py
```

To keep it running: `tmux`/`screen`, or install `deploy/vantab.service`
(adjust `WorkingDirectory`, `User` and `EnvironmentFile`), or on Windows use
`pythonw bot.py` plus Task Scheduler. Downside: the bot is offline whenever the
machine sleeps or the connection drops.

---

## Why the "free app hosting" tiers are a poor fit

These platforms are built for HTTP services, and Vantab is the opposite:

- **Render free** — web services spin down after inactivity; a worker without HTTP
  is not free. A sleeping process means a disconnected bot.
- **Railway** — no permanent free plan any more, only trial credits.
- **Fly.io / Koyeb** — small free allowances that keep shrinking, and they expect a
  listening port; you would have to run a dummy HTTP server.
- **GitHub Actions / Replit / Glitch** — job time limits or removed free tiers. Using
  Actions as a 24/7 runtime also violates GitHub's acceptable-use rules.

If you ever outgrow the free VM, a 4–5 €/month VPS (Hetzner CX22, Contabo, OVH) has
more headroom than any free tier and keeps the same setup.

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
