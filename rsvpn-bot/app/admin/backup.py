"""Копии базы из админки: сделать сейчас, посмотреть список, забрать файл.

    /backup      — снять копию прямо сейчас и прислать её сюда
    /backups     — что уже лежит на сервере и когда делалось

Расписание и хранение — в настройках, раздел «Резервные копии базы».
Восстановление живёт отдельным скриптом (`scripts/dbrestore.py`) и
нарочно не кнопкой: восстановление базы нажатием с телефона — это способ
однажды потерять вечер.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Router, types
from aiogram.filters import Command

from app.content.emoji import e
from app.core.time import fmt, now
from app.services.backup import human_size

log = logging.getLogger(__name__)

RESTORE_HINT = (
    '<blockquote>Вернуть базу из копии (на сервере бота):\n'
    '<code>python -m scripts.dbrestore файл</code> — посмотреть, что внутри\n'
    '<code>python -m scripts.dbrestore файл --yes --drop</code> — залить\n\n'
    'Перед заливкой в боевую базу остановите бота: '
    '<code>pm2 stop rsvpn-bot rsvpn-api</code>.</blockquote>'
)


def file_line(item: Path) -> str:
    from datetime import datetime

    made = datetime.fromtimestamp(item.stat().st_mtime)
    return (f'• <code>{item.name}</code>\n'
            f'  {fmt(made)}, {human_size(item.stat().st_size)}')


async def say(note, message: types.Message, text: str) -> None:
    """Дописать в сообщение «снимаю…», а если не вышло — прислать новым.

    Снимок идёт минуту-другую, и ответ на него терять нельзя: правка
    сообщения может не пройти (удалили, слишком старое), и тогда админ
    остаётся с крутящимся «снимаю…» и без единого слова о результате.
    """
    try:
        await note.edit_text(text)
    except Exception:      # noqa: BLE001 — результат важнее оформления
        await message.answer(text)


async def backup_now(message: types.Message, c, settings) -> None:
    note = await message.answer(f'{e("refresh")} Снимаю копию базы…')
    report = await c.backup.run()

    if not report.ok:
        await say(note, message,
                  f'{e("cross")} <b>Не получилось</b>\n'
                  f'<code>{report.error}</code>\n\n'
                  f'Чаще всего это место на диске: <code>df -h</code>.')
        return

    lines = [f'{e("ok")} <b>Копия базы готова</b>',
             f'<code>{Path(report.path).name}</code>',
             f'<b>Размер:</b> {human_size(report.size)}',
             f'<b>Документов:</b> <code>{report.docs}</code> '
             f'(перечитано {report.checked})',
             f'<b>Заняло:</b> {report.seconds:.0f} с', '',
             f'<b>Каталог:</b> <code>{Path(report.path).parent}</code>']
    if report.removed:
        lines.append(f'<i>Старых удалено: {report.removed}</i>')
    lines.append('')
    lines.append(RESTORE_HINT)
    await say(note, message, '\n'.join(lines))

    # Файл — тому, кто попросил, а не в общий чат: в нём вся база.
    limit = int(await settings.int('backup.max_mb') or 45)
    if report.size > limit * 1024 * 1024:
        await message.answer(
            f'{e("warning")} Файл больше {limit} МБ — Telegram его не примет. '
            f'Забрать с сервера:\n'
            f'<code>scp root@сервер:{report.path} .</code>')
        return

    try:
        await message.answer_document(
            types.FSInputFile(report.path),
            caption=f'{e("document")} Копия базы от {fmt(now())}')
    except Exception as exc:      # noqa: BLE001 — копия уже на диске
        log.warning('копия базы не отправилась: %s', exc)
        await message.answer(f'{e("warning")} Файл не отправился '
                             f'(<code>{exc}</code>), но он на сервере: '
                             f'<code>{report.path}</code>')


async def backups_list(message: types.Message, c, settings) -> None:
    files = await c.backup.files()
    folder = await c.backup.directory()
    hour = await c.backup.hour()
    keep = await c.backup.keep()
    age = await c.backup.age_hours()

    lines = [f'<b>{e("document")} Копии базы</b>', '',
             f'<b>Каталог:</b> <code>{folder}</code>',
             f'<b>Расписание:</b> каждый день в {hour:02d}:00, '
             f'храним {keep} шт.']

    if age is None:
        lines.append(f'{e("attention")} <b>Копий нет вообще.</b> '
                     f'Сделать сейчас — <code>/backup</code>.')
    else:
        lines.append(f'<b>Последняя:</b> {age:.1f} ч назад'
                     + (f' {e("attention")} — это дольше суток'
                        if age > 26 else ''))
    lines.append('')

    for item in files[:15]:
        lines.append(file_line(item))
    if len(files) > 15:
        lines.append(f'<i>…и ещё {len(files) - 15}</i>')

    lines.append('')
    lines.append(RESTORE_HINT)
    await message.answer('\n'.join(lines))


def register(router: Router) -> None:
    router.message.register(backup_now, Command('backup'))
    router.message.register(backups_list, Command('backups'))
