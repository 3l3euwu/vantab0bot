#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Vantab · installer for a fresh Debian/Ubuntu server (Oracle Cloud Free Tier,
#  Google Cloud e2-micro, any small VPS, a Raspberry Pi…)
#
#  Usage, as root or with sudo:
#      curl -fsSL https://raw.githubusercontent.com/3l3eewu/vantab0bot/main/deploy/setup-vps.sh | sudo bash
#  or, from a clone you already have:
#      sudo APP_DIR=/opt/vantab REPO=3l3eewu/vantab0bot bash deploy/setup-vps.sh
#
#  What it does (idempotent, safe to re-run):
#    1. installs git + python3-venv if missing
#    2. creates the system user "vantab"
#    3. clones/updates the repository into APP_DIR
#    4. builds the virtualenv and installs requirements.txt
#    5. creates data/ (database) and .env (from .env.example) if missing
#    6. installs the systemd unit and starts the bot
#
#  It never touches tkn.txt: copy your own credentials there before starting,
#  or fill DISCORD_TOKEN in /opt/vantab/.env.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/vantab}
REPO=${REPO:-3l3eewu/vantab0bot}
BRANCH=${BRANCH:-main}
SERVICE_USER=${SERVICE_USER:-vantab}

log() { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this as root (sudo bash deploy/setup-vps.sh)"

# ── 1. packages ──────────────────────────────────────────────────────────────
if ! command -v git >/dev/null || ! python3 -c 'import venv' >/dev/null 2>&1; then
  log "Installing git and python3-venv"
  if command -v apt-get >/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq git python3 python3-venv python3-pip ca-certificates
  elif command -v dnf >/dev/null; then
    dnf install -y git python3 python3-pip
  else
    die "unsupported distribution: install git + python3-venv yourself, then re-run"
  fi
fi

# ── 2. service user ──────────────────────────────────────────────────────────
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  log "Creating the '$SERVICE_USER' system user"
  useradd --system --create-home --home-dir "/home/$SERVICE_USER" --shell /usr/sbin/nologin "$SERVICE_USER" \
    || useradd --system --create-home --home-dir "/home/$SERVICE_USER" --shell /sbin/nologin "$SERVICE_USER"
fi

# ── 3. code ──────────────────────────────────────────────────────────────────
if [ -d "$APP_DIR/.git" ]; then
  log "Updating $APP_DIR"
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" checkout --quiet "$BRANCH"
  git -C "$APP_DIR" pull --quiet --ff-only origin "$BRANCH"
else
  log "Cloning https://github.com/$REPO into $APP_DIR"
  mkdir -p "$APP_DIR"
  git clone --quiet --branch "$BRANCH" "https://github.com/$REPO.git" "$APP_DIR"
fi

# ── 4. virtualenv ────────────────────────────────────────────────────────────
log "Installing Python dependencies"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# ── 5. state files ───────────────────────────────────────────────────────────
mkdir -p "$APP_DIR/data"
if [ ! -f "$APP_DIR/.env" ]; then
  log "Creating $APP_DIR/.env from .env.example"
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi
chmod 600 "$APP_DIR/.env"
[ -f "$APP_DIR/tkn.txt" ] && chmod 600 "$APP_DIR/tkn.txt"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

# ── 6. systemd ───────────────────────────────────────────────────────────────
log "Installing the systemd unit"
install -m 644 "$APP_DIR/deploy/vantab.service" /etc/systemd/system/vantab.service
systemctl daemon-reload
systemctl enable --now vantab.service

cat <<EOF

─────────────────────────────────────────────────────────────────────────────
Vantab is installed in $APP_DIR and started as the systemd unit "vantab".

Next steps:
  1. put your credentials in $APP_DIR/tkn.txt  (or edit $APP_DIR/.env)
       sudo nano $APP_DIR/tkn.txt
  2. restart it:
       sudo systemctl restart vantab
  3. check it:
       systemctl status vantab --no-pager
       journalctl -u vantab -f          # live logs

Every later update:
  cd $APP_DIR && sudo git pull && sudo systemctl restart vantab
─────────────────────────────────────────────────────────────────────────────
EOF
