#!/usr/bin/env bash
#
# Remnawave node — реальный Let's Encrypt сертификат (Docker certbot) + проброс
# в remnanode + автопродление. Спрашивает домен и почту, IP определяет сам.
# В конце печатает JSON Hysteria2-инбаунда с подставленным доменом (для панели).
#
# ВНИМАНИЕ: профиль "protocol": hysteria — НЕ из стандартного xray-core
# (remnanode). Проверь, что твоя нода его принимает, прежде чем полагаться.
# Серверная часть (серт/проброс/cron) валидна в любом случае.
#
# Запуск:  bash <(curl -fsSL https://scripts.nodewiki.info/raw/<id>)
#     или: DOMAIN=node.example.com EMAIL=you@mail.io bash <(curl -fsSL .../<id>)

set -euo pipefail

DOMAIN="${DOMAIN:-${NODE_DOMAIN:-}}"
EMAIL="${EMAIL:-${LE_EMAIL:-}}"
CERTBOT_DIR="${CERTBOT_DIR:-/opt/certbot}"
NODE_DIR="${NODE_DIR:-/opt/remnanode}"
NODE_CT="${NODE_CT:-remnanode}"
COMPOSE="${COMPOSE:-$NODE_DIR/docker-compose.yml}"
MOUNT="$CERTBOT_DIR/certs:/etc/letsencrypt:ro"
OUT_FILE="${OUT_FILE:-$NODE_DIR/hysteria2-inbound.json}"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
pause(){ read -rp "$1" _ </dev/tty; }

[ "$(id -u)" -eq 0 ] || die "Запусти от root (sudo)."
[ -r /dev/tty ] || die "Нужен интерактивный терминал (нет /dev/tty)."
command -v docker >/dev/null 2>&1 || die "docker не найден — сначала разверни ноду (eGames)."
docker compose version >/dev/null 2>&1 || die "docker compose не найден."
[ -f "$COMPOSE" ] || die "Не найден $COMPOSE — это запускается на сервере с remnanode."

# ---- IP ноды (сам) ----------------------------------------------------------
detect_ip() {
  local ip u
  for u in https://api.ipify.org https://ifconfig.me/ip https://ipinfo.io/ip; do
    ip="$(curl -fsS --max-time 5 "$u" 2>/dev/null | tr -d '[:space:]')" || true
    [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && { printf '%s' "$ip"; return 0; }
  done
  ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' | head -1
}
NODE_IP="$(detect_ip || true)"
[ -n "$NODE_IP" ] && log "IP ноды: $NODE_IP" || warn "IP не определился автоматически."

# ---- вопросы (домен + почта) ------------------------------------------------
if [ -z "$DOMAIN" ]; then read -rp "Домен ноды (A-запись на $NODE_IP): " DOMAIN </dev/tty; fi
[ -n "$DOMAIN" ] || die "Домен обязателен."
if [ -z "$EMAIL" ]; then read -rp "Почта для Let's Encrypt: " EMAIL </dev/tty; fi
[ -n "$EMAIL" ] || die "Почта обязательна."

# ---- порт 80 должен быть свободен для certbot --standalone ------------------
if ss -ltn 2>/dev/null | grep -qE '[0-9.]+:80\s'; then
  warn "Порт 80 занят — certbot --standalone не сможет подтвердить домен."
  warn "Освободи 80 (например, временно останови nginx/контейнер на 80) и запусти снова."
  pause "Если уверен, что 80 свободен — Enter; иначе Ctrl+C... "
fi

# ---- 1. certbot: docker-compose + получение сертификата --------------------
log "Готовлю certbot ($CERTBOT_DIR)…"
mkdir -p "$CERTBOT_DIR"
cat > "$CERTBOT_DIR/docker-compose.yml" <<'YML'
services:
  certbot:
    image: certbot/certbot
    network_mode: host
    volumes:
      - ./certs:/etc/letsencrypt
      - ./var-lib:/var/lib/letsencrypt
YML

LIVE="$CERTBOT_DIR/certs/live/$DOMAIN"
if [ -f "$LIVE/fullchain.pem" ]; then
  log "Сертификат для $DOMAIN уже есть — пропускаю выпуск."
else
  log "Выпускаю сертификат для $DOMAIN…"
  docker compose -f "$CERTBOT_DIR/docker-compose.yml" run --rm certbot \
    certonly --standalone --non-interactive --agree-tos \
    --email "$EMAIL" -d "$DOMAIN" \
    || die "certbot не выпустил сертификат (порт 80? DNS не на $NODE_IP?)."
  [ -f "$LIVE/fullchain.pem" ] || die "Сертификат не появился в $LIVE."
fi

# ---- 2. проброс сертификата в remnanode (через yq, идемпотентно) -----------
need_mikefarah_yq() { command -v yq >/dev/null 2>&1 && yq --version 2>/dev/null | grep -qi mikefarah; }
if ! need_mikefarah_yq; then
  log "Ставлю yq (mikefarah)…"
  curl -fsSL https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 \
    -o /usr/local/bin/yq && chmod +x /usr/local/bin/yq
  hash -r
fi

if grep -q "$CERTBOT_DIR/certs:/etc/letsencrypt" "$COMPOSE"; then
  log "Сертификат уже примонтирован в $COMPOSE — пропускаю."
else
  cp -a "$COMPOSE" "$COMPOSE.bak.$(date +%s)"
  log "Добавляю volume с сертификатом в $COMPOSE (бэкап рядом)…"
  yq -i ".services.\"$NODE_CT\".volumes += [\"$MOUNT\"]" "$COMPOSE" \
    || die "Не удалось вписать volume через yq — проверь структуру $COMPOSE."
fi

# ---- 3. рестарт ноды --------------------------------------------------------
log "Перезапускаю ноду…"
( cd "$NODE_DIR" && docker compose down && docker compose up -d ) \
  || die "Не удалось перезапустить ноду (docker compose)."

# ---- 4. cron на автопродление (+ рестарт ноды, чтобы серт подхватился) ------
CRON_LINE="0 0 28 * * cd $CERTBOT_DIR && docker compose run --rm certbot renew && docker restart $NODE_CT"
if crontab -l 2>/dev/null | grep -Fq "docker compose run --rm certbot renew"; then
  log "Cron на продление уже есть."
else
  ( crontab -l 2>/dev/null; echo "$CRON_LINE" ) | crontab -
  log "Добавил cron: продление 28-го числа + рестарт ноды."
fi

# ---- 5. JSON инбаунда для панели (с подставленным доменом) -----------------
cat > "$OUT_FILE" <<JSON
{
  "log": { "loglevel": "none" },
  "inbounds": [
    {
      "tag": "HYSTERIA-BBR",
      "port": 443,
      "listen": "0.0.0.0",
      "protocol": "hysteria",
      "settings": { "clients": [], "version": 2 },
      "streamSettings": {
        "network": "hysteria",
        "security": "tls",
        "finalmask": { "quicParams": { "debug": false, "congestion": "bbr" } },
        "tlsSettings": {
          "alpn": ["h3"],
          "certificates": [
            {
              "keyFile": "/etc/letsencrypt/live/$DOMAIN/privkey.pem",
              "certificateFile": "/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
            }
          ]
        },
        "hysteriaSettings": { "version": 2 }
      }
    }
  ],
  "outbounds": [
    { "tag": "DIRECT", "protocol": "freedom" },
    { "tag": "BLOCK", "protocol": "blackhole" }
  ],
  "routing": {
    "rules": [
      { "ip": ["geoip:private"], "outboundTag": "BLOCK" },
      { "domain": ["geosite:private"], "outboundTag": "BLOCK" },
      { "protocol": ["bittorrent"], "outboundTag": "BLOCK" }
    ]
  }
}
JSON

echo
log "Готово: сертификат для $DOMAIN получен, примонтирован, нода перезапущена, cron настроен."
log "JSON инбаунда для панели сохранён в $OUT_FILE — вставь его в Config Profile:"
echo "------------------------------------------------------------"
cat "$OUT_FILE"
echo "------------------------------------------------------------"
warn "ПРОВЕРЬ: остаётся ли нода живой после вставки этого профиля."
warn "Стандартный xray-core НЕ поддерживает protocol \"hysteria\" — если нода"
warn "упадёт с ошибкой протокола, значит этот профиль ей не подходит (нужен"
warn "sing-box-нода/форк), а серт/проброс/cron при этом всё равно настроены."
