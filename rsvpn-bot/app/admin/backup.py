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
from aiogram.filters import Command, CommandObject

from app.content import ids, progress
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


async def say(note, message: types.Message, text: str,
              quiet: bool = False) -> None:
    """Дописать в сообщение «снимаю…», а если не вышло — прислать новым.

    Снимок идёт минуту-другую, и ответ на него терять нельзя: правка
    сообщения может не пройти (удалили, слишком старое), и тогда админ
    остаётся с крутящимся «снимаю…» и без единого слова о результате.
    """
    try:
        await note.edit_text(text)
    except Exception:      # noqa: BLE001 — результат важнее оформления
        # Промежуточный шаг не дублируем новым сообщением: не дошла
        # полоска — не беда, а десяток сообщений подряд — беда.
        if not quiet:
            await message.answer(text)


TITLE = 'Копия базы'


async def backup_now(message: types.Message, command: CommandObject, c,
                     settings) -> None:
    force = (command.args or '').strip().lower() in ('force', '!', 'force!',
                                                     'всё равно', 'давай')
    note = await message.answer(progress.screen(
        f'{e("document")} {TITLE}', 'Считаю, сколько всего документов…'))
    # Ссылку на сообщение кладём в состояние: если процесс убьют посреди
    # копии, дописать в эту полоску сможет только следующий запуск бота.
    await c.backup.note_progress(0, 0, 'Начинаю', cards=[
        {'chat_id': note.chat.id, 'message_id': note.message_id}])
    ticker = progress.Ticker()

    async def show(step: str, done: int, total: int) -> None:
        # Правим не чаще раза в пару секунд и только когда сдвинулся
        # процент: Telegram отвечает ошибкой и на слишком частые правки,
        # и на правку тем же самым текстом.
        if not ticker.should(progress.percent(done, total)):
            return
        await c.backup.note_progress(done, total, step)
        await say(note, message, progress.screen(
            f'{e("document")} {TITLE}', step, done, total), quiet=True)

    report = await c.backup.make(on_progress=show, force=force)

    if report.busy:
        running = await c.backup.in_progress()
        idle = c.backup.idle_minutes(running) if running else 0
        await say(note, message,
                  f'{e("clock")} Копия уже делается'
                  + (f' (<code>{running.name}</code>, последняя запись '
                     f'{idle:.0f} мин назад)' if running else '')
                  + f'.\n\nЕсли уверены, что она зависла: '
                    f'<code>/backup force</code> — начнёт новую, не глядя '
                    f'на эту.')
        return

    if not report.ok:
        await say(note, message,
                  f'{e("cross")} <b>Не получилось</b>\n'
                  f'<code>{report.error}</code>\n\n'
                  f'Чаще всего это место на диске: <code>df -h</code>.')
        return

    await c.backup.drop_cards()

    lines = [f'{e("ok")} <b>Копия базы готова</b>',
             f'<code>{Path(report.path).name}</code>',
             f'<b>Размер:</b> {human_size(report.size)}',
             f'<b>Документов:</b> <code>{report.docs}</code> '
             f'(перечитано {report.checked})',
             f'<b>Заняло:</b> {report.seconds:.0f} с', '',
             f'<b>Каталог:</b> <code>{Path(report.path).parent}</code>']
    if report.removed:
        lines.append(f'<i>Старых удалено: {report.removed}</i>')
    if report.stored:
        lines.append(f'{e("ok")} В хранилище: <code>{report.stored}</code>')
    elif report.store_error:
        lines.append(f'{e("warning")} В хранилище не уехала: '
                     f'<code>{report.store_error}</code>')
    lines.append('')
    lines.append(RESTORE_HINT)
    await say(note, message, '\n'.join(lines))

    # Файл — тому, кто попросил, а не в общий чат: в нём вся база. Большой
    # уходит частями тем же путём, что и ночная копия: «слишком большой»
    # означало бы, что копии в телефоне нет именно тогда, когда она нужна.
    limit = int(await settings.int('backup.max_mb') or 45)
    if report.stored:
        await message.answer(
            f'{e("ok")} Копия уехала в хранилище: '
            f'<code>{report.stored}</code>\n\n'
            f'<i>Файл сюда не присылаю — он уже снаружи. Нужен в руки: '
            f'<code>scp root@сервер:{report.path} .</code></i>')
        return

    sent = 0
    if c.notifier is not None:
        sent = await c.notifier.backup_file(
            report.path, limit_mb=limit,
            chat_ids=[message.chat.id], backup=c.backup)
    elif report.size <= limit * 1024 * 1024:
        # Без Notifier (так бывает только в тестовой сборке) отправляем
        # сами — но резать на части уже некому.
        try:
            await message.answer_document(
                types.FSInputFile(report.path),
                caption=f'{e("document")} Копия базы от {fmt(now())}')
            sent = 1
        except Exception as exc:      # noqa: BLE001 — копия уже на диске
            log.warning('копия базы не отправилась: %s', exc)

    if not sent:
        await message.answer(
            f'{e("warning")} Файл не отправился. Он на сервере:\n'
            f'<code>{report.path}</code>\n\n'
            f'Забрать: <code>scp root@сервер:{report.path} .</code>')


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

    running = await c.backup.in_progress()
    if running is not None:
        # Не «файл есть», а «в него пишут»: без времени последней записи
        # брошенный обрывок неотличим от работы.
        lines.append(f'{e("refresh")} <b>Копия делается прямо сейчас</b>\n'
                     f'   <code>{running.name}</code>, последняя запись '
                     f'{c.backup.idle_minutes(running):.0f} мин назад')
    breaks = await c.backup.breaks()
    if breaks:
        lines.append(f'{e("warning")} Прервано попыток подряд: '
                     f'<b>{breaks}</b> — процесс бота останавливается '
                     f'посреди копии')
    broken = await c.backup.broken_leftovers()
    if broken:
        lines.append(f'{e("warning")} Оборванных попыток: '
                     f'<b>{len(broken)}</b> — в них давно никто не пишет, '
                     f'их уберёт следующий снимок')

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


async def storage_check(message: types.Message, c, settings) -> None:
    """Проверить хранилище прямо сейчас, а не через сутки на ночном снимке."""
    storage = getattr(c.backup, 'storage', None)
    if storage is None or not storage.ready:
        await message.answer(
            f'{e("cross")} <b>Хранилище не настроено</b>\n\n'
            f'В <code>.env</code> бота нужны четыре строки:\n'
            f'<code>BACKUP_S3_ENDPOINT=…</code>\n'
            f'<code>BACKUP_S3_BUCKET=…</code>\n'
            f'<code>BACKUP_S3_KEY=…</code>\n'
            f'<code>BACKUP_S3_SECRET=…</code>\n\n'
            f'<blockquote>Для Cloudflare R2 адрес — '
            f'<code>https://&lt;account_id&gt;.r2.cloudflarestorage.com</code>, '
            f'ключи берутся в R2 → Manage API tokens.</blockquote>')
        return

    note = await message.answer(f'{e("refresh")} Проверяю хранилище…')
    import asyncio

    try:
        key = await asyncio.to_thread(storage.check)
    except Exception as exc:      # noqa: BLE001 — показываем причину как есть
        await say(note, message,
                  f'{e("cross")} <b>Хранилище не отвечает</b>\n'
                  f'<code>{str(exc)[:500]}</code>\n\n'
                  f'<blockquote>Частые причины: ключ без права записи '
                  f'(нужен Object Read &amp; Write), опечатка в имени '
                  f'бакета, адрес не того аккаунта.</blockquote>')
        return

    on = await settings.flag('backup.to_storage')
    await say(note, message,
              f'{e("ok")} <b>Хранилище работает</b>\n'
              f'Записал и удалил пробный файл: <code>{key}</code>\n\n'
              + storage_card(storage.config) + '\n'
              + (f'Копии будут уезжать туда автоматически.'
                 if on else
                 f'{e("warning")} Но в настройках выключено «Увозить копию '
                 f'в хранилище» — включите.'))


def storage_card(config) -> str:
    """Куда именно уезжают копии — чтобы те же данные вписать в другое
    место (например, в скрипт бэкапа панели) и не искать их по серверу.

    Секрет не показывается никогда: экран админки фотографируют, пересылают
    и оставляют открытым, а по этой паре ключей чужой человек получает всё
    содержимое бакета — то есть всю базу целиком. Он лежит в .env, и
    забрать его оттуда можно только с доступом к серверу.
    """
    return (f'<b>Адрес:</b> <code>{config.endpoint}</code>\n'
            f'<b>Бакет:</b> <code>{config.bucket}</code>\n'
            f'<b>Папка:</b> <code>{config.prefix}</code>\n'
            f'<b>Регион:</b> <code>{config.region}</code>\n'
            f'<b>Ключ:</b> <code>{ids.mask(config.key, 4, 4)}</code>\n'
            f'<b>Секрет:</b> не показываю\n\n'
            f'<blockquote>Ключ и секрет целиком — в <code>.env</code> бота:\n'
            f'<code>grep BACKUP_S3 /home/rsvpn-bot/.env</code></blockquote>\n')


def register(router: Router) -> None:
    router.message.register(backup_now, Command('backup'))
    router.message.register(backups_list, Command('backups'))
    router.message.register(storage_check, Command('backupcheck'))
