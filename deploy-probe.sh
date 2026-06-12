#!/usr/bin/env bash
#
# nodewiki residential probe — установка зонда как systemd-сервиса на Linux.
#
# ВАЖНО: смысл зонда — мерить ЧЕРЕЗ ДОМАШНИЙ/МОБИЛЬНЫЙ канал (как у конечного
# пользователя). Ставьте его на машину с домашним интернетом: мини-ПК, Raspberry
# Pi, старый ноутбук. На VPS/в датацентре зонд бесполезен — это тот же путь,
# что и фолбэк «из ДЦ». На машине НЕ должен быть включён VPN/прокси.
#
# Запуск root'ом (AGENT_TOKEN — тот же CHECKER_AGENT_TOKEN, что на чекере;
# его печатает deploy-checker.sh, либо смотри /opt/nodewiki-checker/nodewiki-checker.env):
#
#   AGENT_TOKEN='...' \
#   bash <(curl -fsSL \
#     https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe/deploy-probe.sh)
#
# Повторный запуск безопасен: обновляет код, токен берёт из сохранённого env.

set -euo pipefail

CHECKER_URL="${CHECKER_URL:-https://checker.nodewiki.info}"
APP_DIR="${APP_DIR:-/opt/nodewiki-probe}"
APP_USER="${APP_USER:-nwprobe}"
POLL_INTERVAL="${POLL_INTERVAL:-5}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe}"
AGENT_TOKEN="${AGENT_TOKEN:-}"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
envget() { [ -f "$2" ] && grep -h "^$1=" "$2" 2>/dev/null | head -1 | cut -d= -f2- || true; }

[ "$(id -u)" -eq 0 ] || die "Run as root (sudo)."
ENVF="$APP_DIR/nodewiki-probe.env"
[ -n "$AGENT_TOKEN" ] || AGENT_TOKEN="$(envget AGENT_TOKEN "$ENVF")"
[ -n "$AGENT_TOKEN" ] || die "AGENT_TOKEN обязателен (тот же, что CHECKER_AGENT_TOKEN на чекере):
  AGENT_TOKEN='...' bash deploy-probe.sh"

# ---- предупреждение, если канал похож на датацентровый ----------------------
if command -v curl >/dev/null 2>&1; then
  org="$(curl -fsS --max-time 8 'http://ip-api.com/line/?fields=hosting' 2>/dev/null || true)"
  [ "$org" = "true" ] && warn "Похоже, эта машина в датацентре — зонд тут не имеет смысла (нужен домашний/мобильный канал)."
fi

# ---- пакеты ------------------------------------------------------------------
log "Installing packages (python3, venv, curl, unzip)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv curl unzip ca-certificates

# ---- xray-core ----------------------------------------------------------------
if ! command -v xray >/dev/null 2>&1 && [ ! -x /usr/local/bin/xray ]; then
  log "Installing xray-core..."
  bash -c "$(curl -fsSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install \
    || die "Не удалось установить xray — без него зонд не может поднимать туннели."
  # их дефолтный xray-сервер не нужен — нам нужен только бинарь
  systemctl disable --now xray >/dev/null 2>&1 || true
fi
XRAY_BIN="$(command -v xray || echo /usr/local/bin/xray)"

# ---- приложение ----------------------------------------------------------------
id "$APP_USER" &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR"
log "Fetching probe_agent.py..."
curl -fsSL "$RAW_BASE/probe_agent.py" -o "$APP_DIR/probe_agent.py" || die "Не скачался probe_agent.py"
python3 -c "compile(open('$APP_DIR/probe_agent.py').read(),'p','exec')" || die "probe_agent.py не парсится."

log "Virtualenv & deps..."
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet "httpx[socks]"

log "Writing env file..."
cat > "$ENVF" <<EOF
CHECKER_URL=$CHECKER_URL
AGENT_TOKEN=$AGENT_TOKEN
XRAY_BIN=$XRAY_BIN
POLL_INTERVAL=$POLL_INTERVAL
EOF
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$ENVF"

# ---- systemd -------------------------------------------------------------------
log "Systemd service..."
cat > /etc/systemd/system/nodewiki-probe.service <<EOF
[Unit]
Description=nodewiki residential probe (VPN tunnel tester)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENVF
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/probe_agent.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable nodewiki-probe >/dev/null 2>&1 || true
systemctl restart nodewiki-probe

sleep 2
if systemctl is-active --quiet nodewiki-probe; then
  log "Зонд запущен и будет стартовать сам после перезагрузки."
else
  journalctl -u nodewiki-probe --no-pager -n 20
  die "Зонд не запустился — смотри лог выше."
fi

echo
log "Residential probe installed."
echo "  Чекер:   $CHECKER_URL (задачи берутся автоматически)"
echo "  Логи:    journalctl -u nodewiki-probe -f"
echo "  Рестарт: systemctl restart nodewiki-probe"
echo "  Напоминание: на этой машине не должно быть активного VPN/прокси."
