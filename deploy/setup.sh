#!/usr/bin/env bash
# Первоначальная настройка чистого Debian 11/12 под nodewiki.info.
# Запускать от root:
#   bash <(curl -sSL https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/master/deploy/setup.sh)
#
# Делает:
#   1. ставит nginx, PHP-FPM, sqlite, certbot, git
#   2. клонирует репо в /var/www/nodewiki.info
#   3. ставит права (data/ и includes/ — записываемые www-data)
#   4. прописывает nginx-конфиг и перезагружает
#   5. выпускает TLS-сертификаты через certbot

set -euo pipefail

REPO="https://github.com/krasav4ikVlad/krasav4ikVlad.github.io.git"
WEBROOT="/var/www/nodewiki.info"
DOMAINS=(nodewiki.info www.nodewiki.info scripts.nodewiki.info)
EMAIL="${LETSENCRYPT_EMAIL:-admin@nodewiki.info}"

if [[ $EUID -ne 0 ]]; then
  echo "Запусти от root." >&2; exit 1
fi

echo "==> 1. Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y nginx git curl ca-certificates \
                   php-fpm php-sqlite3 php-cli php-mbstring \
                   certbot python3-certbot-nginx

echo "==> 2. Репозиторий в $WEBROOT"
if [[ -d "$WEBROOT/.git" ]]; then
  git -C "$WEBROOT" pull --ff-only
else
  rm -rf "$WEBROOT"
  git clone "$REPO" "$WEBROOT"
fi

echo "==> 3. Права"
chown -R www-data:www-data "$WEBROOT"
mkdir -p "$WEBROOT/data"
chown www-data:www-data "$WEBROOT/data" "$WEBROOT/includes"
chmod 750 "$WEBROOT/data" "$WEBROOT/includes"

echo "==> 4. nginx"
install -m 0644 "$WEBROOT/deploy/nginx.conf" /etc/nginx/sites-available/nodewiki.info
ln -sf /etc/nginx/sites-available/nodewiki.info /etc/nginx/sites-enabled/nodewiki.info
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> 5. Сертификаты Let's Encrypt"
DOMAIN_ARGS=()
for d in "${DOMAINS[@]}"; do DOMAIN_ARGS+=(-d "$d"); done
certbot --nginx --non-interactive --agree-tos -m "$EMAIL" \
        --redirect "${DOMAIN_ARGS[@]}"

systemctl reload nginx

cat <<EOF

Готово.

  Главная:    https://nodewiki.info
  Список:     https://scripts.nodewiki.info/
  Админка:    https://scripts.nodewiki.info/admin
  Сырой curl: https://scripts.nodewiki.info/<slug>

Для последующих обновлений: bash $WEBROOT/deploy/update.sh
EOF
