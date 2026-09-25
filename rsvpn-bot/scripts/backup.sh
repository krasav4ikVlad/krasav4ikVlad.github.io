#!/usr/bin/env bash
#
# Собрать всё, чего нет в git, в один архив — для переезда на другой сервер.
#
#     ./scripts/backup.sh
#
# Код переедет через `git clone`, а вот это — нет, потому что и не должно
# лежать в репозитории:
#
#   .env             ключи платёжек, токен бота, доступ к базе
#   emoji_ids.json   свои значки (иначе бот поедет на стандартных)
#   media/           картинки экранов (иначе экраны будут без них)
#
# Именно этот список забывают при переезде, и обнаруживается это уже на
# новом сервере: бот поднялся, но без картинок, со стандартными значками и
# без единого платёжного ключа.
#
# База сюда НЕ входит: она живёт отдельной машиной, и переезд бота её не
# касается. Снимок базы — mongodump, и это отдельное решение.
#
# В архиве секреты. Кладём его за пределы репозитория, закрываем от чужих
# глаз и удаляем с обеих машин сразу после переезда.

set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

STAMP="$(date +%F-%H%M)"
OUT="${HOME}/rsvpn-move-${STAMP}.tar.gz"

have=()
for item in .env emoji_ids.json media; do
    if [ -e "$item" ]; then
        have+=("$item")
    else
        echo "⚠️  нет $item — пропускаю"
    fi
done

if [ ${#have[@]} -eq 0 ]; then
    echo "❌ Нечего собирать: ни .env, ни картинок. Вы точно в каталоге бота?"
    exit 1
fi

# INCY-энкодер лежит вне репозитория (см. INCY_ENCODER в .env) — без него
# на новом сервере не соберётся ни одна ссылка INCY.
ENCODER="$(grep -E '^INCY_ENCODER=' .env 2>/dev/null | cut -d= -f2- | tr -d ' ' || true)"
EXTRA=()
if [ -n "${ENCODER:-}" ] && [ -f "$ENCODER" ]; then
    cp "$ENCODER" ./incy_encode.mjs.backup
    EXTRA+=(incy_encode.mjs.backup)
    echo "✅ добавил энкодер INCY: $ENCODER"
elif [ -n "${ENCODER:-}" ]; then
    echo "⚠️  INCY_ENCODER указывает на $ENCODER, а файла там нет"
fi

tar -czf "$OUT" "${have[@]}" "${EXTRA[@]+"${EXTRA[@]}"}"
rm -f ./incy_encode.mjs.backup
chmod 600 "$OUT"

echo
echo "✅ Готово: $OUT"
du -h "$OUT" | cut -f1 | sed 's/^/   размер: /'
echo
cat <<HINT
Что дальше (с НОВОГО сервера):

  scp root@СТАРЫЙ_IP:${OUT} .
  tar -xzf $(basename "$OUT") -C /home/rsvpn-bot

В архиве ключи платёжек и токен бота. Удалите его с обеих машин сразу
после переезда:  rm -f ${OUT}

Порядок всего переезда — docs/MOVE_SERVER.md
HINT
