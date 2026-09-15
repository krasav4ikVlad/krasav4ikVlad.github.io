"""Экономика: сколько денег пришло на самом деле и сколько из них роздано.

В /admin считаются люди — сколько зарегистрировано, сколько активных, кто в
каком сегменте. Про деньги там ровно одна строка: остатки на балансах. По
ней нельзя ответить ни на один вопрос, с которого начинается разговор о
прибыли: сколько пришло живых денег за месяц, во что обошлись бонусы,
сколько должны рефералам, на что люди тратят баланс.

Числа здесь берутся из двух мест, и различать их важно.

  * `payments` — единственный источник правды про НАСТОЯЩИЕ деньги: сколько
    человек заплатил провайдеру. Всё остальное — внутренние рубли бота.
  * `balance_log` — движение внутренних рублей: начисления, списания,
    рефералка, выводы.

Главная цифра экрана — сколько внутренних рублей создаётся на каждый рубль,
пришедший снаружи. Бонус за пополнение, бонус новичку и реферальный процент
складываются, и человек, который смотрит на каждую настройку по отдельности,
не видит их суммы. Экран показывает именно её.

Журнал появился не с первого дня, поэтому за период, начавшийся раньше его
первой записи, экран честно говорит «журнал ведётся с такого-то числа», а не
показывает ноль как факт.
"""

from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.admin.stats import collect_stats
from app.bot.callbacks import Admin as Adm
from app.content.emoji import e
from app.core.time import fmt, now
from app.repositories.balance_log import KINDS

DEFAULT_DAYS = 30
PERIODS = (7, 30, 90)

# Провайдеры показываются по-человечески: в базе лежит машинный код.
PROVIDERS = {
    'cardlink': 'Cardlink (СБП)', 'tribute': 'Tribute (карта)',
    'heleket': 'Heleket (крипта)', 'wata': 'WATA', 'severpay': 'SeverPay',
    'cards_ru': 'CloudPayments', 'admin': 'Начислено вручную',
}

# Что из списаний считать выручкой в рублях бота. Вывод рефералки сюда не
# входит: это не продажа, а возврат денег наружу.
SPEND_ORDER = ('plan', 'renewal', 'devices', 'bypass', 'private_server',
               'gift', 'promo', 'payout')


def rub(value: int | float) -> str:
    """Разряды пробелом: 45300₽ читается хуже, чем 45 300₽."""
    return f'{int(round(value)):,}'.replace(',', ' ') + '₽'


def share(part: int, whole: int) -> str:
    return f'{part * 100 // whole}%' if whole else '—'


async def collect(c, days: int = DEFAULT_DAYS) -> dict:
    """Все цифры экрана. Вынесено из хендлера, чтобы считать в тесте."""
    since = now() - timedelta(days=days)
    data: dict = {
        'days': days, 'since': since,
        'gross': 0, 'credited': 0, 'payments': 0, 'providers': {},
        'payers': set(), 'old_payers': set(),
        'referral': 0, 'payout': 0, 'spend': {}, 'topups_logged': 0,
        'journal_from': None, 'journal_covers': True,
    }

    # ── настоящие деньги ────────────────────────────────────────────────────
    async for row in c.payments_repo.col.find(
            {'status': 'done'},
            {'amount': 1, 'credited': 1, 'provider': 1, 'user_id': 1, 'created_at': 1}):
        amount = int(row.get('amount') or 0)
        user_id = row.get('user_id')
        if (row.get('created_at') or since) < since:
            # платил и раньше — нужен, чтобы отличить новых плательщиков
            data['old_payers'].add(user_id)
            continue
        data['gross'] += amount
        data['credited'] += int(row.get('credited') or amount)
        data['payments'] += 1
        data['payers'].add(user_id)
        provider = row.get('provider') or 'other'
        data['providers'][provider] = data['providers'].get(provider, 0) + amount

    # ── внутренние рубли ────────────────────────────────────────────────────
    first = await c.balance_log.col.find({}).sort('at', 1).to_list(length=1)
    if first:
        data['journal_from'] = first[0].get('at')
        data['journal_covers'] = (first[0].get('at') or since) <= since

    async for row in c.balance_log.col.find(
            {'at': {'$gte': since}}, {'amount': 1, 'kind': 1, 'direction': 1}):
        amount = int(row.get('amount') or 0)
        kind = row.get('kind') or 'other'
        if kind == 'referral' and amount > 0:
            data['referral'] += amount
        elif kind == 'topup' and amount > 0:
            data['topups_logged'] += amount
        if amount < 0:
            data['spend'][kind] = data['spend'].get(kind, 0) + (-amount)

    data['payout'] = data['spend'].get('payout', 0)

    # ── остатки и люди ──────────────────────────────────────────────────────
    stats = await collect_stats(c.users, c.config.admin_ids)
    data['balances'] = stats['balance_active'] + stats['balance_no_active']
    data['ref_balance'] = stats['ref_balance_sum']
    data['active_subs'] = stats['active_subs']
    data['total_users'] = stats['total_users']

    # ── производные ─────────────────────────────────────────────────────────
    data['bonus'] = max(0, data['credited'] - data['gross'])
    data['given'] = data['bonus'] + data['referral']
    data['new_payers'] = len(data['payers'] - data['old_payers'])
    data['obligations'] = data['credited'] + data['referral']
    return data


def advice(data: dict) -> list[str]:
    """Что из посчитанного стоит поправить. Только по своим же цифрам."""
    lines = []
    gross = data['gross']
    if not gross:
        return lines

    if data['bonus'] * 100 // gross >= 15:
        lines.append(
            f'Бонус за пополнение стоит вам {rub(data["bonus"])} за период — это '
            f'{share(data["bonus"], gross)} выручки. Это не скидка на витрине, '
            f'её никто не сравнивает с ценой конкурента: она просто уменьшает '
            f'каждый рубль. Снижение бонуса на треть — прибавка к прибыли '
            f'примерно {rub(data["bonus"] // 3)} за тот же период.')

    if data['referral'] * 100 // gross >= 15:
        lines.append(
            f'Рефералам начислено {rub(data["referral"])} — {share(data["referral"], gross)} '
            f'выручки, и это живые деньги: их выводят. Процент платится с '
            f'каждого пополнения друга, а не с первого.')

    if data['obligations'] > gross * 1.35:
        lines.append(
            f'На каждый пришедший рубль вы создаёте '
            f'{data["obligations"] / gross:.2f}₽ обязательств. Пока платящих '
            f'становится больше, это не видно; как только приток замедлится, '
            f'разница ляжет на баланс.')

    if data['active_subs'] and data['payers']:
        rate = len(data['payers']) * 100 // max(1, data['active_subs'])
        if rate < 40:
            lines.append(
                f'Платили за период {len(data["payers"])} человек при '
                f'{data["active_subs"]} активных подписках ({rate}%). Остальные '
                f'живут на балансе, бонусах и подарках — деньги от них уже '
                f'получены раньше или не получены вовсе.')

    if data['new_payers'] and len(data['payers']):
        repeat = len(data['payers']) - data['new_payers']
        if repeat < data['new_payers']:
            lines.append(
                f'Повторных плательщиков {repeat} против {data["new_payers"]} новых. '
                f'Выручка держится на притоке новых людей, а не на возвратах — '
                f'самое дорогое место в экономике.')

    return lines


def render(data: dict) -> str:
    days, gross = data['days'], data['gross']
    lines = [f'<b>{e("money")} Экономика за {days} дн.</b>', '']

    lines.append(f'<b>{e("card")} Пришло живых денег</b>')
    lines.append(f'• Всего: <code>{rub(gross)}</code>')
    lines.append(f'• Платежей: <code>{data["payments"]}</code>, '
                 f'плательщиков: <code>{len(data["payers"])}</code> '
                 f'(новых: <code>{data["new_payers"]}</code>)')
    if data['payments']:
        lines.append(f'• Средний платёж: <code>{rub(gross / data["payments"])}</code>')
    for provider, amount in sorted(data['providers'].items(),
                                   key=lambda item: -item[1]):
        lines.append(f'   ↳ {PROVIDERS.get(provider, provider)}: '
                     f'<code>{rub(amount)}</code> ({share(amount, gross)})')

    lines.append('')
    lines.append(f'<b>{e("gift")} Сколько из этого роздано</b>')
    lines.append(f'• Бонусы к пополнению: <code>−{rub(data["bonus"])}</code> '
                 f'({share(data["bonus"], gross)})')
    lines.append(f'• Начислено рефералам: <code>−{rub(data["referral"])}</code> '
                 f'({share(data["referral"], gross)})')
    lines.append(f'• Итого: <code>−{rub(data["given"])}</code> '
                 f'({share(data["given"], gross)} выручки)')
    if gross:
        lines.append(f'• <b>На 1₽ прихода создаётся '
                     f'{data["obligations"] / gross:.2f}₽ обязательств</b>')

    lines.append('')
    lines.append(f'<b>{e("hourglass")} Долги перед людьми сейчас</b>')
    lines.append(f'• Баланс на руках: <code>{rub(data["balances"])}</code>')
    lines.append(f'• Реферальный к выводу: <code>{rub(data["ref_balance"])}</code>')
    lines.append(f'• Выведено за период: <code>{rub(data["payout"])}</code>')
    lines.append('<blockquote>Это услуги и деньги, за которые вы уже получили '
                 'оплату или пообещали её вернуть. Прибылью эта сумма '
                 'становится только после того, как человек её потратит.</blockquote>')

    spend = data['spend']
    if spend:
        lines.append('')
        lines.append(f'<b>{e("cart")} На что уходит баланс</b>')
        rows = ([(kind, spend[kind]) for kind in SPEND_ORDER if spend.get(kind)]
                + [(kind, amount) for kind, amount in sorted(spend.items())
                   if kind not in SPEND_ORDER])
        for kind, amount in rows:
            lines.append(f'• {KINDS.get(kind, kind)}: <code>{rub(amount)}</code>')

    if not data['journal_covers']:
        started = fmt(data['journal_from']) if data['journal_from'] else 'ещё не начат'
        lines.append('')
        lines.append(f'{e("attention")} <i>Журнал баланса ведётся с {started} — '
                     f'рефералка, выводы и траты за более ранние дни в него не '
                     f'попали. Приход живых денег это не затрагивает: он '
                     f'считается по платежам и есть за всю историю.</i>')

    tips = advice(data)
    if tips:
        lines.append('')
        lines.append(f'<b>{e("target")} Куда смотреть</b>')
        lines += [f'• {tip}' for tip in tips]

    return '\n'.join(lines)


def keyboard(days: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(*[types.InlineKeyboardButton(
        text=f'{e("ok")} {period} дн.' if period == days else f'{period} дн.',
        callback_data=Adm(act='money', a=str(period)).pack()) for period in PERIODS])
    kb.row(types.InlineKeyboardButton(
        text=f'{e("back")} Назад', callback_data=Adm(act='main').pack()))
    return kb


async def screen(call: types.CallbackQuery, callback_data: Adm, c, settings) -> None:
    from app.admin.panel import edit

    days = int(callback_data.a) if (callback_data.a or '').isdigit() else DEFAULT_DAYS
    await call.answer('Считаю…')
    await edit(call, render(await collect(c, days)), keyboard(days))


async def command(message: types.Message, c, settings) -> None:
    parts = (message.text or '').split()
    days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else DEFAULT_DAYS
    days = max(1, min(days, 365))
    await message.answer(render(await collect(c, days)),
                         reply_markup=keyboard(days).as_markup())


def register(router: Router) -> None:
    router.message.register(command, Command('money'))
    router.callback_query.register(screen, Adm.filter(F.act == 'money'))
