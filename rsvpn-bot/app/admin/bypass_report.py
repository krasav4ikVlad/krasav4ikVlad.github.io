"""`/bypassuse` — сколько ByPass ест и сколько приносит, и `/incy` — почему
не выдаётся ссылка INCY.

Две команды в одном файле, потому что вопрос у них один: «что происходит с
ByPass». Первая отвечает деньгами и гигабайтами, вторая — тем, почему
человек вместо ссылки видит «воспользуйтесь Happ».
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
from app.services import bypass_plan, bypass_usage

DEFAULT_DAYS = 30
TEST_URL = 'https://example.com/sub/test'


# ── расход и выручка ────────────────────────────────────────────────────────
async def usage(message: types.Message, command, c, settings) -> None:
    raw = (command.args or '').strip()
    days = int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_DAYS
    end = now()
    start = end - timedelta(days=days)

    await message.answer(f'{e("refresh")} Считаю…')
    data = await bypass_usage.collect(
        c.users, c.balance_log, c.vpn,
        str(await settings.get('bypass.squad_uuid') or ''), start, end)

    await message.answer(render(data))


def render(data: dict) -> str:
    days, gb = data['days'], bypass_usage.gb
    lines = [f'{e("bypass")} <b>ByPass: расход и выручка</b>',
             f'{fmt(data["start"], "%d.%m.%Y")} — {fmt(data["end"], "%d.%m.%Y")} '
             f'({days} дн.)', '']

    lines.append(f'{e("referrals")} Подключили ByPass: <b>{data["subscribers"]}</b>')
    lines.append(f'{e("traffic")} Из них качали за период: <b>{data["spenders"]}</b>')
    lines.append(f'{e("card")} Покупали гигабайты: <b>{data["payers"]}</b>')
    lines.append('')

    if not data['spenders'] and not data['squad']:
        lines.append(f'{e("warning")} Сквад ByPass не задан в настройках — '
                     f'расход спросить не у кого.')
        return '\n'.join(lines)

    # ── расход
    lines.append('<b>Сколько качают</b>')
    if data['spenders']:
        avg = data['used_bytes'] / data['spenders']
        lines.append(f'Всего: <b>{gb(data["used_bytes"])} Гб</b>')
        lines.append(f'В среднем на качающего: <b>{gb(avg)} Гб</b> за {days} дн. '
                     f'= <b>{bypass_usage.per_month(gb(avg), days)} Гб</b> в месяц')
        lines.append(f'Медиана: <b>{gb(data["median_bytes"])} Гб</b> — по ней и '
                     f'считайте типичного человека, среднее задирают верхние')
        lines.append('')
        lines.append('<b>Едят больше всех</b>')
        for row in data['top']:
            lines.append(f'   <code>{row["user_id"]}</code> — '
                         f'<b>{gb(row["bytes"])} Гб</b>')
    else:
        lines.append('Панель не показала расход за период.')
    lines.append('')

    # ── деньги
    lines.append('<b>Сколько платят</b>')
    lines.append(f'Куплено: <b>{data["gb_bought"]} Гб</b> на '
                 f'<b>{data["paid"]}₽</b> за {data["purchases"]} покупок')
    if data['payers']:
        average = data['paid'] / data['payers']
        monthly = round(bypass_usage.per_month(average, days))
        lines.append(f'В среднем на платящего: <b>{round(average)}₽</b> '
                     f'за {days} дн. = <b>{monthly}₽</b> в месяц')
        lines.append(f'Медиана: <b>{data["median_paid"]}₽</b>')
    if data['gb_bought']:
        lines.append(f'Вышло по <b>{round(data["paid"] / data["gb_bought"], 1)}₽</b> '
                     f'за гигабайт')
    lines.append(f'{e("traffic")} Не съедено на руках: '
                 f'<b>{gb(data["left_bytes"])} Гб</b> — это оплаченный трафик, '
                 f'который ещё предстоит прокачать')
    lines.append('')

    # ── главное
    if data['spenders'] and data['gb_bought']:
        real = gb(data['used_bytes'])
        lines.append(f'{e("attention")} <b>Прокачано {real} Гб, оплачено '
                     f'{data["gb_bought"]} Гб.</b>')
        if real > data['gb_bought'] * 1.5:
            lines.append('Расход обгоняет оплату — так и должно быть при '
                         'коэффициенте 0.1: с лимита списывается десятая часть '
                         'того, что реально уходит в канал. Возвращать '
                         'коэффициент к 1.0 стоит с оглядкой на эту разницу.')

    lines.append('')
    lines.append('<blockquote>Среднее считается по тем, кто качал, а не по '
                 'всем подключившим: ByPass подключают бесплатно и часто '
                 'забывают, и «среднее по всем» получается втрое меньше '
                 'настоящего.\n\nРасход — из панели, по скваду ByPass за '
                 'период. Если у нод включён коэффициент, панель показывает '
                 'то, что реально прошло через канал, а бот списывает с '
                 'лимита долю от этого.</blockquote>')
    return '\n'.join(lines)


# ── под безлимитный тариф ───────────────────────────────────────────────────
PRICES = (150, 250, 350, 500, 700, 1000)

PLAN_COLUMNS = ('id', 'username', 'гб_куплено_в_мес', 'руб_в_мес',
                'покупок', 'дней_между_покупками', 'гб_прокачано_в_мес',
                'за_обычную_подписку_в_мес', 'остаток_гб')


async def plan(message: types.Message, command, c, settings) -> None:
    """`/bypassplan [дней] [csv]` — человек за человеком, под цену безлимита."""
    parts = (command.args or '').lower().split()
    days = next((int(part) for part in parts if part.isdigit() and int(part) > 0),
                DEFAULT_DAYS)
    as_csv = 'csv' in parts

    end = now()
    start = end - timedelta(days=days)

    await message.answer(f'{e("refresh")} Считаю…')
    data = await bypass_plan.collect(
        c.users, c.balance_log, c.vpn,
        str(await settings.get('bypass.squad_uuid') or ''), start, end)

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

    cost = float(await settings.get('bypass.cost_per_gb') or 0)
    await message.answer(render_plan(data, cost))


def render_plan(data: dict, cost_per_gb: float) -> str:
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

    subs = [row['sub_month'] for row in buyers if row['sub_month']]
    if subs:
        lines.append(f'{e("card")} За обычную подписку те же люди платят '
                     f'<b>{sum(subs)}₽</b> в месяц '
                     f'(в среднем {round(sum(subs) / len(subs))}₽)')
        lines.append('')

    # ── цена безлимита
    lines.append('<b>Что будет при безлимите</b>')
    lines.append('<i>цена → перейдут → выручка за трафик в месяц</i>')
    for row in bypass_plan.simulate(buyers, list(PRICES), cost_per_gb):
        line = (f'   <b>{row["price"]}₽</b> → перейдут {row["switchers"]} → '
                f'<b>{row["revenue"]}₽</b> ({row["delta"]:+d}₽)')
        if cost_per_gb:
            line += f', трафик {row["traffic_gb"]} Гб = {row["cost"]}₽'
        lines.append(line)
    lines.append('')

    if not cost_per_gb:
        lines.append(f'{e("attention")} Цена гигабайта для вас не задана '
                     f'(/admin → ByPass → «Себестоимость гигабайта»), поэтому '
                     f'расход не посчитан — только выручка.')
        lines.append('')

    lines.append('<blockquote>Переходят те, кому это выгодно: у кого траты за '
                 'месяц выше новой цены. Остальные остаются на пакетах — '
                 'поэтому выручка почти не растёт, а на дорогих ценах не '
                 'меняется вовсе.\n\nТрафик перешедших считается вдвое выше '
                 'нынешнего: счётчик перестаёт мешать, и люди перестают '
                 'экономить. Новых покупателей, которых безлимит приведёт, '
                 'модель не знает — её ответ это «не хуже чем», а не '
                 'прогноз.\n\nСтрока на каждого: '
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
            row['used_gb_month'], row['sub_month'],
            bypass_usage.gb(row['left_bytes']),
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
    router.message.register(usage, Command('bypassuse'))
    router.message.register(plan, Command('bypassplan'))
    router.message.register(incy, Command('incy'))
    router.message.register(incy_reset, Command('incyreset'))
