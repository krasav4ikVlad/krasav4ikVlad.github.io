#!/usr/bin/env bash
# Подтягивает новый код из master и перезагружает nginx, если конфиг менялся.
# Использование на сервере:  bash /var/www/nodewiki.info/deploy/update.sh

set -euo pipefail

WEBROOT="/var/www/nodewiki.info"
cd "$WEBROOT"

OLD_CONF_HASH=$(sha256sum deploy/nginx.conf 2>/dev/null | awk '{print $1}' || echo "")

git fetch --quiet
git reset --hard origin/master
chown -R www-data:www-data "$WEBROOT"

NEW_CONF_HASH=$(sha256sum deploy/nginx.conf | awk '{print $1}')

if [[ "$OLD_CONF_HASH" != "$NEW_CONF_HASH" ]]; then
  echo "nginx-конфиг изменился — применяю"
  install -m 0644 deploy/nginx.conf /etc/nginx/sites-available/nodewiki.info
  nginx -t
  systemctl reload nginx
fi

echo "обновлено до $(git rev-parse --short HEAD)"
