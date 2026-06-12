#!/usr/bin/env bash
#
# nodewiki VPN Checker — deploy on a SEPARATE (RU) server.
# Python service (checker_app.py) on 127.0.0.1:8002 behind nginx + TLS.
#
# SSO: SECRET_KEY и TOKEN_DB ДОЛЖНЫ совпадать с основным сервером —
# тогда чекер принимает ту же сессию (.nodewiki.info) и видит тех же
# пользователей. Возьмите их из основного сервера:
#   grep -E '^(SECRET_KEY|TOKEN_DB)=' /opt/script-vault/script-vault.env
#
# DNS: в Cloudflare заведите A-запись (grey cloud) checker.nodewiki.info -> IP ЭТОГО сервера.
#
# Запуск root'ом НА РУ-СЕРВЕРЕ:
#   TOKEN_DB='mongodb+srv://...' SECRET_KEY='...тот же...' \
#     bash <(curl -fsSL \
#       https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe/deploy-checker.sh)
#
# Повторный запуск безопасен (идемпотентно).

set -euo pipefail

DOMAIN="${DOMAIN:-checker.nodewiki.info}"
APP_DIR="${APP_DIR:-/opt/nodewiki-checker}"
APP_USER="${APP_USER:-nwchecker}"
APP_PORT="${APP_PORT:-8002}"
HUB_URL="${HUB_URL:-https://nodewiki.info}"
LE_EMAIL="${LE_EMAIL:-prostotvinkazaza@gmail.com}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe}"
TOKEN_DB="${TOKEN_DB:-}"
SECRET_KEY="${SECRET_KEY:-}"
CHECKER_WORKERS="${CHECKER_WORKERS:-2}"
CHECKER_ALLOW_PRIVATE="${CHECKER_ALLOW_PRIVATE:-0}"
# токен для residential-зондов; если не задан — генерируется и печатается в конце
AGENT_TOKEN="${AGENT_TOKEN:-}"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
# безопасное чтение KEY=value из env-файла (не валит set -e/pipefail, если строки нет)
envget() { [ -f "$2" ] && grep -h "^$1=" "$2" 2>/dev/null | head -1 | cut -d= -f2- || true; }

[ "$(id -u)" -eq 0 ] || die "Run as root (sudo)."
# повторный запуск: подхватываем секреты из уже записанного env, если не переданы
ENVF="$APP_DIR/nodewiki-checker.env"
[ -n "$TOKEN_DB" ]   || TOKEN_DB="$(envget TOKEN_DB "$ENVF")"
[ -n "$SECRET_KEY" ] || SECRET_KEY="$(envget SECRET_KEY "$ENVF")"
[ -n "$TOKEN_DB" ] || die "TOKEN_DB обязателен (та же база, что у основного сервера)."
[ -n "$SECRET_KEY" ] || die "SECRET_KEY обязателен и должен СОВПАДАТЬ с основным сервером (иначе SSO не сработает)."

# ---- packages (iputils-ping нужен для ICMP) --------------------------------
log "Installing packages (python, nginx, certbot, iputils-ping)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx \
  iputils-ping unzip curl ca-certificates

# ---- xray-core (для глубокой проверки через туннель) -----------------------
if ! command -v xray >/dev/null 2>&1; then
  log "Installing xray-core..."
  bash -c "$(curl -fsSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install \
    || warn "Не удалось установить xray — глубокая проверка будет недоступна (xray не установлен)."
  # не держим их дефолтный xray-сервер запущенным: нам нужен только бинарь
  systemctl disable --now xray >/dev/null 2>&1 || true
fi

# ---- токен для residential-зондов ------------------------------------------
GENERATED_AGENT=""
if [ -z "$AGENT_TOKEN" ]; then
  AGENT_TOKEN="$(envget CHECKER_AGENT_TOKEN "$ENVF")"   # переиспользуем сохранённый, если есть
  if [ -z "$AGENT_TOKEN" ]; then
    AGENT_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
    GENERATED_AGENT="$AGENT_TOKEN"
  fi
fi

# ---- app -------------------------------------------------------------------
id "$APP_USER" &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR"
log "Fetching checker_app.py..."
curl -fsSL "$RAW_BASE/checker_app.py" -o "$APP_DIR/checker_app.py" || die "Не скачался checker_app.py"
python3 -c "compile(open('$APP_DIR/checker_app.py').read(),'c','exec')" || die "checker_app.py не парсится."

log "Virtualenv & deps..."
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet fastapi uvicorn python-multipart motor "httpx[socks]"

log "Writing env file..."
cat > "$APP_DIR/nodewiki-checker.env" <<EOF
TOKEN_DB=$TOKEN_DB
SECRET_KEY=$SECRET_KEY
COOKIE_DOMAIN=.nodewiki.info
HUB_URL=$HUB_URL
BASE_URL=https://$DOMAIN
HOST=127.0.0.1
PORT=$APP_PORT
CHECKER_WORKERS=$CHECKER_WORKERS
CHECKER_ALLOW_PRIVATE=$CHECKER_ALLOW_PRIVATE
XRAY_BIN=/usr/local/bin/xray
CHECKER_XRAY_CONCURRENCY=1
CHECKER_AGENT_TOKEN=$AGENT_TOKEN
EOF
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/nodewiki-checker.env"

# ---- systemd (AmbientCapabilities=CAP_NET_RAW => рабочий ICMP-ping) ---------
log "Systemd service..."
cat > /etc/systemd/system/nodewiki-checker.service <<EOF
[Unit]
Description=nodewiki VPN Checker
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/nodewiki-checker.env
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/checker_app.py
Restart=always
RestartSec=2
AmbientCapabilities=CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_RAW
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable nodewiki-checker >/dev/null 2>&1 || true
systemctl restart nodewiki-checker

log "Waiting for the checker on 127.0.0.1:$APP_PORT ..."
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$APP_PORT/health" >/dev/null 2>&1; then
    log "Checker is up."; break
  fi
  sleep 0.5
  [ "$i" = "20" ] && { journalctl -u nodewiki-checker --no-pager -n 30; die "Checker did not start."; }
done

# ---- nginx -----------------------------------------------------------------
log "Configuring nginx for $DOMAIN ..."
cat > /etc/nginx/sites-available/nodewiki-checker <<EOF
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
        proxy_read_timeout 120s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/nodewiki-checker /etc/nginx/sites-enabled/nodewiki-checker
[ -e /etc/nginx/sites-enabled/default ] && rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx >/dev/null 2>&1 || true
systemctl restart nginx

log "Requesting Let's Encrypt certificate for $DOMAIN ..."
if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$LE_EMAIL" --redirect; then
  SCHEME="https"
else
  warn "certbot failed — чекер пока на HTTP. Проверьте, что $DOMAIN резолвится на ЭТОТ сервер и :80 открыт, затем:"
  warn "  certbot --nginx -d $DOMAIN --agree-tos -m $LE_EMAIL --redirect"
  SCHEME="http"
fi

echo
log "VPN Checker deployed:  $SCHEME://$DOMAIN/"
echo "  Вход — через хаб ($HUB_URL); сессия общая (.nodewiki.info)."
echo "  Логи:    journalctl -u nodewiki-checker -f"
echo "  Рестарт: systemctl restart nodewiki-checker"
echo
echo "  residential-зонд (мерить КАК У ПОЛЬЗОВАТЕЛЯ, а не из ДЦ):"
echo "    Linux (systemd) — одной командой:"
echo "      AGENT_TOKEN=<token> bash <(curl -fsSL $RAW_BASE/deploy-probe.sh)"
echo "    Windows (Планировщик) — в PowerShell от админа:"
echo "      \$env:AGENT_TOKEN='<token>'; irm $RAW_BASE/deploy-probe.ps1 | iex"
if [ -n "$GENERATED_AGENT" ]; then
  printf '\033[1;32m    AGENT_TOKEN (сгенерирован): %s\033[0m\n' "$GENERATED_AGENT"
else
  echo "    токен: grep CHECKER_AGENT_TOKEN $APP_DIR/nodewiki-checker.env"
fi
echo "    Без зонда глубокая проверка работает из ДЦ (помечается «через дата-центр»)."
