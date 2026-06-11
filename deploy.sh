#!/usr/bin/env bash
#
# Script Vault — one-command deploy for a fresh Debian/Ubuntu server.
# Sets up: Python venv + app, systemd service, nginx vhost, Let's Encrypt TLS.
#
# Run as root ON THE SERVER:
#
#   TOKEN_DB='mongodb+srv://user:pass@cluster/...' \
#   ANTHROPIC_API_KEY='sk-ant-...' \
#   bash <(curl -fsSL \
#     https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe/deploy.sh)
#
# TOKEN_DB (MongoDB connection string) is required. ANTHROPIC_API_KEY is
# optional — without it the AI helper is disabled, the rest works.
# Re-running is safe (idempotent): it updates app.py and restarts the service.

set -euo pipefail

# ---- settings (override via env) -------------------------------------------
DOMAIN="${DOMAIN:-scripts.nodewiki.info}"
APP_DIR="${APP_DIR:-/opt/script-vault}"
APP_USER="${APP_USER:-scriptvault}"
APP_PORT="${APP_PORT:-8000}"
LE_EMAIL="${LE_EMAIL:-prostotvinkazaza@gmail.com}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe}"
# Set REPLACE_APACHE=1 to stop/disable apache2 if it is holding port 80/443.
REPLACE_APACHE="${REPLACE_APACHE:-0}"
TOKEN_DB="${TOKEN_DB:-}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
# ---------------------------------------------------------------------------

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root (sudo)."
[ -n "$TOKEN_DB" ] || die "TOKEN_DB is required — pass your MongoDB connection string:
  TOKEN_DB='mongodb+srv://...' bash deploy.sh"

# ---- preflight: who owns ports 80/443? -------------------------------------
log "Checking what is listening on ports 80/443..."
listeners="$(ss -tlnp 2>/dev/null | grep -E ':80\s|:443\s' || true)"
if [ -n "$listeners" ]; then
  echo "$listeners"
  if echo "$listeners" | grep -qi nginx; then
    log "nginx already present — will add a vhost (other sites untouched)."
  elif echo "$listeners" | grep -qi apache; then
    if [ "$REPLACE_APACHE" = "1" ]; then
      warn "apache2 holds 80/443 — stopping & disabling it (REPLACE_APACHE=1)."
      systemctl stop apache2 || true
      systemctl disable apache2 || true
    else
      die "apache2 is using port 80/443. Re-run with REPLACE_APACHE=1 to switch to nginx,
      or tell me what else runs on this server so we can coexist."
    fi
  else
    warn "Unknown service on 80/443. nginx may fail to bind. Inspect the output above."
    warn "Re-run after freeing the ports, or set REPLACE_APACHE=1 if it's apache."
  fi
fi

# ---- packages --------------------------------------------------------------
log "Installing packages (python, nginx, certbot)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx curl ca-certificates

# ---- app user & dirs -------------------------------------------------------
id "$APP_USER" &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR/data"

# ---- fetch app.py ----------------------------------------------------------
log "Fetching app.py..."
curl -fsSL "$RAW_BASE/app.py" -o "$APP_DIR/app.py" || die "Could not download app.py from $RAW_BASE"
python3 -c "compile(open('$APP_DIR/app.py').read(), 'app.py', 'exec')" || die "Downloaded app.py failed to parse."

# ---- virtualenv ------------------------------------------------------------
log "Setting up virtualenv & dependencies..."
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet fastapi uvicorn python-multipart motor anthropic

# ---- persistent SECRET_KEY (so sessions survive restarts) ------------------
SECRET_FILE="$APP_DIR/secret.key"
if [ ! -s "$SECRET_FILE" ]; then
  python3 -c "import secrets; print(secrets.token_urlsafe(48))" > "$SECRET_FILE"
fi
SECRET_KEY="$(cat "$SECRET_FILE")"

# ---- env file --------------------------------------------------------------
log "Writing environment file..."
cat > "$APP_DIR/script-vault.env" <<EOF
TOKEN_DB=$TOKEN_DB
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
SECRET_KEY=$SECRET_KEY
BASE_URL=https://$DOMAIN
HOST=127.0.0.1
PORT=$APP_PORT
EOF

chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$SECRET_FILE" "$APP_DIR/script-vault.env"

# ---- systemd service -------------------------------------------------------
log "Installing systemd service..."
cat > /etc/systemd/system/script-vault.service <<EOF
[Unit]
Description=Script Vault
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/script-vault.env
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/app.py
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable script-vault >/dev/null 2>&1 || true
systemctl restart script-vault

# ---- wait for the app to answer locally ------------------------------------
log "Waiting for the app on 127.0.0.1:$APP_PORT ..."
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$APP_PORT/login" >/dev/null 2>&1; then
    log "App is up."
    break
  fi
  sleep 0.5
  [ "$i" = "20" ] && { journalctl -u script-vault --no-pager -n 30; die "App did not start."; }
done

# ---- nginx vhost -----------------------------------------------------------
log "Configuring nginx for $DOMAIN ..."
cat > /etc/nginx/sites-available/script-vault <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:$APP_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
    }
}
EOF
ln -sf /etc/nginx/sites-available/script-vault /etc/nginx/sites-enabled/script-vault
[ -e /etc/nginx/sites-enabled/default ] && rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx >/dev/null 2>&1 || true
systemctl restart nginx

# ---- TLS via Let's Encrypt (HTTP-01; origin must be reachable on :80) ------
log "Requesting Let's Encrypt certificate for $DOMAIN ..."
if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$LE_EMAIL" --redirect; then
  SCHEME="https"
else
  warn "certbot failed — the site is live on HTTP for now."
  warn "Ensure $DOMAIN resolves to THIS server and port 80 is open to the internet, then run:"
  warn "  certbot --nginx -d $DOMAIN --agree-tos -m $LE_EMAIL --redirect"
  SCHEME="http"
fi

# ---- done ------------------------------------------------------------------
echo
log "Deploy complete:  $SCHEME://$DOMAIN/"
echo
echo   "  Open the site and register the first account at $SCHEME://$DOMAIN/register"
if [ -z "$ANTHROPIC_API_KEY" ]; then
  warn "ANTHROPIC_API_KEY not set — the AI helper is disabled."
  warn "Add it to $APP_DIR/script-vault.env and run: systemctl restart script-vault"
fi
echo
echo "  Logs:     journalctl -u script-vault -f"
echo "  Restart:  systemctl restart script-vault"
