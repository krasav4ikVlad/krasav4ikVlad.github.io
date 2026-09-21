"""ByPass в цифрах: средняя ставка, разбор под безлимит и диагностика INCY.

Три команды об одном — что происходит с ByPass. `/bypassavg` отвечает одной
цифрой «сколько приносит активный покупатель», `/bypassplan` раскладывает
её по людям и по ценам будущего безлимита, `/incy` объясняет, почему
человек вместо ссылки видит «воспользуйтесь Happ».

Панель ни одна из них не спрашивает. Расход по скваду она отдаёт под
своими внутренними номерами, а не под telegram id, свести их не с чем — и
не нужно: гигабайты покупают впрок и тратят до лимита, поэтому проданное и
есть прокачанное, с задержкой в несколько дней.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
from datetime import timedelta

from aiogram import Router, types
from aiogram.filters import Command

from app.content.emoji import e
from app.core.time import fmt, now
from app.integrations.vpn.links import LinkEncryptionError
from app.services import bypass_arpu, bypass_plan

DEFAULT_DAYS = 30
TEST_URL = 'https://example.com/sub/test'


# ── средний заработок с активного ───────────────────────────────────────────
async def average(message: types.Message, command, c, settings) -> None:
    """`/bypassavg [дней]` — сколько приносит активный покупатель ByPass."""
    raw = (command.args or '').strip()
    days = int(raw) if raw.isdigit() and int(raw) > 0 else bypass_arpu.ACTIVE_DAYS

    await message.answer(f'{e("refresh")} Считаю…')
    await message.answer(render_average(
        await bypass_arpu.collect(c.balance_log, active_days=days)))


def render_average(data: dict) -> str:
    lines = [f'{e("bypass")} <b>ByPass: сколько приносит покупатель</b>',
             f'Активными считаем тех, кто покупал за последние '
             f'{data["active_days"]} дн.', '']

    if not data['active']:
        lines.append('Активных покупателей нет.')
        return '\n'.join(lines)

    lines.append(f'{e("referrals")} Активных покупателей: <b>{data["active"]}</b>')
    lines.append(f'{e("money")} <b>В среднем {data["average"]}₽ в месяц</b> '
                 f'с человека')
    lines.append(f'   медиана: <b>{data["median"]}₽</b>, '
                 f'трафика {data["gb_average"]} Гб в месяц')
    lines.append(f'   за последние 30 дней они заплатили '
                 f'<b>{data["revenue_30"]}₽</b> — это '
                 f'<b>{data["simple"]}₽</b> на человека')
    lines.append('')

    if data['gap']:
        lines.append(f'{e("calendar")} Докупают в среднем раз в '
                     f'<b>{data["gap"]}</b> дн., покупок за всё время: '
                     f'{data["purchases"]}')
        lines.append('')

    lines.append('<b>Сколько платят в месяц</b>')
    for row in data['buckets']:
        if not row['people']:
            continue
        title = (f'{row["from"]}–{row["to"]}₽' if row['to']
                 else f'{row["from"]}₽ и больше')
        lines.append(f'   {title}: <b>{row["people"]}</b> чел.')
    lines.append('')

    if data['gone']:
        together = round((data['average'] * data['active']
                          + data['gone_rate'] * data['gone'])
                         / (data['active'] + data['gone']))
        lines.append(f'{e("cross")} Отсеяны как ушедшие: <b>{data["gone"]}</b> '
                     f'(покупали, но давно)')
        for row in data['gone_buckets']:
            title = (f'{row["from"]}–{row["to"]} дн. назад' if row['to']
                     else f'больше {row["from"]} дн. назад')
            lines.append(f'   {title}: {row["people"]} чел.')
        lines.append(f'   <i>Если бы считали вместе с ними, вышло бы '
                     f'{together}₽ вместо {data["average"]}₽.</i>')
        lines.append('')

    lines.append('<blockquote>Ставка считается по каждому за его собственный '
                 'срок: сколько заплатил с первой покупки, приведённое к '
                 'месяцу. Иначе тот, кто взял 100 ГБ на два месяца, в один '
                 'месяц выглядел бы богачом, а в другой — нулём, хотя платит '
                 'ровно столько же.\n\nСрок берётся не меньше месяца: '
                 'вчерашняя покупка на 500₽ — это не 15 000₽ в месяц, а '
                 'человек, который только начал.</blockquote>')
    return '\n'.join(lines)


# ── под безлимитный тариф ───────────────────────────────────────────────────
PRICES = (150, 250, 350, 500, 700, 1000)

PLAN_COLUMNS = ('id', 'username', 'гб_куплено_в_мес', 'руб_в_мес',
                'покупок', 'дней_между_покупками',
                'за_обычную_подписку_в_мес', 'лимит_выдан_гб')


async def plan(message: types.Message, command, c, settings) -> None:
    """`/bypassplan [дней] [csv]` — человек за человеком, под цену безлимита."""
    parts = (command.args or '').lower().split()
    days = next((int(part) for part in parts if part.isdigit() and int(part) > 0),
                DEFAULT_DAYS)
    as_csv = 'csv' in parts

    end = now()
    start = end - timedelta(days=days)

    await message.answer(f'{e("refresh")} Считаю…')
    data = await bypass_plan.collect(c.users, c.balance_log, start, end)

    if as_csv:
        if not data['rows']:
            await message.answer(f'{e("cross")} Выгружать нечего: ByPass никто '
                                 f'не подключал.')
            return
        await message.answer_document(
            types.BufferedInputFile(
                plan_csv(data),
                filename=f'bypass-{fmt(start, "%d.%m.%Y")}-{fmt(end, "%d.%m.%Y")}.csv'),
            caption=f'{e("bypass")} Строка на каждого, у кого подключён ByPass. '
                    f'Всё приведено к месяцу.')
        return

    await message.answer(render_plan(
        data,
        cost_month=await settings.int('bypass.cost_month'),
        rate=float(await settings.get('bypass.traffic_rate') or 1.0)))


def render_plan(data: dict, cost_month: int = 0, rate: float = 1.0) -> str:
    rows, days = data['rows'], data['days']
    buyers = data['buyers']

    lines = [f'{e("bypass")} <b>ByPass: кто сколько покупает</b>',
             f'{fmt(data["start"], "%d.%m.%Y")} — {fmt(data["end"], "%d.%m.%Y")} '
             f'({days} дн., всё приведено к месяцу)', '']

    lines.append(f'Подключили ByPass: <b>{len(rows)}</b>')
    lines.append(f'Покупали гигабайты: <b>{len(buyers)}</b>')
    if not buyers:
        lines.append('')
        lines.append('<i>Никто ничего не покупал — цену безлимита считать '
                     'не из чего.</i>')
        return '\n'.join(lines)

    repeat = [row for row in buyers if row['purchases'] > 1]
    lines.append(f'Докупали больше одного раза: <b>{len(repeat)}</b>'
                 + (f' — в среднем раз в '
                    f'<b>{round(sum(row["gap_days"] for row in repeat) / len(repeat))}</b> дн.'
                    if repeat else ''))
    lines.append('')

    # ── распределение
    lines.append('<b>Сколько гигабайт в месяц покупают</b>')
    for row in bypass_plan.buckets(buyers):
        title = (f'{row["from"]}–{row["to"]} Гб' if row['to']
                 else f'{row["from"]}+ Гб')
        if row['people']:
            lines.append(f'   {title}: <b>{row["people"]}</b> чел., '
                         f'платят {row["spent"]}₽/мес')
    lines.append('')

    spend = [row['spent_month'] for row in buyers]
    volume = [row['gb_month'] for row in buyers]
    lines.append('<b>Сколько платят за трафик</b>')
    for share in (50, 75, 90, 95):
        lines.append(f'   {share}% укладываются в '
                     f'<b>{round(bypass_plan.percentile(spend, share))}₽</b> '
                     f'и <b>{bypass_plan.percentile(volume, share)} Гб</b> в месяц')
    lines.append(f'   Всего за трафик: <b>{sum(spend)}₽</b> в месяц')
    lines.append('')

    if data['sub_payers']:
        lines.append(f'{e("card")} За обычную подписку те же люди заплатили '
                     f'<b>{data["sub_month"]}₽</b> в месяц — '
                     f'{data["sub_payers"]} чел. по '
                     f'{round(data["sub_month"] / data["sub_payers"])}₽')
        lines.append(f'   <i>Считаются только платежи внутри периода: кто '
                     f'оплатил полгода вперёд до его начала, здесь выглядит '
                     f'как ноль.</i>')
        lines.append('')

    # ── сходится ли вообще
    money = bypass_plan.economics(data, cost_month, rate)
    if cost_month:
        lines.append('<b>Сходится ли ByPass</b>')
        lines.append(f'Выручка за трафик: <b>{money["revenue_month"]}₽</b> в месяц')
        lines.append(f'Серверы: <b>−{money["cost_month"]}₽</b> в месяц')
        lines.append(f'Итого: <b>{money["profit"]:+d}₽</b>')
        lines.append('')
        lines.append(f'Продано <b>{money["sold_gb"]} Гб</b> в месяц; при '
                     f'коэффициенте {money["rate"]} в канал ушло '
                     f'<b>{money["real_gb"]} Гб</b>')
        lines.append(f'Настоящий гигабайт обходится в '
                     f'<b>{money["cost_per_real_gb"]}₽</b>, проданный — в '
                     f'<b>{money["cost_per_sold_gb"]}₽</b> при цене '
                     f'<b>{money["price_per_sold_gb"]}₽</b>')
        if money['cost_per_sold_gb'] > money['price_per_sold_gb']:
            lines.append(f'{e("attention")} <b>Продаём дешевле, чем '
                         f'обходится.</b> Разницу создаёт коэффициент: с '
                         f'лимита списывается доля того, что уходит в канал, '
                         f'а платим мы за всё.')
        lines.append('')

    # ── цена безлимита
    lines.append('<b>Что будет при безлимите</b>')
    lines.append('<i>цена → перейдут → выручка за трафик в месяц</i>')
    for row in bypass_plan.simulate(buyers, list(PRICES),
                                    money['cost_per_real_gb'], rate):
        line = (f'   <b>{row["price"]}₽</b> → перейдут {row["switchers"]} → '
                f'<b>{row["revenue"]}₽</b> ({row["delta"]:+d}₽)')
        if cost_month:
            line += f', трафик {row["traffic_gb"]} Гб = {row["cost"]}₽'
        lines.append(line)
    lines.append('')

    if not cost_month:
        lines.append(f'{e("attention")} Стоимость серверов не задана '
                     f'(/admin → ByPass → «Серверы ByPass в месяц»), поэтому '
                     f'расход не посчитан — только выручка.')
        lines.append('')

    lines.append('<blockquote>Переходят те, кому это выгодно: у кого траты за '
                 'месяц выше новой цены. Остальные остаются на пакетах — '
                 'поэтому выручка почти не растёт, а на дорогих ценах не '
                 'меняется вовсе.\n\nТрафик перешедших считается вдвое выше '
                 'нынешнего: счётчик перестаёт мешать, и люди перестают '
                 'экономить. Новых покупателей, которых безлимит приведёт, '
                 'модель не знает — её ответ это «не хуже чем», а не '
                 'прогноз.\n\nПанель не спрашивается: всё из журнала '
                 'списаний. Строка на каждого — '
                 '<code>/bypassplan csv</code>.</blockquote>')
    return '\n'.join(lines)


def plan_csv(data: dict) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=';', lineterminator='\r\n')
    writer.writerow(PLAN_COLUMNS)
    for row in data['rows']:
        writer.writerow([
            row['user_id'], row['username'], row['gb_month'],
            row['spent_month'], row['purchases'], row['gap_days'] or '',
            row['sub_month'], round(row['limit_bytes'] / bypass_plan.GB, 1),
        ])
    return buffer.getvalue().encode('utf-8-sig')


# ── почему не работает INCY ─────────────────────────────────────────────────
async def incy(message: types.Message, c, settings) -> None:
    """Проверка энкодера INCY: где он, есть ли node и что он отвечает.

    «Подключение через INCY недоступно» — честный текст для человека и
    пустой для того, кто чинит: ни пути, ни ошибки node в нём нет.
    """
    script = c.config.vpn.incy_script
    cwd = c.config.vpn.incy_cwd

    lines = [f'{e("tools")} <b>Энкодер INCY</b>', '']
    lines.append(f'INCY_ENCODER: <code>{script or "не задан"}</code>')
    if script:
        lines.append(f'   файл на месте: {_yes(os.path.isfile(script))}')
    lines.append(f'INCY_CWD: <code>{cwd or "не задан"}</code>')
    if cwd:
        lines.append(f'   каталог на месте: {_yes(os.path.isdir(cwd))}')

    node = shutil.which('node')
    lines.append(f'node: <code>{node or "не найден в PATH"}</code>')
    lines.append('')

    try:
        link = await c.links.for_app('incy', TEST_URL)
    except LinkEncryptionError as exc:
        lines.append(f'{e("cross")} <b>Ссылка не собралась</b>')
        lines.append(f'<code>{exc}</code>')
        lines.append('')
        lines.append(_advice(script, cwd, node))
    except Exception as exc:                       # noqa: BLE001 — диагностика
        lines.append(f'{e("cross")} <b>Неожиданная ошибка</b>')
        lines.append(f'<code>{type(exc).__name__}: {exc}</code>')
    else:
        lines.append(f'{e("ok")} <b>Ссылка собирается</b>')
        lines.append(f'<code>{link[:80]}…</code>')
        lines.append('')
        lines.append('<i>Если человек всё равно видит «воспользуйтесь Happ» — '
                     'у него закеширована старая неудача: ссылка считается '
                     'один раз и хранится в его карточке. Такой кеш снимается '
                     'командой <code>/incyreset 802421217</code>.</i>')

    await message.answer('\n'.join(lines))


def _yes(value: bool) -> str:
    return 'да' if value else 'нет'


def _advice(script: str, cwd: str, node: str | None) -> str:
    if not script:
        return ('<b>Что делать:</b> в <code>.env</code> нет переменной '
                '<code>INCY_ENCODER</code> — путь к node-скрипту энкодера. '
                'Пропишите её и перезапустите бота. Пока её нет, INCY не '
                'работает ни в ByPass, ни в обычной подписке.')
    if not os.path.isfile(script):
        return ('<b>Что делать:</b> файла по этому пути нет. Бот и исходники '
                'лежат в разных каталогах, поэтому путь должен быть '
                'абсолютным — относительный считается от каталога запуска '
                'pm2, а не от папки с кодом.')
    if not node:
        return ('<b>Что делать:</b> в PATH нет <code>node</code>. Под pm2 '
                'окружение своё: node ставится в систему '
                '(<code>apt install nodejs</code>) или прописывается полным '
                'путём в ecosystem.config.js.')
    return ('<b>Что делать:</b> скрипт на месте и node есть — значит падает '
            'сам энкодер. Полный текст его ошибки выше: обычно это '
            'недостающие зависимости, лечится <code>npm install</code> в '
            'каталоге INCY_CWD.')


async def incy_reset(message: types.Message, command, c, settings) -> None:
    """`/incyreset <id>` — забыть посчитанную ссылку INCY у человека.

    Ссылка считается один раз и кладётся в карточку. Пока энкодер был сломан,
    класть было нечего, — но у тех, кто успел получить ссылку до поломки,
    лежит старая. Эта команда заставляет пересчитать.
    """
    target = (command.args or '').strip()
    if not target:
        await message.answer(f'{e("cross")} <code>/incyreset 802421217</code> — '
                             f'или <code>/incyreset all</code> для всех.')
        return

    if target.lower() == 'all':
        result = await c.users.col.update_many(
            {'vpn.bypass_connectUrl_incy': {'$nin': ['', None]}},
            {'$unset': {'vpn.bypass_connectUrl_incy': ''}})
        await message.answer(
            f'{e("ok")} Сброшено ссылок: <b>{result.modified_count}</b>.\n\n'
            f'<blockquote>Следующее нажатие на INCY соберёт ссылку заново.'
            f'</blockquote>')
        return

    user = await c.moderation.find_user(target)
    if not user:
        await message.answer(f'{e("cross")} Пользователь <code>{target}</code> '
                             f'не найден.')
        return

    user_id = (user.get('user_data') or {}).get('user_id')
    await c.users.col.update_one(
        {'user_data.user_id': user_id},
        {'$unset': {'vpn.bypass_connectUrl_incy': '', 'vpn.connectUrl_incy': ''}})
    await message.answer(f'{e("ok")} Ссылки INCY у <code>{user_id}</code> '
                         f'сброшены — соберутся заново при следующем нажатии.')


def register(router: Router) -> None:
    router.message.register(average, Command('bypassavg'))
    router.message.register(plan, Command('bypassplan'))
    router.message.register(incy, Command('incy'))
    router.message.register(incy_reset, Command('incyreset'))
