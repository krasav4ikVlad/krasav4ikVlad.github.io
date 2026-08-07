#!/usr/bin/env bash
# Установка VPN Analytics Dashboard на чистый Debian/Ubuntu сервер.
#
#   curl -fsSL https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/claude/vpn-analytics-dashboard-8uoklk/vpn-dashboard/deploy/install.sh | bash
#
# Скрипт идемпотентен: повторный запуск ничего не ломает.
set -euo pipefail

BRANCH="claude/vpn-analytics-dashboard-8uoklk"
REPO_URL="https://github.com/krasav4ikVlad/krasav4ikVlad.github.io.git"
REPO_DIR="${REPO_DIR:-$HOME/krasav4ikVlad.github.io}"
APP_DIR="$REPO_DIR/vpn-dashboard"

say()  { echo -e "\n\033[1;34m==>\033[0m $*"; }
warn() { echo -e "\033[1;33m!!\033[0m $*"; }

[ "$(id -u)" -eq 0 ] || { warn "Запустите от root"; exit 1; }

# --- базовые пакеты -------------------------------------------------------
say "Ставлю базовые пакеты"
apt-get update -qq
apt-get install -y -qq curl git ufw ca-certificates gnupg >/dev/null

# --- docker ---------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  say "Ставлю Docker"
  curl -fsSL https://get.docker.com | sh
else
  say "Docker уже установлен"
fi

# --- caddy (https-прокси) -------------------------------------------------
if ! command -v caddy >/dev/null 2>&1; then
  say "Ставлю Caddy"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
else
  say "Caddy уже установлен"
fi

# --- репозиторий ----------------------------------------------------------
if [ -d "$REPO_DIR/.git" ]; then
  say "Обновляю репозиторий"
  git -C "$REPO_DIR" fetch origin "$BRANCH" -q
  git -C "$REPO_DIR" checkout "$BRANCH" -q
  git -C "$REPO_DIR" pull origin "$BRANCH" -q
else
  say "Клонирую репозиторий"
  git clone -q -b "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi
cd "$APP_DIR"

# --- конфиг ---------------------------------------------------------------
if [ -f .env ]; then
  say ".env уже существует — оставляю как есть"
else
  say "Настройка (.env)"
  read -rp "Домен дашборда (например stats.nodemesh.media): " DOMAIN </dev/tty
  read -rp "MONGO_URI (строка подключения к базе бота): " MONGO_URI </dev/tty
  read -rp "Имя базы бота [RS_2]: " MONGO_DB </dev/tty
  MONGO_DB="${MONGO_DB:-RS_2}"
  read -rp "Remnawave URL (пусто = пропустить): " RW_URL </dev/tty
  read -rp "Remnawave API-токен (пусто = пропустить): " RW_TOKEN </dev/tty
  read -rp "TG_BOT_TOKEN для алертов (пусто = пропустить): " TG_TOKEN </dev/tty
  read -rp "TG_ADMIN_CHAT_ID (пусто = пропустить): " TG_CHAT </dev/tty
  read -rp "Логин админа [admin]: " ADMIN_USER </dev/tty
  ADMIN_USER="${ADMIN_USER:-admin}"
  read -rsp "Пароль админа (ввод скрыт): " ADMIN_PASS </dev/tty; echo

  say "Генерирую секреты"
  JWT_SECRET="$(openssl rand -hex 32)"
  ADMIN_HASH="$(docker run --rm -i python:3.11-slim sh -c \
    'pip install -q bcrypt && python -c "import bcrypt,sys; print(bcrypt.hashpw(sys.stdin.readline().rstrip(\"\n\").encode(), bcrypt.gensalt()).decode())"' \
    <<<"$ADMIN_PASS")"

  # bcrypt-хэш содержит $ — в .env строго в одинарных кавычках
  cat > .env <<ENV
MONGO_URI=${MONGO_URI}
MONGO_DB=${MONGO_DB}
USERS_COLLECTION=users
PAYMENTS_COLLECTION=payments_webhook
PAYMENTS_DB=

REMNAWAVE_API_URL=${RW_URL}
REMNAWAVE_TOKEN=${RW_TOKEN}

JWT_SECRET=${JWT_SECRET}
JWT_TTL_HOURS=72
ADMIN_USERNAME=${ADMIN_USER}
ADMIN_PASSWORD_HASH='${ADMIN_HASH}'
COOKIE_SECURE=true

TG_BOT_TOKEN=${TG_TOKEN}
TG_ADMIN_CHAT_ID=${TG_CHAT}

ETL_INTERVAL_MINUTES=5
ALERTS_INTERVAL_MINUTES=5
PROVIDER_SILENCE_HOURS=6
ALERT_COOLDOWN_HOURS=6

DASHBOARD_PORT=8080
LOG_LEVEL=INFO
ENV
  echo "DOMAIN=${DOMAIN}" > .install-domain
fi

DOMAIN="${DOMAIN:-$(cut -d= -f2 .install-domain 2>/dev/null || true)}"

# --- порт 443 -------------------------------------------------------------
if ss -ltn 2>/dev/null | grep -q ':443 '; then
  HOLDER="$(ss -ltnp 2>/dev/null | grep ':443 ' | grep -oP 'users:\(\("\K[^"]+' | head -1 || true)"
  if [ "$HOLDER" != "caddy" ] && [ -n "$HOLDER" ]; then
    warn "Порт 443 занят процессом '$HOLDER' (нода Remnawave?)."
    warn "Освободите его или повесьте дашборд на другой порт — см. README."
  fi
fi

# --- caddy config ---------------------------------------------------------
if [ -n "${DOMAIN:-}" ]; then
  say "Настраиваю Caddy для https://${DOMAIN}"
  printf '%s {\n    reverse_proxy 127.0.0.1:8080\n}\n' "$DOMAIN" > /etc/caddy/Caddyfile
  systemctl enable -q caddy || true
  systemctl restart caddy
fi

# --- firewall -------------------------------------------------------------
say "Настраиваю firewall (SSH, 80, 443)"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

# --- запуск ---------------------------------------------------------------
say "Собираю и запускаю контейнеры (первый раз ~5 минут)"
docker compose up -d --build

say "Готово!"
echo
echo "  Дашборд:   https://${DOMAIN:-<домен>}"
echo "  Статус:    https://${DOMAIN:-<домен>}/api/health (etl_tx_rows > 0 = данные развёрнуты)"
echo "  Логи:      cd $APP_DIR && docker compose logs backend --tail 30"
echo
echo "  НЕ ЗАБУДЬТЕ:"
echo "  1. A-запись домена → IP этого сервера ($(curl -4 -s icanhazip.com || echo '?')), в Cloudflare — серое облачко (DNS only)."
echo "  2. В панели Timeweb добавить этот IP в список доступа к MongoDB."
echo "  3. Первый прогон ETL занимает несколько минут — разделы наполнятся после него."
