#!/bin/bash
# Idempotent server bootstrap. Safe to re-run — every step checks before acting.
# Run on a fresh Ubuntu box (tested on 22.04 and 26.04) as the ubuntu user:
#   ssh -i key/<your-key> ubuntu@<host>
#   ~/agent/deploy/setup_server.sh
set -e

APP_DIR="$HOME/agent"
DOMAIN="${CC_DOMAIN:-cc.thinkgalactic.in}"
SWAP_FILE="/swapfile"
SWAP_SIZE_GB=2

echo "== 1/6: swapfile (${SWAP_SIZE_GB}GB) — the thing that would have saved the Oracle box =="
if ! sudo swapon --show | grep -q "$SWAP_FILE"; then
  sudo fallocate -l "${SWAP_SIZE_GB}G" "$SWAP_FILE" || sudo dd if=/dev/zero of="$SWAP_FILE" bs=1M count=$((SWAP_SIZE_GB * 1024))
  sudo chmod 600 "$SWAP_FILE"
  sudo mkswap "$SWAP_FILE"
  sudo swapon "$SWAP_FILE"
  grep -q "$SWAP_FILE" /etc/fstab || echo "$SWAP_FILE none swap sw 0 0" | sudo tee -a /etc/fstab
  echo "swapfile created and enabled"
else
  echo "swapfile already active, skipping"
fi

echo "== 2/6: system packages =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git curl 2>&1 | tail -5

echo "== 3/6: python venv + pinned requirements =="
cd "$APP_DIR"
if [ ! -d venv ]; then
  python3 -m venv venv
fi
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt
echo "venv ready: $(./venv/bin/python --version)"

echo "== 4/6: Caddy (auto-TLS reverse proxy) =="
if ! command -v caddy >/dev/null 2>&1; then
  sudo apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
  sudo apt-get update -qq
  sudo apt-get install -y -qq caddy
  echo "caddy installed"
else
  echo "caddy already installed, skipping"
fi

sudo tee /etc/caddy/Caddyfile > /dev/null <<EOF
${DOMAIN} {
    reverse_proxy localhost:4000
}
EOF
sudo systemctl reload caddy 2>/dev/null || sudo systemctl restart caddy

echo "== 5/7: systemd services (auto-restart, memory-capped) =="
sudo tee /etc/systemd/system/commandcenter.service > /dev/null <<EOF
[Unit]
Description=Hariv Command Center
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/gunicorn --bind 0.0.0.0:4000 --workers 2 --timeout 600 app:app
Restart=always
RestartSec=3
MemoryMax=1536M
OOMPolicy=stop

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/ccbot.service > /dev/null <<EOF
[Unit]
Description=Hariv Command Center — Telegram bot
After=network.target commandcenter.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python -u tools/telegram_bot.py
Restart=always
RestartSec=5
MemoryMax=512M
OOMPolicy=stop

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable commandcenter ccbot
sudo systemctl restart commandcenter
if grep -q "TELEGRAM_BOT_TOKEN=." "$APP_DIR/.env" 2>/dev/null; then
  sudo systemctl restart ccbot
  echo "ccbot started"
else
  echo "TELEGRAM_BOT_TOKEN not set in .env — ccbot service enabled but not started this run"
fi

echo "== 6/7: gunicorn threads + timeout for SSE streaming =="
# The board/followup streaming endpoints now run advisors one at a time
# instead of concurrently (avoids bursting past OpenRouter's 20-req/minute
# cap), so a full multi-advisor session can legitimately take several
# minutes — 600s gives real headroom instead of gunicorn killing the worker
# mid-session. A single sync worker would also block all other requests
# during that window, hence --threads. Both seds are idempotent regardless
# of what a previous run of this script already left in place.
sudo sed -i -E 's/--timeout [0-9]+/--timeout 600/' /etc/systemd/system/commandcenter.service
if ! grep -q -- '--threads' /etc/systemd/system/commandcenter.service; then
  sudo sed -i 's/--workers 2/--workers 2 --threads 4/' /etc/systemd/system/commandcenter.service
fi
sudo systemctl daemon-reload
sudo systemctl restart commandcenter

echo "== 7/7: cron jobs (intel, briefs, Telegram pushes, weekly, memory, anticipation, quarterly bet) =="
CRON_MARKER="# commandcenter-cron"
# `|| true` matters: on a fresh box with no existing crontab, `grep -v` finds
# nothing to filter and exits 1 — under `set -e` that would kill this subshell
# before the heredoc below ever runs, silently installing an EMPTY crontab.
(crontab -l 2>/dev/null | grep -v "$CRON_MARKER" || true; cat <<EOF
0 5 * * * curl -s -X POST http://localhost:4000/api/cron/intel $CRON_MARKER
30 5 * * * curl -s -X POST http://localhost:4000/api/cron/morning $CRON_MARKER
0 6 * * * ${APP_DIR}/venv/bin/python ${APP_DIR}/tools/telegram_bot.py push_morning_brief $CRON_MARKER
45 8 * * * ${APP_DIR}/venv/bin/python ${APP_DIR}/tools/anticipate.py $CRON_MARKER
0 9 * * * ${APP_DIR}/venv/bin/python ${APP_DIR}/tools/telegram_bot.py push_aging_recommendations $CRON_MARKER
5 9 * * * ${APP_DIR}/venv/bin/python ${APP_DIR}/tools/telegram_bot.py push_decision_reviews $CRON_MARKER
30 21 * * * ${APP_DIR}/venv/bin/python ${APP_DIR}/tools/telegram_bot.py push_unlogged_nag $CRON_MARKER
45 21 * * * ${APP_DIR}/venv/bin/python ${APP_DIR}/tools/telegram_bot.py push_evening_brief $CRON_MARKER
30 22 * * * ${APP_DIR}/venv/bin/python ${APP_DIR}/tools/memory_reflect.py $CRON_MARKER
0 8 * * 0 curl -s -X POST http://localhost:4000/api/cron/weekly $CRON_MARKER
5 8 * * 0 ${APP_DIR}/venv/bin/python ${APP_DIR}/tools/telegram_bot.py push_weekly_synthesis $CRON_MARKER
0 20 * * 0 curl -s -X POST http://localhost:4000/api/cron/auto-board $CRON_MARKER
0 3 1 * * curl -s -X POST http://localhost:4000/api/cron/kb-consolidate $CRON_MARKER
0 8 1 1,4,7,10 * curl -s -X POST http://localhost:4000/api/cron/quarter-end $CRON_MARKER
EOF
) | crontab -

echo ""
echo "Done. Checking health..."
sleep 2
sudo systemctl status commandcenter --no-pager | grep Active
sudo systemctl status ccbot --no-pager | grep Active || true
curl -s -o /dev/null -w "local health: %{http_code}\n" http://localhost:4000/healthz || echo "note: /healthz not deployed yet, that's fine on first run"
echo ""
echo "Point ${DOMAIN}'s DNS A-record at this host's IP, then Caddy issues TLS automatically on first request."
