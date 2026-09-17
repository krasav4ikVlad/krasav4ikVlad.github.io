#!/usr/bin/env bash
#
# Обновление бота одной командой.
#
#     cd /home/rsvpn-bot && ./scripts/update.sh
#
# Режим техработ при обновлении НЕ включается. Перезапуск занимает секунды,
# и за это время человек в худшем случае не получает ответа на одно нажатие —
# а экран «идут технические работы» видели бы все, включая тех, кто просто
# зашёл посмотреть подписку. Лечение оказалось заметнее болезни.
#
# Когда работы всё-таки нужны — например, правится база или меняется панель, —
# включайте режим явно:
#
#     ./scripts/update.sh --maintenance     # включить на время обновления
#     python -m scripts.maintenance on      # включить отдельно, руками
#
# Порядок остальных шагов не косметический, каждый уже стоил разбора:
#
#   * зависимости ставятся ДО перезапуска. Наоборот — это запуск нового
#     кода со старыми библиотеками, то есть падение на импорте;
#   * pm2 restart не поднимает процессы, которых нет. После перезагрузки
#     сервера их может не быть вовсе, поэтому проверяем и стартуем;
#   * подписки переводятся на числовые id ПОСЛЕ перезапуска: этим занимается
#     новый код, а не старый.
#
# Скрипт останавливается на первой же ошибке. С --maintenance он в этом
# случае НЕ выключает режим техработ: лучше оставить людям честный экран
# ожидания, чем вернуть их в бота, который обновился наполовину.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PYTHON="$ROOT/.venv/bin/python"

MAINTENANCE=0
for arg in "$@"; do
    case "$arg" in
        --maintenance|-m) MAINTENANCE=1 ;;
        *) echo "Неизвестный аргумент: $arg"; echo "Можно только --maintenance"; exit 1 ;;
    esac
done

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

if [ ! -x "$PYTHON" ]; then
    echo "Нет виртуального окружения: $PYTHON"
    echo "Создать: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

if [ "$MAINTENANCE" = 1 ]; then
    say "Включаю режим техработ"
    "$PYTHON" -m scripts.maintenance on
fi

say "Забираю новую версию"
git pull

say "Ставлю зависимости"
"$PYTHON" -m pip install -q -r requirements.txt

say "Перезапускаю процессы"
# describe возвращает ненулевой код, когда процесса нет, — это не ошибка,
# а ответ на вопрос «запущен ли он».
if pm2 describe rsvpn-bot > /dev/null 2>&1; then
    pm2 restart rsvpn-bot rsvpn-api --update-env
else
    echo "Процессов не было — запускаю заново"
    pm2 start ecosystem.config.js
    pm2 save
fi

say "Жду, пока бот поднимется"
sleep 5
pm2 describe rsvpn-bot | grep -E 'status|restarts' || true

say "Проверяю окружение"
"$PYTHON" -m scripts.check_setup || echo "(проверка нашла замечания — см. выше)"

say "Перевожу подписки на числовые id панели, если она уже 3.x"
"$PYTHON" -m scripts.panel_ids fix

if [ "$MAINTENANCE" = 1 ]; then
    say "Выключаю режим техработ"
    "$PYTHON" -m scripts.maintenance off
fi

say "Готово"
"$PYTHON" -c 'from app.version import version_line; print("Версия:", version_line())'
