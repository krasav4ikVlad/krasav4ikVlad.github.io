#!/usr/bin/env bash
#
# nodewiki hub — главная страница с единым входом (SSO).
# Python-сервис (hub_app.py) на 127.0.0.1:8001 за nginx + TLS.
# SECRET_KEY и TOKEN_DB берёт из /opt/script-vault/script-vault.env —
# поэтому сессия хаба автоматически принимается Script Vault'ом.
#
# Запускать root'ом НА ОСНОВНОМ СЕРВЕРЕ (там же, где scripts):
#
#   bash <(curl -fsSL \
#     https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe/deploy-hub.sh)
#
# Также дописывает в env Script Vault: COOKIE_DOMAIN=.nodewiki.info и
# LOGIN_URL=https://nodewiki.info/login (вход переезжает на главную)
# и перезапускает script-vault. Повторный запуск безопасен.

set -euo pipefail

DOMAIN="${DOMAIN:-nodewiki.info}"
ALT="${ALT:-www.nodewiki.info}"
APP_DIR="${APP_DIR:-/opt/nodewiki-hub}"
APP_USER="${APP_USER:-scriptvault}"   # тот же системный пользователь, что у scripts
APP_PORT="${APP_PORT:-8001}"
SV_ENV="${SV_ENV:-/opt/script-vault/script-vault.env}"
LE_EMAIL="${LE_EMAIL:-prostotvinkazaza@gmail.com}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe}"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root (sudo)."
[ -f "$SV_ENV" ] || die "Не найден $SV_ENV — сначала разверните Script Vault (deploy.sh)."

# ---- preflight: никакой другой vhost не должен держать наш домен -------------
# (точное совпадение токена server_name; scripts.nodewiki.info — не конфликт)
conflicts=""
for f in $(grep -Rls "server_name" /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | grep -v "nodewiki-hub" || true); do
  if awk -v d="$DOMAIN" '
        /^[ \t]*#/ { next }
        /server_name/ { for (i = 1; i <= NF; i++) { t = $i; gsub(/;/, "", t); if (t == d) { found = 1 } } }
        END { exit !found }' "$f"; then
    conflicts="$conflicts$f"$'\n'
  fi
done
if [ -n "$conflicts" ]; then
  warn "Эти конфиги уже объявляют $DOMAIN и будут конфликтовать с хабом:"
  echo "$conflicts" >&2
  die "Отключите их (rm симлинк из sites-enabled) и запустите скрипт снова."
fi

# ---- секреты из Script Vault (общие => SSO) ----------------------------------
TOKEN_DB="$(grep -h '^TOKEN_DB=' "$SV_ENV" | head -1 | cut -d= -f2-)"
SECRET_KEY="$(grep -h '^SECRET_KEY=' "$SV_ENV" | head -1 | cut -d= -f2-)"
[ -n "$TOKEN_DB" ] && [ -n "$SECRET_KEY" ] || die "В $SV_ENV нет TOKEN_DB/SECRET_KEY."

# ---- пакеты ------------------------------------------------------------------
if ! command -v nginx >/dev/null 2>&1 || ! command -v certbot >/dev/null 2>&1; then
  log "Installing nginx + certbot..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y nginx certbot python3-certbot-nginx python3 python3-venv curl ca-certificates
fi

# ---- приложение --------------------------------------------------------------
log "Fetching hub_app.py..."
id "$APP_USER" &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR"
curl -fsSL "$RAW_BASE/hub_app.py" -o "$APP_DIR/hub_app.py" || die "Не скачался hub_app.py"
python3 -c "compile(open('$APP_DIR/hub_app.py').read(), 'hub_app.py', 'exec')" || die "hub_app.py не парсится."

log "Virtualenv & deps..."
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet fastapi uvicorn python-multipart motor

log "Writing env file..."
cat > "$APP_DIR/nodewiki-hub.env" <<EOF
TOKEN_DB=$TOKEN_DB
SECRET_KEY=$SECRET_KEY
COOKIE_DOMAIN=.nodewiki.info
BASE_URL=https://$DOMAIN
SCRIPTS_URL=https://scripts.nodewiki.info
HOST=127.0.0.1
PORT=$APP_PORT
EOF
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/nodewiki-hub.env"

log "Systemd service..."
cat > /etc/systemd/system/nodewiki-hub.service <<EOF
[Unit]
Description=nodewiki hub (SSO + landing)
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/nodewiki-hub.env
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/hub_app.py
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
systemctl enable nodewiki-hub >/dev/null 2>&1 || true
systemctl restart nodewiki-hub

log "Waiting for the hub on 127.0.0.1:$APP_PORT ..."
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$APP_PORT/health" >/dev/null 2>&1; then
    log "Hub is up."
    break
  fi
  sleep 0.5
  [ "$i" = "20" ] && { journalctl -u nodewiki-hub --no-pager -n 30; die "Hub did not start."; }
done

# ---- nginx -------------------------------------------------------------------
log "Configuring nginx for $DOMAIN ..."
cat > /etc/nginx/sites-available/nodewiki-hub <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN $ALT;

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
ln -sf /etc/nginx/sites-available/nodewiki-hub /etc/nginx/sites-enabled/nodewiki-hub
nginx -t
systemctl reload nginx

log "Requesting Let's Encrypt certificate for $DOMAIN, $ALT ..."
if certbot --nginx -d "$DOMAIN" -d "$ALT" --non-interactive --agree-tos -m "$LE_EMAIL" --redirect; then
  SCHEME="https"
else
  warn "certbot failed — хаб пока на HTTP. Проверьте DNS и запустите:"
  warn "  certbot --nginx -d $DOMAIN -d $ALT --agree-tos -m $LE_EMAIL --redirect"
  SCHEME="http"
fi

# ---- включаем SSO в Script Vault ----------------------------------------------
log "Enabling SSO in Script Vault env..."
grep -q '^COOKIE_DOMAIN=' "$SV_ENV" \
  && sed -i 's|^COOKIE_DOMAIN=.*|COOKIE_DOMAIN=.nodewiki.info|' "$SV_ENV" \
  || echo "COOKIE_DOMAIN=.nodewiki.info" >> "$SV_ENV"
grep -q '^LOGIN_URL=' "$SV_ENV" \
  && sed -i "s|^LOGIN_URL=.*|LOGIN_URL=$SCHEME://$DOMAIN/login|" "$SV_ENV" \
  || echo "LOGIN_URL=$SCHEME://$DOMAIN/login" >> "$SV_ENV"
grep -q '^HUB_URL=' "$SV_ENV" \
  && sed -i "s|^HUB_URL=.*|HUB_URL=$SCHEME://$DOMAIN|" "$SV_ENV" \
  || echo "HUB_URL=$SCHEME://$DOMAIN" >> "$SV_ENV"
systemctl restart script-vault

echo
log "Hub deployed:  $SCHEME://$DOMAIN/"
echo "  Вход и регистрация теперь на главной; сессия общая для поддоменов."
echo "  ВНИМАНИЕ: всем нужно войти заново один раз (cookie переехала на .nodewiki.info)."
echo "  Логи:    journalctl -u nodewiki-hub -f"
echo "  Рестарт: systemctl restart nodewiki-hub"
