#!/usr/bin/env bash
#
# nodewiki — обновление кода без указания секретов.
# Скачивает свежий код для тех сервисов, что установлены на ЭТОЙ машине,
# и перезапускает их. Env-файлы (TOKEN_DB/SECRET_KEY/…) НЕ трогаются.
#
# Запуск root'ом:
#   bash <(curl -fsSL \
#     https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe/update.sh)
#
# Можно закрепить версию: REF=<commit|branch> bash <(curl ... update.sh)

set -euo pipefail

REF="${REF:-refs/heads/claude/script-hosting-app-msq5fe}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/$REF}"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root (sudo)."

updated=0

# file, service, pip-deps (доустановим, если венв есть)
update_one() {
  local dir="$1" file="$2" svc="$3" deps="$4"
  [ -d "$dir" ] || return 0
  log "Обновляю $svc ($file)…"
  local tmp; tmp="$(mktemp)"
  curl -fsSL "$RAW_BASE/$file" -o "$tmp" || { warn "не скачался $file"; rm -f "$tmp"; return 0; }
  python3 -c "compile(open('$tmp').read(),'$file','exec')" || { warn "$file не парсится — пропускаю"; rm -f "$tmp"; return 0; }
  mv "$tmp" "$dir/$file"
  # вернуть владельца сервисному пользователю (иначе он не прочитает файл)
  local owner; owner="$(stat -c '%U:%G' "$dir" 2>/dev/null || true)"
  [ -n "$owner" ] && chown "$owner" "$dir/$file" 2>/dev/null || true
  chmod 644 "$dir/$file" 2>/dev/null || true
  if [ -n "$deps" ] && [ -x "$dir/venv/bin/pip" ]; then
    "$dir/venv/bin/pip" install --quiet $deps || warn "pip: часть зависимостей не доустановилась"
  fi
  systemctl restart "$svc" 2>/dev/null || warn "не удалось перезапустить $svc"
  updated=1
}

# Script Vault (основной сервер)
update_one /opt/script-vault   app.py         script-vault   ""
# Hub (основной сервер)
update_one /opt/nodewiki-hub   hub_app.py     nodewiki-hub   ""
# VPN Checker (РУ-сервер)
update_one /opt/nodewiki-checker checker_app.py nodewiki-checker "httpx[socks]"

[ "$updated" = "1" ] || die "На этой машине не найдено ни одного сервиса nodewiki (/opt/script-vault, /opt/nodewiki-hub, /opt/nodewiki-checker)."

echo
log "Готово. Статусы:"
for s in script-vault nodewiki-hub nodewiki-checker; do
  systemctl is-active --quiet "$s" 2>/dev/null && echo "  $s: active" || true
done
echo "  Логи при проблемах: journalctl -u <сервис> -n 30 --no-pager"
