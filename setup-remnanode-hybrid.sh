#!/usr/bin/env bash
#
# Remnawave node — гибридная схема (Reality 443 + XHTTP-over-TLS 4443).
# Собирает ВСЕ данные в начале, генерит секреты, ставит базу (eGames),
# затем САМ подставляет всё в nginx.conf, открывает порт 4443 и выдаёт
# готовый Xray-конфиг для вставки в панель.
#
# Запуск root'ом на чистом VPS:
#   bash <(curl -fsSL https://scripts.nodewiki.info/raw/<id>)
# или, если данные задаёшь заранее (необязательно):
#   NODE_DOMAIN=node.example.com NODE_IP=1.2.3.4 NODE_TOKEN=... LE_EMAIL=me@x.io \
#     bash setup-remnanode-hybrid.sh
#
# Идемпотентно: секреты сохраняются в STATE-файл и переиспользуются при повторе.

set -euo pipefail

# ---- параметры (env переопределяет; иначе спросим) -------------------------
NODE_DOMAIN="${NODE_DOMAIN:-}"          # домен для XHTTP/4443 (A-запись -> IP ноды)
NODE_IP="${NODE_IP:-}"                  # IP этой ноды (как в панели)
NODE_TOKEN="${NODE_TOKEN:-}"            # секретный токен ноды из панели
LE_EMAIL="${LE_EMAIL:-}"               # почта для Let's Encrypt
NODE_LABEL="${NODE_LABEL:-}"           # метка ноды -> теги инбаундов (напр. de1)
REALITY_DEST="${REALITY_DEST:-www.microsoft.com:443}"  # external target Reality

# пути/константы инфраструктуры eGames remnanode
NGINX_CONF="${NGINX_CONF:-/opt/remnanode/nginx.conf}"
NGINX_CT="${NGINX_CT:-remnawave-nginx}"
NODE_CT="${NODE_CT:-remnanode}"
SOCKET="${SOCKET:-/dev/shm/xrxh.socket}"
XHTTP_PORT="${XHTTP_PORT:-4443}"
STATE_FILE="${STATE_FILE:-/opt/remnanode/.nodewiki-setup.env}"
OUT_FILE="${OUT_FILE:-/opt/remnanode/nodewiki-setup-output.txt}"
EGAMES_URL="https://raw.githubusercontent.com/eGamesAPI/remnawave-reverse-proxy/refs/heads/main/install_remnawave.sh"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
ask()  { # ask VAR "Вопрос" [default] — читаем из /dev/tty (стдин занят пайпом скрипта)
  local __v="$1" __q="$2" __d="${3:-}" __a=""
  [ -n "${!__v:-}" ] && return 0
  if [ -n "$__d" ]; then read -rp "  $__q [$__d]: " __a </dev/tty; __a="${__a:-$__d}"
  else read -rp "  $__q: " __a </dev/tty; fi
  [ -n "$__a" ] || die "Пустое значение для $__v"
  printf -v "$__v" '%s' "$__a"
}
pause() { read -rp "$1" _ </dev/tty; }  # ждать Enter с терминала

[ "$(id -u)" -eq 0 ] || die "Запусти от root (sudo)."
command -v openssl >/dev/null 2>&1 || die "Нужен openssl."

# curl | bash подаёт САМ скрипт в stdin (fd0) — bash читает команды оттуда.
# Поэтому fd0 НЕ трогаем (иначе bash начнёт читать остаток скрипта с клавиатуры),
# а каждый read и вложенный установщик берут ввод напрямую из /dev/tty.
[ -r /dev/tty ] || die "Нужен интерактивный терминал (нет /dev/tty)."

mkdir -p "$(dirname "$STATE_FILE")"

# ---- 0. подхватываем сохранённое состояние (повторный запуск) ---------------
# shellcheck disable=SC1090
[ -f "$STATE_FILE" ] && . "$STATE_FILE"

# ---- 1. собираем ВСЕ данные в начале ---------------------------------------
log "Данные ноды (вводятся один раз):"
ask NODE_DOMAIN "Домен ноды для XHTTP (A-запись на IP ноды)"
ask NODE_IP     "IP этой ноды"
ask NODE_TOKEN  "Секретный токен ноды (из панели)"
ask LE_EMAIL    "Почта для Let's Encrypt"
ask NODE_LABEL  "Короткая метка ноды (для тегов инбаундов)" "node1"

REALITY_SNI="${REALITY_DEST%%:*}"
CERT_DIR="${CERT_DIR:-/etc/nginx/ssl/$NODE_DOMAIN}"
TAG_REALITY="REALITY-$(echo "$NODE_LABEL" | tr '[:lower:]' '[:upper:]')"
TAG_XHTTP="XHTTP-$(echo "$NODE_LABEL" | tr '[:lower:]' '[:upper:]')"

# ---- 2. генерируем секреты (что можно — без контейнера) --------------------
XHTTP_SECRET="${XHTTP_SECRET:-$(openssl rand -hex 32)}"
SID1="${SID1:-$(openssl rand -hex 1)}"
SID2="${SID2:-$(openssl rand -hex 2)}"
SID4="${SID4:-$(openssl rand -hex 4)}"
SID8="${SID8:-$(openssl rand -hex 8)}"

# храним ТОЛЬКО сгенерированные секреты (чтобы при повторе они не менялись).
# Пользовательские поля (домен/IP/токен/почта) НЕ сохраняем — иначе прерванный
# запуск мог бы «зафиксировать» мусор и поломать повторную установку.
save_state() {
  umask 077
  cat > "$STATE_FILE" <<EOF
XHTTP_SECRET=$XHTTP_SECRET
SID1=$SID1
SID2=$SID2
SID4=$SID4
SID8=$SID8
REALITY_PRIV=${REALITY_PRIV:-}
REALITY_PUB=${REALITY_PUB:-}
EOF
}
save_state
log "Секреты сгенерированы и сохранены в $STATE_FILE"

# ---- 3. базовая установка eGames (меню проходишь руками) -------------------
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$NODE_CT"; then
  printf '\n\033[1;36m========= БАЗОВАЯ УСТАНОВКА (eGames) =========\033[0m\n'
  cat <<EOF
Сейчас запустится установщик eGames. Пройди меню РОВНО так:
    2  ->  1  ->  4  ->  1
Затем он спросит данные ноды — вставь подготовленные значения:
    Домен ноды : $NODE_DOMAIN
    IP ноды    : $NODE_IP
    Токен ноды : $NODE_TOKEN
Дальше: два раза Enter, потом  2 , потом почта:  $LE_EMAIL
После выпуска сертификата выйди из установщика обратно в консоль.
EOF
  printf '\033[1;36m=============================================\033[0m\n\n'
  pause "Готов запустить установщик? Enter — старт, Ctrl+C — отмена... "
  # установщику тоже отдаём терминал на stdin (иначе он не прочитает меню)
  bash <(curl -fsSL "$EGAMES_URL") </dev/tty || warn "Установщик завершился с ошибкой — проверим контейнеры ниже."
  echo
  pause "Нода добавлена и контейнеры подняты? Enter для продолжения... "
else
  log "Контейнер $NODE_CT уже есть — базовую установку пропускаю."
fi

command -v docker >/dev/null 2>&1 || die "docker не найден — базовая установка не прошла."
docker ps --format '{{.Names}}' | grep -qx "$NODE_CT"  || die "Контейнер $NODE_CT не запущен."
docker ps --format '{{.Names}}' | grep -qx "$NGINX_CT" || die "Контейнер $NGINX_CT не запущен."

# ---- 4. Reality keypair (нужен контейнер xray) -----------------------------
if [ -z "${REALITY_PRIV:-}" ] || [ -z "${REALITY_PUB:-}" ]; then
  log "Генерирую Reality keypair через $NODE_CT ..."
  X25519="$(docker exec "$NODE_CT" xray x25519 2>/dev/null)" || die "Не удалось вызвать xray x25519."
  REALITY_PRIV="$(echo "$X25519" | grep -iE 'private' | head -1 | awk '{print $NF}')"
  REALITY_PUB="$(echo "$X25519"  | grep -iE 'password|public' | head -1 | awk '{print $NF}')"
  [ -n "$REALITY_PRIV" ] && [ -n "$REALITY_PUB" ] || die "Не распарсил вывод xray x25519."
  save_state
fi

# ---- 5. nginx.conf с подстановкой (nginx-переменные экранированы \$) --------
if [ -f "$NGINX_CONF" ]; then
  cp -a "$NGINX_CONF" "$NGINX_CONF.bak.$(date +%s)"
  log "Бэкап старого конфига: $NGINX_CONF.bak.*"
fi
log "Пишу $NGINX_CONF ..."
cat > "$NGINX_CONF" <<EOF
# nginx TLS-frontend для XHTTP на порту $XHTTP_PORT (сгенерировано nodewiki)
# Reality на 443 nginx не использует — слушает напрямую на TCP/443
server_names_hash_bucket_size 64;

ssl_protocols TLSv1.2 TLSv1.3;
ssl_ecdh_curve X25519:prime256v1:secp384r1;
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
ssl_prefer_server_ciphers on;
ssl_session_timeout 1d;
ssl_session_cache shared:MozSSL:10m;
ssl_session_tickets off;

server {
    server_name $NODE_DOMAIN;
    listen $XHTTP_PORT ssl;
    listen [::]:$XHTTP_PORT ssl;
    http2 on;

    ssl_certificate         "$CERT_DIR/fullchain.pem";
    ssl_certificate_key     "$CERT_DIR/privkey.pem";
    ssl_trusted_certificate "$CERT_DIR/fullchain.pem";

    add_header X-Robots-Tag "noindex, nofollow, noarchive, nosnippet, noimageindex" always;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    server_tokens off;
    root /var/www/html;
    index index.html;

    location /api/v1/captcha/challenge/verify/$XHTTP_SECRET/ {
        client_max_body_size 0;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        client_body_timeout 5m;
        proxy_read_timeout 315s;
        proxy_send_timeout 5m;
        proxy_pass http://unix:$SOCKET;
    }

    location /api/v1/captcha/challenge/verify/ { return 404; }

    location = /favicon.ico { log_not_found off; access_log off; expires 7d; }
    location = /robots.txt  { log_not_found off; access_log off; }
    location = /sitemap.xml { log_not_found off; access_log off; }

    location / { try_files \$uri \$uri/ =404; }
}

server {
    listen $XHTTP_PORT ssl default_server;
    listen [::]:$XHTTP_PORT ssl default_server;
    server_name _;
    ssl_reject_handshake on;
    return 444;
}
EOF

# ---- 6. проверка сертификата + валидация + рестарт nginx -------------------
if ! docker exec "$NGINX_CT" test -f "$CERT_DIR/fullchain.pem" 2>/dev/null; then
  warn "В $NGINX_CT нет $CERT_DIR/fullchain.pem — проверь, что сертификат для $NODE_DOMAIN выпущен и примонтирован."
fi
log "Проверяю синтаксис nginx ..."
docker exec "$NGINX_CT" nginx -t || die "nginx -t не прошёл — конфиг не применён (бэкап рядом)."
docker restart "$NGINX_CT" >/dev/null
log "nginx перезапущен."

# ---- 7. порт 4443: firewall + проверка публикации контейнером --------------
if command -v ufw >/dev/null 2>&1; then
  ufw allow "$XHTTP_PORT/tcp" >/dev/null 2>&1 && log "ufw: открыт $XHTTP_PORT/tcp" || warn "ufw не открыл порт."
fi
if ! docker ps --format '{{.Names}} {{.Ports}}' | grep "$NGINX_CT" | grep -q ":$XHTTP_PORT->"; then
  warn "Контейнер $NGINX_CT не публикует $XHTTP_PORT наружу. Добавь в docker-compose проброс \"$XHTTP_PORT:$XHTTP_PORT\" и:"
  warn "  docker compose up -d --force-recreate $NGINX_CT"
fi

# ---- 8. готовый Xray-конфиг для панели + параметры хостов -------------------
XRAY_JSON=$(cat <<EOF
{
  "log": { "loglevel": "warning" },
  "dns": { "servers": [ { "address": "https://dns.google/dns-query", "skipFallback": false } ], "queryStrategy": "UseIPv4" },
  "inbounds": [
    {
      "tag": "$TAG_REALITY",
      "port": 443,
      "protocol": "vless",
      "settings": { "clients": [], "decryption": "none" },
      "sniffing": { "enabled": true, "destOverride": ["http", "tls", "quic"] },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "dest": "$REALITY_DEST",
          "show": false,
          "xver": 0,
          "spiderX": "",
          "shortIds": ["", "$SID1", "$SID2", "$SID4", "$SID8"],
          "privateKey": "$REALITY_PRIV",
          "serverNames": ["$REALITY_SNI"]
        }
      }
    },
    {
      "tag": "$TAG_XHTTP",
      "listen": "$SOCKET,0666",
      "protocol": "vless",
      "settings": { "clients": [], "decryption": "none" },
      "sniffing": { "enabled": true, "destOverride": ["http", "tls", "quic"] },
      "streamSettings": {
        "network": "xhttp",
        "xhttpSettings": {
          "mode": "auto",
          "path": "/api/v1/captcha/challenge/verify/$XHTTP_SECRET/",
          "extra": {
            "noSSEHeader": true,
            "xPaddingBytes": "100-1000",
            "XPaddingMethod": "repeat-x",
            "XPaddingHeader": "X-Captcha-Token",
            "XPaddingPlacement": "header",
            "XPaddingKey": "x_padding",
            "xPaddingObfsMode": false,
            "sessionPlacement": "header",
            "sessionKey": "X-Captcha-Session",
            "seqPlacement": "header",
            "seqKey": "X-Captcha-Seq",
            "scMaxBufferedPosts": 30,
            "scMaxEachPostBytes": 1000000,
            "scStreamUpServerSecs": "20-80"
          }
        }
      }
    }
  ],
  "outbounds": [
    { "tag": "DIRECT", "protocol": "freedom" },
    { "tag": "BLOCK", "protocol": "blackhole" }
  ],
  "routing": {
    "rules": [
      { "ip": ["geoip:private"], "type": "field", "outboundTag": "BLOCK" },
      { "type": "field", "protocol": ["bittorrent"], "outboundTag": "BLOCK" }
    ]
  }
}
EOF
)

{
  echo "================ NODEWIKI: РЕЗУЛЬТАТ УСТАНОВКИ ================"
  echo "Дата: $(date)"
  echo
  echo "Домен (XHTTP) : $NODE_DOMAIN"
  echo "IP ноды       : $NODE_IP"
  echo "Reality dest  : $REALITY_DEST   (SNI: $REALITY_SNI)"
  echo "Теги инбаундов: $TAG_REALITY (443) | $TAG_XHTTP ($XHTTP_PORT)"
  echo
  echo "--- СЕКРЕТЫ -------------------------------------------------"
  echo "Reality privateKey : $REALITY_PRIV"
  echo "Reality publicKey  : $REALITY_PUB     <- этот ключ идёт КЛИЕНТУ"
  echo "XHTTP secret (hex) : $XHTTP_SECRET"
  echo "shortIds           : $SID1 / $SID2 / $SID4 / $SID8"
  echo "XHTTP path         : /api/v1/captcha/challenge/verify/$XHTTP_SECRET/"
  echo
  echo "--- XRAY CONFIG (вставить в Remnawave -> Config Profiles) ---"
  echo "$XRAY_JSON"
  echo
  echo "--- HOST: Reality (443) ------------------------------------"
  echo "Inbound: $TAG_REALITY | Host: $NODE_DOMAIN | Port: 443 | Security: Reality"
  echo "SNI: $REALITY_SNI | Public key: $REALITY_PUB | shortId: $SID4 | Fingerprint: chrome"
  echo
  echo "--- HOST: XHTTP ($XHTTP_PORT) ------------------------------"
  echo "Inbound: $TAG_XHTTP | Host: $NODE_DOMAIN | Port: $XHTTP_PORT | Security: TLS"
  echo "Path: /api/v1/captcha/challenge/verify/$XHTTP_SECRET/ | SNI: $NODE_DOMAIN | ALPN: h2,http/1.1 | Fingerprint: chrome"
  echo "============================================================="
} | tee "$OUT_FILE"

echo
log "Готово. nginx и порт $XHTTP_PORT настроены автоматически."
log "Полный результат сохранён в $OUT_FILE"
warn "Осталось вручную: вставить Xray-конфиг в панель (Config Profiles) и завести два Host'а по данным выше."
