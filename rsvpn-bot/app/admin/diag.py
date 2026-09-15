"""/diag — что из фонового работает прямо сейчас.

Вопрос «идут ли напоминания и автопродление» неудобен тем, что ответы на
него живут в разных местах: продление — в планировщике бота, напоминания —
в вебхуках панели, которые принимает вообще другой процесс. По логам это
видно, но за логами надо идти на сервер.

Экран собирает всё в одно место и, главное, честно показывает пустоту:
«вебхуков от панели не было» — самый частый и самый полезный ответ, потому
что означает конкретное: панель не настроена или не достучалась.

Здесь только чтение и никаких действий: команду можно жать смело.
"""

from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.admin import health
from app.bot.callbacks import Admin as Adm
from app.content.emoji import e
from app.core.time import fmt, now
from app.services.expiry import ALL_KEYS
from app.version import version_line


def ago(moment) -> str:
    """«12 минут назад» — по абсолютному времени непонятно, свежее это или нет."""
    if not moment:
        return 'не было'
    seconds = (now() - moment).total_seconds()
    if seconds < 90:
        return 'только что'
    if seconds < 3600:
        return f'{int(seconds // 60)} мин назад'
    if seconds < 86400:
        return f'{int(seconds // 3600)} ч назад'
    return fmt(moment)


def numbers(info: dict) -> str:
    return ', '.join(f'{key}={value}' for key, value in (info or {}).items()
                     if value not in (None, ''))


async def text(c, settings) -> str:
    marks = await c.health.read()
    scheduler = c.config.scheduler_enabled

    lines = [f'<b>{e("tools")} Диагностика</b>', f'Сборка: <code>{version_line()}</code>', '']

    # ── планировщик: автопродление и списания ───────────────────────────────
    lines.append(f'<b>{e("renew")} Автопродление</b>')
    if not scheduler:
        lines.append(f'{e("cross")} Планировщик выключен в этом процессе '
                     '(SCHEDULER_ENABLED=0) — списаний не будет')
    elif not await settings.flag('features.autorenew_enabled'):
        lines.append(f'{e("cross")} Выключено в настройках '
                     '(Функции → Автопродление)')
    else:
        lines.append(f'{e("ok")} Включено, проверка каждые 15 минут')

    renewal = marks.get(health.RENEWAL) or {}
    lines.append(f'Последний проход: <b>{ago(renewal.get("at"))}</b>')
    if renewal.get('info'):
        lines.append(f'<code>{numbers(renewal["info"])}</code>')

    devices = marks.get(health.DEVICES) or {}
    if devices.get('at'):
        lines.append(f'Плата за устройства: {ago(devices.get("at"))} '
                     f'<code>{numbers(devices.get("info"))}</code>')

    # ── напоминания: приходят вебхуками панели ──────────────────────────────
    lines.append(f'\n<b>{e("clock")} Напоминания об истечении</b>')
    if not await settings.flag('expiry.notify_enabled'):
        lines.append(f'{e("cross")} Выключены в настройках')
    else:
        on = [key for key in ALL_KEYS if await settings.flag(f'expiry.send_{key}')]
        lines.append(f'{e("ok")} Включены: <code>{", ".join(on) or "ни одного порога"}</code>')

    webhook = marks.get(health.PANEL_WEBHOOK) or {}
    lines.append(f'Событий от панели: <b>{ago(webhook.get("at"))}</b>'
                 + (f' (всего {webhook.get("runs", 0)})' if webhook.get('at') else ''))
    if webhook.get('info'):
        lines.append(f'<code>{numbers(webhook["info"])}</code>')

    sent = marks.get(health.EXPIRY_SENT) or {}
    lines.append(f'Последнее отправленное: <b>{ago(sent.get("at"))}</b>'
                 + (f' <code>{numbers(sent.get("info"))}</code>' if sent.get('at') else ''))

    if not webhook.get('at'):
        lines.append(
            f'\n<blockquote>{e("attention")} Панель ни разу не позвала бота. '
            'Напоминания приходят вебхуками, сам бот их не рассылает. '
            'Проверьте в <code>/opt/remnawave/.env</code>: '
            '<code>WEBHOOK_ENABLED=true</code>, '
            '<code>WEBHOOK_URL</code> на <code>/remnawave/webhook</code>, '
            '<code>EXPIRATION_NOTIFICATIONS_ENABLED=true</code> и совпадение '
            '<code>WEBHOOK_SECRET_HEADER</code> с REMNAWAVE_WEBHOOK_SECRET. '
            'И что процесс API запущен.</blockquote>')
    elif webhook.get('info', {}).get('note') == 'bad_signature':
        lines.append(f'\n<blockquote>{e("attention")} Панель зовёт, но подпись не '
                     'сходится: секрет в панели и в .env бота разные.</blockquote>')

    # ── кампании ────────────────────────────────────────────────────────────
    campaigns = marks.get(health.CAMPAIGNS) or {}
    lines.append(f'\n<b>{e("megaphone")} Кампании</b>: {ago(campaigns.get("at"))}'
                 + (f' <code>{numbers(campaigns.get("info"))}</code>'
                    if campaigns.get('at') else ''))

    # ── сверка ByPass ───────────────────────────────────────────────────────
    #
    # Задача тихая: чинит даты и молчит. Без строки здесь «не отработала ни
    # разу» и «отработала, расхождений нет» выглядят одинаково, а разница
    # между ними — отключённый посреди месяца ByPass.
    sync = marks.get(health.BYPASS_SYNC) or {}
    lines.append(f'\n<b>{e("renew")} Сверка дат ByPass</b>: {ago(sync.get("at"))}'
                 + (f' <code>{numbers(sync.get("info"))}</code>'
                    if sync.get('at') else ''))
    if not sync.get('at') and scheduler:
        lines.append('<blockquote>Задача идёт раз в шесть часов. Проверить '
                     'вручную: <code>/bypasssync</code>.</blockquote>')

    # ── ошибки, которые видели люди ─────────────────────────────────────────
    #
    # «Сервис подписок не отвечает» человек видит, а мы — нет: раньше это
    # уходило строкой INFO в лог на сервере, без имени и без экрана.
    journal = getattr(c, 'errors', None)
    if journal is not None:
        day = now() - timedelta(days=1)
        total = await journal.count_since(day)
        panel = await journal.count_since(day, kind='panel')
        lines.append(f'\n<b>{e("warning")} Ошибки за сутки</b>: {total}'
                     + (f', из них панель: {panel}' if panel else ''))
        if total:
            lines.append('<blockquote>Кто и что видел — <code>/errors</code>, '
                         'по одному человеку — <code>/errors 802421217</code>.'
                         '</blockquote>')

    # ── что сейчас раздаётся бесплатно ──────────────────────────────────────
    # Скидка и бонус живут в настройках и не напоминают о себе: включили на
    # выходные, забыли выключить — и каждое продление уходит дешевле. Вопрос
    # «почему упала выручка» начинается отсюда, поэтому цифры видны в /diag,
    # а не только в разделе, куда надо специально зайти.
    lines.append(f'\n<b>{e("discount")} Скидки и бонусы сейчас</b>')
    active = []
    for key, title in await _discount_rows(settings):
        rate = await settings.rate(key)
        if rate > 0:
            active.append(f'{title}: <b>−{round(rate * 100)}%</b>')
    lines.append('; '.join(active) if active
                 else f'{e("ok")} Скидок по аудиториям нет')

    topup_on = await settings.flag('bonus.topup_enabled')
    topup_rate = round(await settings.rate('bonus.topup_rate') * 100) if topup_on else 0
    ab_rate = round(await settings.rate('bonus.ab_new_trial_rate') * 100)
    lines.append(f'Бонус к пополнению: <b>+{topup_rate}%</b>, '
                 f'новичкам сверх этого: <b>+{ab_rate}%</b>, '
                 f'рефералам: <b>{round(await settings.rate("bonus.ref_rate") * 100)}%</b>')
    return '\n'.join(lines)


async def _discount_rows(settings) -> list[tuple[str, str]]:
    """Ключи скидок с человеческими названиями — из той же схемы, что и админка."""
    from app.settings.schema import SCHEMA

    return [(s.key, s.title) for group in SCHEMA for s in group.items
            if s.key.startswith('discount.') and s.type == 'percent']


def _kb() -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text=f'{e("refresh")} Обновить', callback_data=Adm(act='diag').pack()))
    kb.row(types.InlineKeyboardButton(
        text=f'{e("bell")} Проверить напоминание',
        callback_data=Adm(act='exptest').pack()))
    return kb.as_markup()


async def command(message: types.Message, c, settings) -> None:
    await message.answer(await text(c, settings), reply_markup=_kb())


# Что означают виды ошибок в журнале — на русском и без сокращений.
ERROR_KINDS = {
    'panel': 'панель',
    'app': 'ожидаемая',
    'crash': 'сбой',
}


async def errors(message: types.Message, command, c, settings) -> None:
    """`/errors [id пользователя]` — что видели люди вместо экрана.

    «Сервис подписок не отвечает» — текст для человека; здесь настоящий
    ответ панели, экран, на котором это случилось, и кто именно попал.
    """
    journal = getattr(c, 'errors', None)
    if journal is None:
        await message.answer(f'{e("cross")} Журнал ошибок не подключён.')
        return

    argument = (command.args or '').strip().lstrip('#')
    user_id = int(argument) if argument.isdigit() else 0

    rows = await journal.recent(limit=15, user_id=user_id)
    if not rows:
        await message.answer(
            f'{e("ok")} Ошибок нет'
            + (f' у <code>{user_id}</code>' if user_id else ' за последнее время')
            + '.')
        return

    lines = [f'{e("warning")} <b>Последние ошибки</b>'
             + (f' у <code>{user_id}</code>' if user_id else ''), '']
    for row in rows:
        who = (f'@{row["username"]}' if row.get('username')
               else f'<code>{row.get("user_id")}</code>')
        lines.append(
            f'{fmt(row.get("at"))} · {who} · '
            f'{ERROR_KINDS.get(row.get("kind"), row.get("kind"))}')
        if row.get('where'):
            lines.append(f'   экран: <code>{row["where"]}</code>')
        lines.append(f'   <code>{str(row.get("message"))[:300]}</code>')
        lines.append('')

    lines.append('<blockquote>Ошибки панели («HTTP 4xx/5xx») — это ответ '
                 'Remnawave: код и текст видно целиком. Проверить конкретного '
                 'человека: <code>/errors 802421217</code>.</blockquote>')
    await message.answer('\n'.join(lines))


async def bypass_sync(message: types.Message, command, c, settings) -> None:
    """`/bypasssync [fix]` — у кого ByPass разошёлся с подпиской.

    Сверяются срок и лимит устройств: и то и другое у ByPass своё, и любое
    расхождение человек видит как «купил, а не работает».

    Без аргумента только показывает: правка ходит в панель по запросу на
    человека, и запускать её вслепую, не увидев масштаба, незачем.
    """
    from app.services import bypass

    apply = 'fix' in (command.args or '').split()
    if apply and c.vpn is None:
        # Без панели правка молча запишется в «не вышло» на каждом человеке.
        await message.answer(f'{e("cross")} Клиент панели не собран — править '
                             f'нечем. Проверьте настройки подключения к Remnawave.')
        return

    report = await bypass.repair(c.users, c.vpn, apply=apply)

    if not report['checked']:
        await message.answer(f'{e("ok")} Даты ByPass и подписок совпадают у всех.')
        return

    def what(row: dict) -> str:
        parts = []
        if row.get('dates'):
            parts.append(f'срок: подписка {fmt(row["main"])}, ByPass '
                         + (fmt(row['bypass']) if row['bypass'] else 'неизвестен'))
        if row.get('device_limit'):
            parts.append(f'устройства: {row.get("devices")} против '
                         + (str(row['bypass_devices'])
                            if row.get('bypass_devices') else 'неизвестно'))
        return '; '.join(parts)

    rows = '\n'.join(f'<code>{row["user_id"]}</code>: {what(row)}'
                     for row in report['rows'])

    await message.answer(
        f'{e("warning")} <b>Расхождений: {report["checked"]}</b>\n\n{rows}'
        + (f'\n\n<b>Выровнено:</b> {report["fixed"]}'
           + (f', не вышло: {report["failed"]}' if report['failed'] else '')
           if apply else
           '\n\n<blockquote>Это только показ. Чтобы выровнять даты по основной '
           'подписке, пришлите <code>/bypasssync fix</code>. Фоновая задача '
           'делает то же самое раз в шесть часов.</blockquote>'))


PANEL_ANSWERS = {
    'nothing': f'{e("ok")} Все подписки опознаются панелью — переводить нечего.',
    'unknown': f'{e("cross")} Панель не ответила — версию выяснить не вышло. '
               f'Проверьте подключение: <code>/squadcheck</code> и '
               f'<code>/errors</code>.',
}

# Куда переводим. Направление зависит от панели, а не от нашего намерения:
# после отката с 3.x на 2.x переводить надо ровно наоборот.
DIRECTIONS = {
    'to_id': ('панель уже 3.x', 'числовые id'),
    'to_uuid': ('панель откатили на 2.x', 'прежние uuid'),
}


async def panel_ids(message: types.Message, command, c, settings) -> None:
    """`/panelids [fix]` — перевести подписки на числовые id панели 3.x.

    С Remnawave 3.0 пользователь опознаётся числовым id, а uuid из ответов
    убран. У всех, кто купил подписку раньше, в базе лежит uuid, и панель
    3.x его не понимает: ни продлить, ни отключить, ни показать устройства.
    Обратного поиска по uuid в 3.x нет, поэтому каждого приходится
    спрашивать заново по shortUuid — он от версии панели не зависит.
    """
    from app.services import panel_ids as migration

    if c.vpn is None:
        await message.answer(f'{e("cross")} Клиент панели не собран — '
                             f'спрашивать не у кого.')
        return

    apply = 'fix' in (command.args or '').split()
    report = await migration.migrate(c.users, c.vpn, apply=apply)

    if report['panel'] in PANEL_ANSWERS:
        await message.answer(PANEL_ANSWERS[report['panel']])
        return

    why, target = DIRECTIONS.get(report['direction'], ('панель сменила версию',
                                                       'нужный вид'))
    if apply:
        await message.answer(
            f'{e("ok")} <b>Переведено на {target}: {report["moved"]}</b> '
            f'из {report["stale"]}'
            + (f'\nНе вышло: <code>{report["failed"]}</code> — эти подписки '
               f'панель по короткому идентификатору не нашла. Скорее всего их '
               f'там уже нет: посмотрите <code>/errors</code>.'
               if report['failed'] else ''))
        return

    rows = '\n'.join(f'<code>{row["user_id"]}</code>: '
                      + ', '.join(field for field, _, _ in row['pending'])
                      for row in report['rows'])
    await message.answer(
        f'{e("warning")} <b>{why.capitalize()}, а подписок с другим видом '
        f'ссылки: {report["stale"]}</b>\n\n{rows}\n\n'
        f'<blockquote>Пока они не переведены на {target}, панель отказывает '
        f'по каждому запросу о них: продление, устройства, блокировка. '
        f'Пришлите <code>/panelids fix</code>. Фоновая задача делает то же '
        f'самое раз в час — команда нужна, когда ждать некогда.</blockquote>')


MAINTENANCE_ON = 'features.maintenance_mode'


async def maintenance_text(settings, c) -> str:
    on = await settings.flag(MAINTENANCE_ON)
    game = await settings.flag('features.maintenance_game')
    body = str(await settings.get('text.maintenance') or '').strip()

    head = (f'{e("wrench")} <b>Техработы включены</b>\n\n'
            f'Люди сейчас видят один экран и больше ничего сделать не могут. '
            f'Подписки это не трогает — VPN у них работает.'
            if on else
            f'{e("ok")} <b>Бот работает обычно</b>\n\n'
            f'Включите режим перед выкладкой или починкой: на любое действие '
            f'человек увидит экран о работах вместо ошибки.')

    return (f'{head}\n\n'
            f'<b>Что увидят люди:</b>\n<blockquote>{body}</blockquote>\n'
            f'Сапёр на экране ожидания: '
            f'<b>{"включён" if game else "выключен"}</b>\n\n'
            f'<blockquote>Вас режим не касается: админка и все команды '
            f'работают как всегда, можно спокойно проверять. Текст и сапёр '
            f'меняются в /admin → Технические работы.</blockquote>')


def maintenance_kb(on: bool) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text=(f'{e("ok")} Выключить техработы' if on
              else f'{e("wrench")} Включить техработы'),
        callback_data=Adm(act='maint', a='off' if on else 'on').pack()))
    kb.row(types.InlineKeyboardButton(
        text=f'{e("back")} Назад', callback_data=Adm(act='main').pack()))
    return kb


async def maintenance_screen(call: types.CallbackQuery, callback_data: Adm,
                             c, settings) -> None:
    from app.admin.panel import edit

    if callback_data.a in ('on', 'off'):
        await settings.set(MAINTENANCE_ON, callback_data.a == 'on')
        await call.answer('Техработы включены' if callback_data.a == 'on'
                          else 'Бот снова работает')

    on = await settings.flag(MAINTENANCE_ON)
    await edit(call, await maintenance_text(settings, c), maintenance_kb(on))


async def maintenance_command(message: types.Message, command, c, settings) -> None:
    """`/maintenance [on|off]` — режим техработ.

    Без аргумента показывает состояние и кнопку: включать такое вслепую,
    одной командой, слишком легко — а выключить забыть ещё легче.
    """
    arg = (command.args or '').strip().lower()
    if arg in ('on', 'вкл', '1'):
        await settings.set(MAINTENANCE_ON, True)
    elif arg in ('off', 'выкл', '0'):
        await settings.set(MAINTENANCE_ON, False)

    on = await settings.flag(MAINTENANCE_ON)
    await message.answer(await maintenance_text(settings, c),
                         reply_markup=maintenance_kb(on).as_markup())


DEVSYNC_USAGE = (
    f'{e("devices")} <b>Сверка лимита устройств</b>\n\n'
    f'<code>/devsync</code> — где база бота разошлась с оплаченным\n'
    f'<code>/devsync fix</code> — выровнять найденное\n'
    f'<code>/devsync panel</code> — спросить у панели настоящий лимит\n'
    f'<code>/devsync panel fix</code> — и выровнять\n'
    f'<code>/devsync 802421217</code> — все числа по одному человеку\n\n'
    f'<blockquote>Чисел три: сколько оплачено пакетами, что записано у бота '
    f'и что стоит в панели. Первое — правда, оно выводится из денег; '
    f'остальные к нему и приводятся.\n\n'
    f'Сверка с панелью стоит запроса на человека, поэтому идёт отдельно и '
    f'по умолчанию на 300 человек: <code>/devsync panel 2000</code> — '
    f'больше.</blockquote>'
)


def _packages(count: int) -> str:
    """«в 1 пакетах» читается как опечатка, и это она и есть."""
    tail = count % 100
    if 11 <= tail <= 14:
        return f'{count} пакетах'
    return f'{count} ' + {1: 'пакете', 2: 'пакетах', 3: 'пакетах',
                          4: 'пакетах'}.get(count % 10, 'пакетах')


def _devrow(row: dict) -> str:
    panel = row.get('panel')
    return (f'<code>{row["user_id"]}</code>: оплачено <b>{row["paid"]}</b>, '
            f'у бота <b>{row["stored"]}</b>'
            + (f', в панели <b>{panel}</b>' if panel is not None else ''))


async def _devsync_one(message: types.Message, c, settings, user_id: int) -> None:
    from app.services import device_sync

    base = await settings.int('price.devices_free_limit')
    card = await device_sync.one(c.users, c.vpn, base, user_id)
    if not card:
        await message.answer(f'{e("cross")} Пользователь <code>{user_id}</code> '
                             f'не найден.')
        return

    active = [p for p in card['packages'] if p.get('active', True)]
    lines = [f'{e("devices")} <b>Устройства пользователя {user_id}</b>', '']
    lines.append(f'{e("money")} Оплачено: <b>{card["paid"]}</b> '
                 f'({card["base"]} бесплатных + {card["paid"] - card["base"]} '
                 f'в {_packages(len(active))})')
    lines.append(f'{e("support")} У бота записано: <b>{card["stored"]}</b>')
    lines.append(f'{e("servers")} В панели: '
                 + (f'<b>{card["panel"]}</b>' if card['panel'] is not None
                    else f'<i>{card["panel_error"] or "неизвестно"}</i>'))
    if card['bypass'] is not None:
        lines.append(f'{e("bypass")} У ByPass: <b>{card["bypass"]}</b>')

    agree = (card['panel'] is not None
             and card['paid'] == card['stored'] == int(card['panel']))
    lines.append('')
    lines.append(f'{e("ok")} Всё сходится.' if agree else
                 f'{e("warning")} Расхождение. Выровнять по оплаченному: '
                 f'<code>/devsync fix</code> пройдёт по всей базе, либо '
                 f'докупка/возврат устройства у человека выставит лимит сам.')
    await message.answer('\n'.join(lines))


async def devsync(message: types.Message, command, c, settings) -> None:
    """`/devsync [fix|panel|id]` — сверка лимита устройств."""
    from app.services import device_sync

    args = (command.args or '').split()
    if args and args[0].isdigit():
        await _devsync_one(message, c, settings, int(args[0]))
        return

    apply = 'fix' in args
    base = await settings.int('price.devices_free_limit')

    if apply and c.vpn is None:
        await message.answer(f'{e("cross")} Клиент панели не собран — '
                             f'выравнивать нечем.')
        return

    if 'panel' in args:
        if c.vpn is None:
            await message.answer(f'{e("cross")} Клиент панели не собран.')
            return
        cap = next((int(a) for a in args if a.isdigit()), 300)
        await message.answer(f'{e("hourglass")} Спрашиваю панель по '
                             f'{cap} подпискам — это займёт время.')
        found = await device_sync.check_panel(c.users, c.vpn, base, limit=cap)
        rows = found['drift']
        tail = (f'\nПроверено: {found["checked"]}'
                + (f', не ответила панель: {found["failed"]}'
                   if found['failed'] else '')
                + ('\n<i>Дошли до предела — повторите с большим числом.</i>'
                   if found['stopped'] else ''))
    else:
        rows = await device_sync.find_drift(c.users, base)
        tail = '\n<i>Это сверка без панели. Что стоит в ней самой — '\
               '<code>/devsync panel</code>.</i>'

    if not rows:
        await message.answer(f'{e("ok")} Расхождений нет.{tail}')
        return

    shown = '\n'.join(_devrow(row) for row in rows[:20])
    if not apply:
        await message.answer(
            f'{e("warning")} <b>Расхождений: {len(rows)}</b>\n\n{shown}'
            + (f'\n<i>…и ещё {len(rows) - 20}</i>' if len(rows) > 20 else '')
            + f'{tail}\n\n'
            f'<blockquote>Выровнять по оплаченному: добавьте <code>fix</code>. '
            f'Бот выставит число в панели, у себя и у ByPass — в этом '
            f'порядке.</blockquote>')
        return

    report = await device_sync.repair(c.users, c.vpn, rows, apply=True)
    await message.answer(
        f'{e("ok")} <b>Выровнено: {report["fixed"]}</b> из {report["total"]}'
        + (f'\nНе вышло: <code>{report["failed"]}</code> — панель отказала, '
           f'их документы не тронуты. Смотрите <code>/errors</code>.'
           if report['failed'] else ''))


async def refresh(call: types.CallbackQuery, c, settings) -> None:
    try:
        await call.message.edit_text(await text(c, settings), reply_markup=_kb())
    except Exception:      # «message is not modified» — ничего не изменилось
        pass
    await call.answer('Обновлено')


# ── проверка напоминаний ────────────────────────────────────────────────────
#
# «Придёт ли мне сообщение о продлении» — вопрос, на который до сих пор можно
# было ответить только ожиданием: напоминания рождаются в панели, а она
# присылает событие сама и по своему расписанию. Ждать сутки, чтобы узнать,
# что выключен один флажок, — плохой способ.
#
# Кнопка прогоняет тот же самый обработчик, что и настоящий вебхук, но
# событие подкладывает сама. Проверяется всё, кроме одного звена: настройки,
# поиск пользователя, текст, клавиатура, доставка. Дошла ли до бота панель —
# видно выше по строке «Событий от панели».
#
# Отметку об отправке снимаем: иначе проверка съела бы настоящее напоминание,
# и человек его уже не получил бы.

def _hours_for(key: str) -> int:
    from app.services.expiry import AFTER_EXPIRY, REMINDERS

    for threshold, code in REMINDERS:
        if code == key:
            return -threshold
    for threshold, code in AFTER_EXPIRY:
        if code == key:
            return threshold
    return -24


async def test_menu(call: types.CallbackQuery, c, settings) -> None:
    kb = InlineKeyboardBuilder()
    for key in ALL_KEYS:
        on = await settings.flag(f'expiry.send_{key}')
        kb.row(types.InlineKeyboardButton(
            text=f'{e("ok") if on else e("cross")} {key}',
            callback_data=Adm(act='exptestgo', a=key).pack()))
    kb.row(types.InlineKeyboardButton(
        text=f'{e("back")} Назад', callback_data=Adm(act='diag').pack()))

    enabled = await settings.flag('expiry.notify_enabled')
    await call.message.edit_text(
        f'<b>{e("bell")} Проверка напоминаний</b>\n\n'
        + (f'{e("ok")} Напоминания включены\n\n' if enabled else
           f'{e("cross")} Напоминания выключены целиком — ни одно не уйдёт, '
           'даже если порог включён\n\n')
        + 'Выберите порог — придёт ровно то сообщение, которое получит '
          'пользователь на этом этапе.\n\n'
          '<blockquote>Это прогон настоящего обработчика с подложенным '
          'событием. Он проверяет настройки, текст и доставку. Единственное, '
          'что так не проверить, — доходит ли до бота сама панель: это видно '
          'в /diag по строке «Событий от панели».</blockquote>',
        reply_markup=kb.as_markup())
    await call.answer()


async def test_send(call: types.CallbackQuery, callback_data: Adm, c, settings) -> None:
    key = callback_data.a
    if key not in ALL_KEYS or not c.expiry:
        await call.answer('Напоминания не собраны в этой сборке', show_alert=True)
        return

    user_id = call.from_user.id
    result = await c.expiry.handle(
        'user.expiration', {'telegramId': user_id}, {'expiration': _hours_for(key)})

    # Снимаем отметку — проверка не должна отнимать настоящее напоминание.
    await c.users.col.update_one(
        {'user_data.user_id': user_id},
        {'$unset': {f'vpn.notified.{key}': '', f'vpn.notified_at.{key}': ''}})

    note = str((result or {}).get('note') or '')
    if note.startswith('sent_'):
        answer = ('Отправлено' if (result or {}).get('delivered')
                  else 'Обработчик сработал, но Telegram не принял сообщение')
    elif note == 'disabled':
        answer = 'Напоминания выключены целиком: Настройки → Напоминания'
    elif note.startswith('skipped_'):
        answer = f'Порог {key} выключен в настройках — такое письмо не уходит'
    elif note == 'user_not_found':
        answer = 'Вас нет в базе бота — напишите боту /start'
    else:
        answer = f'Не отправлено: {note or "неизвестно"}'

    await call.answer(answer, show_alert=True)


def register(router: Router) -> None:
    router.message.register(command, Command('diag'))
    router.message.register(bypass_sync, Command('bypasssync'))
    router.message.register(errors, Command('errors'))
    router.message.register(panel_ids, Command('panelids'))
    router.message.register(maintenance_command, Command('maintenance'))
    router.message.register(devsync, Command('devsync'))
    router.callback_query.register(maintenance_screen, Adm.filter(F.act == 'maint'))
    router.callback_query.register(refresh, Adm.filter(F.act == 'diag'))
    router.callback_query.register(test_menu, Adm.filter(F.act == 'exptest'))
    router.callback_query.register(test_send, Adm.filter(F.act == 'exptestgo'))
