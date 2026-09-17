"""`/freebies` — кто живёт на подарках.

Отчёт отвечает на вопрос «нас абузят?» числами, а не ощущением. Три
раздела, и у каждого своя цена вопроса: бесплатный сервер после истечения,
один кошелёк на много аккаунтов и бонус за возвращение — окупается он или
просто раздаётся.

Имена и id показываются нарочно: решение по каждому — человеческое.
Одинаковый кошелёк бывает и у того, кто оплачивает подписку жене и
родителям, и такого клиента наказывать не за что.
"""

from __future__ import annotations

from datetime import timedelta

from aiogram import Router, types
from aiogram.filters import Command

from app.content.emoji import e
from app.core.time import fmt, now
from app.services import freebies

DEFAULT_DAYS = 30


async def command(message: types.Message, command, c, settings) -> None:
    raw = (command.args or '').strip()
    days = int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_DAYS
    end = now()
    start = end - timedelta(days=days)

    await message.answer(f'{e("refresh")} Считаю…')

    max_days = await settings.int('lifeline.max_days')
    riders = await freebies.lifeline_riders(c.users, over_days=max_days)
    shared = await freebies.shared_payers(c.payments_repo)
    bonus = await freebies.return_bonus(c.balance_log, c.payments_repo, start, end)

    await message.answer(render(riders, shared, bonus, days, max_days))


def render(riders: dict, shared: list[dict], bonus: dict, days: int,
           max_days: int) -> str:
    lines = [f'{e("gift")} <b>Кто живёт на подарках</b>',
             f'Бонусы — за последние {days} дн., остальное — на сейчас', '']

    # ── запасной сервер ─────────────────────────────────────────────────────
    lines.append(f'{e("knot")} <b>Запасной сервер после истечения</b>')
    lines.append(f'Сидит сейчас: <b>{riders["total"]}</b>'
                 + (f', из них дольше {max_days} дн.: <b>{riders["over"]}</b>'
                    if max_days else ''))
    if riders['never_paid']:
        lines.append(f'Никогда не платили: <b>{riders["never_paid"]}</b>')
    for row in riders['rows'][:10]:
        who = f'@{row["username"]}' if row['username'] else 'без юзернейма'
        lines.append(f'   <code>{row["user_id"]}</code> ({who}) — '
                     f'{row["days"]} дн.'
                     + ('' if row['paid_ever'] else ', ни одной оплаты'))
    if not max_days:
        lines.append('<i>Предел не задан: окно продлевается на каждом '
                     'истечении, то есть бессрочно. Ставится в /admin → '
                     'Lifeline → «Дольше этого не держим».</i>')
    lines.append('')

    # ── один кошелёк на много аккаунтов ─────────────────────────────────────
    lines.append(f'{e("card")} <b>Один кошелёк — несколько аккаунтов</b>')
    if not shared:
        lines.append('Совпадений нет.')
    else:
        lines.append(f'Групп: <b>{len(shared)}</b>')
        for group in shared[:10]:
            ids = ', '.join(f'<code>{user_id}</code>'
                            for user_id in group['user_ids'][:6])
            lines.append(f'   {group["accounts"]} аккаунта: {ids} — '
                         f'{group["amount"]}₽ за {group["payments"]} оплат')
        lines.append('<i>Это не приговор: так же выглядит человек, который '
                     'оплачивает подписку жене и родителям. Смотреть стоит на '
                     'тех, у кого аккаунтов много, а оплата каждый раз '
                     'первая — они собирают бонус новичка снова и снова.</i>')
    lines.append('')

    # ── бонус за возвращение ────────────────────────────────────────────────
    lines.append(f'{e("renew")} <b>Бонус за возвращение</b>')
    lines.append(f'Роздано: <b>{bonus["given"]}₽</b> на '
                 f'<b>{bonus["people"]}</b> чел.')
    if bonus['people']:
        share = round(100 * bonus['returned'] / bonus['people'])
        lines.append(f'Заплатили после начисления: <b>{bonus["returned"]}</b> '
                     f'({share}%) на <b>{bonus["revenue"]}₽</b>')
        lines.append(f'На каждый розданный рубль вернулось: '
                     f'<b>{_per_ruble(bonus)}₽</b>')
    lines.append('')

    lines.append('<blockquote>Бонус за возвращение даётся один раз на '
                 'аккаунт — флаг кампании ставится навсегда, и «истечь ещё '
                 'раз», чтобы получить его снова, нельзя. Поэтому вопрос к '
                 'нему не «абузят ли», а «окупается ли».\n\n'
                 'По-настоящему бесконечны два других: бесплатный сервер '
                 'после истечения и новый аккаунт вместо старого.</blockquote>')
    return '\n'.join(lines)


def _per_ruble(bonus: dict) -> str:
    if not bonus['given']:
        return '—'
    return f'{bonus["revenue"] / bonus["given"]:.1f}'


def register(router: Router) -> None:
    router.message.register(command, Command('freebies'))
