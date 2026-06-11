#!/usr/bin/env bash
#
# nodewiki hub — deploy the landing page at the apex domain.
# Static page served by nginx + Let's Encrypt TLS. Coexists with the
# scripts.nodewiki.info vhost (separate server_name, nothing removed).
#
# Run as root ON THE MAIN SERVER (the one that already serves scripts):
#
#   bash <(curl -fsSL \
#     https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe/deploy-hub.sh)
#
# DNS first: in Cloudflare add A-records (grey cloud / DNS-only) for
#   nodewiki.info      -> <this server IP>
#   www.nodewiki.info  -> <this server IP>
# (same as scripts.nodewiki.info). Re-running is safe (idempotent).

set -euo pipefail

DOMAIN="${DOMAIN:-nodewiki.info}"
ALT="${ALT:-www.nodewiki.info}"
WEBROOT="${WEBROOT:-/var/www/nodewiki-hub}"
LE_EMAIL="${LE_EMAIL:-prostotvinkazaza@gmail.com}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe}"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root (sudo)."

# ---- preflight: no other vhost may claim our domain -------------------------
# (this is exactly what broke scripts.nodewiki.info before: a legacy config
#  held the domain and won over the new vhost)
# -R (а не -r): sites-enabled состоит из симлинков, -r их пропускает.
# Сравниваем ТОЧНЫЙ токен домена: scripts.nodewiki.info конфликтом не считается.
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

# ---- packages (nginx + certbot are usually already there from scripts) -----
if ! command -v nginx >/dev/null 2>&1 || ! command -v certbot >/dev/null 2>&1; then
  log "Installing nginx + certbot..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y nginx certbot python3-certbot-nginx curl ca-certificates
fi

# ---- fetch the static page -------------------------------------------------
log "Fetching hub page..."
mkdir -p "$WEBROOT"
curl -fsSL "$RAW_BASE/hub/index.html" -o "$WEBROOT/index.html" \
  || die "Could not download hub/index.html from $RAW_BASE"
chown -R www-data:www-data "$WEBROOT" 2>/dev/null || true

# ---- nginx vhost (HTTP; certbot adds TLS) ----------------------------------
log "Configuring nginx for $DOMAIN ..."
cat > /etc/nginx/sites-available/nodewiki-hub <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN $ALT;

    root $WEBROOT;
    index index.html;

    location / {
        try_files \$uri \$uri/ =404;
    }
}
EOF
ln -sf /etc/nginx/sites-available/nodewiki-hub /etc/nginx/sites-enabled/nodewiki-hub
nginx -t
systemctl reload nginx

# ---- TLS via Let's Encrypt (HTTP-01; both names must resolve here) ---------
log "Requesting Let's Encrypt certificate for $DOMAIN, $ALT ..."
if certbot --nginx -d "$DOMAIN" -d "$ALT" --non-interactive --agree-tos -m "$LE_EMAIL" --redirect; then
  SCHEME="https"
else
  warn "certbot failed — the hub is live on HTTP for now."
  warn "Ensure $DOMAIN and $ALT both resolve to THIS server and port 80 is open, then run:"
  warn "  certbot --nginx -d $DOMAIN -d $ALT --agree-tos -m $LE_EMAIL --redirect"
  SCHEME="http"
fi

echo
log "Hub deployed:  $SCHEME://$DOMAIN/"
echo "  Page file:  $WEBROOT/index.html"
echo "  Update:     re-run this script (re-fetches the page)"
