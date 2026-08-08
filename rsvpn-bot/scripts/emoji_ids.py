"""Свои id кастомных эмодзи — из кода в отдельный файл.

Зачем: app/content/emoji.py — исходник, и обновление бота его перезапишет
вместе с проставленными id. emoji_ids.json лежит рядом с .env и вне git,
поэтому обновления его не трогают.

Порядок ровно один раз, ПЕРЕД обновлением:

    python -m scripts.emoji_ids --export     # id из кода → emoji_ids.json
    <обновиться>
    python -m scripts.emoji_ids              # проверить, что всё подхватилось

Без аргументов ничего не меняет: показывает, что бот видит сейчас и откуда.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.content.emoji import EMOJI, IDS_FILE, OVERRIDDEN, missing_ids  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def filled() -> dict[str, str]:
    return {name: emoji_id for name, (_, emoji_id) in EMOJI.items() if emoji_id}


def export(path: Path, force: bool) -> int:
    values = filled()
    if not values:
        print('Заполненных id нет — выгружать нечего.')
        return 1

    if path.exists() and not force:
        existing = json.loads(path.read_text(encoding='utf-8'))
        added = {k: v for k, v in values.items() if k not in existing}
        if not added:
            print(f'{path.name}: уже всё на месте, {len(existing)} шт.')
            return 0
        existing.update(added)
        values = existing
        print(f'{path.name}: добавлено {len(added)}')

    path.write_text(json.dumps(values, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')
    print(f'{path}: {len(values)} id')
    print('Файл вне git — обновления бота его не тронут.')
    return 0


def show(path: Path) -> int:
    values = filled()
    source = f'{path.name} + код' if path.exists() else 'только код'

    print(f'Источник: {source}')
    print(f'Заполнено: {len(values)} из {len(EMOJI)}')
    for name, emoji_id in sorted(values.items()):
        print(f'  {name:<14} {EMOJI[name][0]}  {emoji_id}')

    empty = missing_ids()
    if empty:
        print(f'\nБез id ({len(empty)}) — показываются обычными значками:')
        print('  ' + ', '.join(empty))

    if OVERRIDDEN:
        print(f'\n{path.name} перебивает то, что задано в коде ({len(OVERRIDDEN)}):')
        for name, (in_code, in_file) in sorted(OVERRIDDEN.items()):
            print(f'  {name:<14} код {in_code}  →  файл {in_file}')
        print('\nФайл всегда сильнее кода. Если новые значки приехали с '
              'обновлением\nи файл больше не нужен — удалите его: '
              f'rm {path}')

    if not path.exists():
        print(f'\n{path.name} нет. Выгрузить id туда: '
              f'python -m scripts.emoji_ids --export')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', action='store_true',
                        help=f'записать заполненные id в {IDS_FILE}')
    parser.add_argument('--force', action='store_true',
                        help='перезаписать файл целиком, а не дополнить')
    args = parser.parse_args()

    path = ROOT / IDS_FILE
    return export(path, args.force) if args.export else show(path)


if __name__ == '__main__':
    raise SystemExit(main())
