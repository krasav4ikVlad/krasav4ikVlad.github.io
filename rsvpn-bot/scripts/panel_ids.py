"""Перевод подписок на числовые id панели Remnawave 3.x — из терминала.

    python -m scripts.panel_ids          # только посмотреть
    python -m scripts.panel_ids fix      # перевести

То же, что команда /panelids в боте, но из терминала: в день обновления
панели бот может быть как раз недоступен, а перевод занимает минуты, и
смотреть на него удобнее в консоли.

В обновление бота (scripts/update.sh) этот шаг не входит: он идёт по всей
базе и к выкладке кода отношения не имеет. Зовите его руками — в тот день,
когда меняется сама панель, и после отката, если он случится.
"""

from __future__ import annotations

import asyncio
import sys

ANSWERS = {
    'nothing': 'Все подписки опознаются панелью — переводить нечего.',
    'unknown': 'Панель не ответила — версию выяснить не вышло. '
               'Проверьте адрес и токен в .env.',
}

# Направление зависит от панели, а не от нашего намерения: после отката с
# 3.x на 2.x переводить надо ровно наоборот.
DIRECTIONS = {
    'to_id': ('панель уже 3.x', 'числовые id'),
    'to_uuid': ('панель откатили на 2.x', 'прежние uuid'),
}


async def main() -> int:
    from app.core.config import Config
    from app.core.container import Container
    from app.services import panel_ids

    apply = 'fix' in [arg.lower() for arg in sys.argv[1:]]

    try:
        config = Config.from_env()
    except RuntimeError as exc:
        print(f'Конфиг: {exc}')
        return 1

    container = Container.build(config)
    if container.vpn is None:
        print('Клиент панели не собран: проверьте REMNAWAVE_URL и токен в .env.')
        return 1

    try:
        report = await panel_ids.migrate(container.users, container.vpn, apply=apply)
    except Exception as exc:
        print(f'Не вышло: {exc}')
        return 1

    if report['panel'] in ANSWERS:
        print(ANSWERS[report['panel']])
        return 0

    why, target = DIRECTIONS.get(report['direction'],
                                 ('панель сменила версию', 'нужный вид'))
    if not apply:
        print(f'{why.capitalize()}, а подписок с другим видом ссылки: '
              f'{report["stale"]}.')
        print('Пока они не переведены, панель отказывает по каждому запросу '
              'о них: продление, устройства, блокировка.')
        print('Перевести: python -m scripts.panel_ids fix')
        return 0

    print(f'Переведено на {target}: {report["moved"]} из {report["stale"]}.')
    if report['failed']:
        print(f'Не вышло: {report["failed"]} — этих подписок панель по '
              f'короткому идентификатору не нашла. Скорее всего их там уже нет.')
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
