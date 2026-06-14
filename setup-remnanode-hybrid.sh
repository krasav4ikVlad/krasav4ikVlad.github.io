#!/usr/bin/env bash
#
# Remnawave node — быстрая установка (гибридная схема).
# Спрашивает в консоли домен, IP панели, токен и почту — и делает РОВНО 4 вещи:
#   1) ставит базу eGames (меню прокликивает САМ — автопилот),
#   2) пишет твой nginx.conf, меняя в нём ТОЛЬКО домен,
#   3) перезапускает nginx,
#   4) открывает порт 4443.
# IP ноды определяется сам. Никаких ключей/секретов не генерит — inbound'ы
# приходят из панели, XHTTP-путь в nginx фиксированный (см. константу ниже).
#
# Запуск:  curl -fsSL https://scripts.nodewiki.info/raw/<id> | bash
#     или: bash <(curl -fsSL https://scripts.nodewiki.info/raw/<id>)
# Меню eGames пройти руками:  EGAMES_AUTO=0 bash <(curl -fsSL .../<id>)

set -euo pipefail

# ============================================================================
# ЗАПОЛНИ ОДИН РАЗ (одинаково для ВСЕХ твоих нод) — единый XHTTP-путь из nginx.
# Это тот самый путь с секретом, что прописан и в панели (XHTTP inbound).
# ============================================================================
XHTTP_PATH="${XHTTP_PATH:-/api/v1/captcha/challenge/verify/REPLACE_WITH_YOUR_FIXED_SECRET/}"
# ============================================================================

DOMAIN="${DOMAIN:-${NODE_DOMAIN:-}}"          # домен ноды (единственный вопрос)
NGINX_CONF="${NGINX_CONF:-/opt/remnanode/nginx.conf}"
NGINX_CT="${NGINX_CT:-remnawave-nginx}"
NODE_CT="${NODE_CT:-remnanode}"
SOCKET="${SOCKET:-/dev/shm/xrxh.socket}"
XHTTP_PORT="${XHTTP_PORT:-4443}"
CERT_DIR_BASE="${CERT_DIR_BASE:-/etc/nginx/ssl}"
EGAMES_URL="https://raw.githubusercontent.com/eGamesAPI/remnawave-reverse-proxy/refs/heads/main/install_remnawave.sh"

# автопилот eGames (по умолчанию ВКЛ): сам прокликивает меню 1->4->1 и вводит
# домен, дальше передаёт управление тебе. Пройти меню руками — EGAMES_AUTO=0.
EGAMES_AUTO="${EGAMES_AUTO:-1}"               # 1 = автопрокликивание через expect
EGAMES_LANG="${EGAMES_LANG:-2}"               # язык eGames: 1=English, 2=Русский

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
pause(){ read -rp "$1" _ </dev/tty; }          # ввод берём из терминала (стдин занят пайпом)
ask()  { # ask VAR "Вопрос"
  local __v="$1" __q="$2" __a=""
  [ -n "${!__v:-}" ] && return 0
  read -rp "  $__q: " __a </dev/tty
  [ -n "$__a" ] || die "Пустое значение для $__v"
  printf -v "$__v" '%s' "$__a"
}

[ "$(id -u)" -eq 0 ] || die "Запусти от root (sudo)."
[ -r /dev/tty ] || die "Нужен интерактивный терминал (нет /dev/tty)."
case "$XHTTP_PATH" in
  *REPLACE_WITH_YOUR_FIXED_SECRET*)
    die "Сначала впиши свой единый XHTTP-путь в константу XHTTP_PATH вверху скрипта (один раз).";;
esac

# ---- 0. сам узнаём IP ноды --------------------------------------------------
detect_ip() {
  local ip u
  for u in https://api.ipify.org https://ifconfig.me/ip https://ipinfo.io/ip; do
    ip="$(curl -fsS --max-time 5 "$u" 2>/dev/null | tr -d '[:space:]')" || true
    [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && { printf '%s' "$ip"; return 0; }
  done
  ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' | head -1
}
NODE_IP="$(detect_ip || true)"
[ -n "$NODE_IP" ] && log "IP ноды определён: $NODE_IP" || warn "Не удалось определить IP автоматически."

# ---- 1. домен (единственный вопрос) ----------------------------------------
if [ -z "$DOMAIN" ]; then
  read -rp "Домен ноды (A-запись на $NODE_IP): " DOMAIN </dev/tty
fi
[ -n "$DOMAIN" ] || die "Домен обязателен."
CERT_DIR="$CERT_DIR_BASE/$DOMAIN"

# expect-автопилот: ждёт каждый маркер [?] и шлёт следующий ответ из очереди,
# затем отдаёт управление тебе (interact) — на случай неожиданного вопроса.
egames_autopilot() {
  if ! command -v expect >/dev/null 2>&1; then
    log "Ставлю expect…"
    apt-get update -y >/dev/null 2>&1 || true
    apt-get install -y expect >/dev/null 2>&1 || die "Не удалось установить expect."
  fi

  local sh exp
  sh="$(mktemp /tmp/egames.XXXXXX.sh)"
  exp="$(mktemp /tmp/egames.XXXXXX.exp)"
  curl -fsSL "$EGAMES_URL" -o "$sh" || die "Не скачался установщик eGames."
  export SELFSTEAL_DOMAIN="$DOMAIN" EGAMES_SH="$sh"

  # Автопилот делает только ДЕТЕРМИНИРОВАННУЮ часть: меню 1->4->1 + ввод домена
  # (всё это обычные [?]-вопросы). Дальше у модуля eGames идут «скрытые»
  # подтверждения (токен + двойной Enter и т.п.), которых я не вижу, — поэтому
  # отдаём управление пользователю (interact): остальное он вводит сам.
  cat > "$exp" <<'EXP'
set timeout -1
set queue [list 1 4 1 $env(SELFSTEAL_DOMAIN)]
spawn bash $env(EGAMES_SH)
foreach a $queue {
    expect -ex {[?]}
    send -- "$a\r"
}
send_user "\n\n===> Автопилот прошёл меню и ввёл домен. ДАЛЬШЕ ВВОДИ САМ:\n     IP панели  ->  токен (подтверди двойным Enter, как обычно)  ->  метод серта 2  ->  почта.\n\n"
interact
EXP
  log "Автопилот eGames: язык + меню 1→4→1 + домен. Остальное введёшь сам (так надёжнее)."
  expect -f "$exp" </dev/tty || warn "expect завершился с ошибкой — проверю контейнеры ниже."
  rm -f "$sh" "$exp"
}

# ---- 2. база eGames --------------------------------------------------------
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$NODE_CT"; then
  # язык выбираем заранее (файлом) — пропускаем вопрос про язык и его «Invalid choice»
  mkdir -p /usr/local/remnawave_reverse
  [ -f /usr/local/remnawave_reverse/selected_language ] || echo "$EGAMES_LANG" > /usr/local/remnawave_reverse/selected_language

  if [ "$EGAMES_AUTO" = "1" ]; then
    egames_autopilot
  else
    printf '\n\033[1;36m========= БАЗОВАЯ УСТАНОВКА (eGames) =========\033[0m\n'
    cat <<EOF
Язык уже выбран (Русский). Пройди меню РОВНО так:
    1  ->  4  ->  1        (Компоненты -> Установить ноду -> Nginx)
Когда спросит данные ноды — введи:
    Selfsteal домен : $DOMAIN
    IP панели       : <IP сервера с панелью>
    Токен ноды      : <вставь из панели>
Метод сертификата:  2  (ACME), затем своя почта.
После выпуска сертификата выйди из установщика обратно в консоль.
EOF
    printf '\033[1;36m=============================================\033[0m\n\n'
    pause "Enter — запустить установщик (Ctrl+C — отмена)... "
    bash <(curl -fsSL "$EGAMES_URL") </dev/tty || warn "Установщик завершился с ошибкой — проверю контейнеры ниже."
    echo
    pause "Нода добавлена и контейнеры подняты? Enter для продолжения... "
  fi
else
  log "Контейнер $NODE_CT уже есть — установку eGames пропускаю."
fi

command -v docker >/dev/null 2>&1 || die "docker не найден — база не установилась."
docker ps --format '{{.Names}}' | grep -qx "$NGINX_CT" || die "Контейнер $NGINX_CT не запущен."

# ---- 3. nginx.conf: меняем ТОЛЬКО домен ------------------------------------
# единый путь XHTTP_PATH и его префикс для анти-пробинга
_p="${XHTTP_PATH%/}"; XHTTP_PREFIX="${_p%/*}/"

if [ -f "$NGINX_CONF" ]; then
  cp -a "$NGINX_CONF" "$NGINX_CONF.bak.$(date +%s)"
  log "Бэкап старого конфига: $NGINX_CONF.bak.*"
fi
log "Пишу $NGINX_CONF (домен: $DOMAIN) ..."
cat > "$NGINX_CONF" <<EOF
# nginx TLS-frontend для XHTTP на $XHTTP_PORT (домен подставлен автоматически)
server_names_hash_bucket_size 64;

ssl_protocols TLSv1.2 TLSv1.3;
ssl_ecdh_curve X25519:prime256v1:secp384r1;
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
ssl_prefer_server_ciphers on;
ssl_session_timeout 1d;
ssl_session_cache shared:MozSSL:10m;
ssl_session_tickets off;

server {
    server_name $DOMAIN;
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

    location $XHTTP_PATH {
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

    location $XHTTP_PREFIX { return 404; }

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

if ! docker exec "$NGINX_CT" test -f "$CERT_DIR/fullchain.pem" 2>/dev/null; then
  warn "В $NGINX_CT нет $CERT_DIR/fullchain.pem — проверь, что сертификат для $DOMAIN выпущен и примонтирован."
fi

# ---- 3b. перезапуск nginx ---------------------------------------------------
log "Проверяю синтаксис nginx ..."
docker exec "$NGINX_CT" nginx -t || die "nginx -t не прошёл — конфиг не применён (бэкап рядом)."
docker restart "$NGINX_CT" >/dev/null
log "nginx перезапущен."

# ---- 4. порт 4443 -----------------------------------------------------------
if command -v ufw >/dev/null 2>&1; then
  ufw allow "$XHTTP_PORT/tcp" >/dev/null 2>&1 && log "ufw: открыт $XHTTP_PORT/tcp" || warn "ufw не открыл порт."
else
  log "ufw не найден — пропускаю (открой $XHTTP_PORT в своём фаерволе, если он есть)."
fi
if ! docker ps --format '{{.Names}} {{.Ports}}' | grep "$NGINX_CT" | grep -q ":$XHTTP_PORT->"; then
  warn "Контейнер $NGINX_CT не публикует $XHTTP_PORT наружу. Добавь в docker-compose проброс \"$XHTTP_PORT:$XHTTP_PORT\" и:"
  warn "  docker compose up -d --force-recreate $NGINX_CT"
fi

echo
log "Готово: домен $DOMAIN, nginx обновлён и перезапущен, порт $XHTTP_PORT открыт."
