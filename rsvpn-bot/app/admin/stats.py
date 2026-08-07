"""Статистика админки — один раз.

В admin.py тот же самый подсчёт написан дважды: в admin_panel() (команда /admin)
и в ветке call.data.endswith(':main'). Причём во второй копии уже потерялись
сегменты — классический симптом дублирования.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from app.content.emoji import e

log = logging.getLogger(__name__)

MSK = ZoneInfo('Europe/Moscow')

SEGMENT_USERS_LABELS = {
    'new_trial_d0': f'{e("new")} Новые триал D0 (день 0)',
    'new_trial_d1': f'{e("new")} Новые триал D1 (день 1)',
    'new_trial_d2': f'{e("new")} Новые триал D2 (день 2)',
    'new_trial_d2_hot': f'{e("hot")} Новые триал D2 HOT (≤6ч)',
    'new_trial_d3': f'{e("new")} Новые триал D3 (день 3)',
    'new_trial_d3_hot': f'{e("hot")} Новые триал D3 HOT (≤2ч)',
    'trial': f'{e("trial")} Триал',
    'active_no_topup': f'{e("yellow")} Активные без пополнения',
    'first_payment_active': f'{e("card")} Активные с 1 оплатой',
    'active_paid': f'{e("green")} Активные платящие',
    'expiring_3d': f'{e("hourglass")} Истекают до 3 дней',
    'expired_1d': f'{e("renew")} Истекли 0–1 день',
    'expired_3d': f'{e("renew")} Истекли 2–3 дня',
    'expired_7d': f'{e("renew")} Истекли 4–7 дней',
    'expired_14d': f'{e("renew")} Истекли 8–14 дней',
    'expired_21d': f'{e("renew")} Истекли 15–21 день',
    'expired_30d': f'{e("renew")} Истекли 22–30 дней',
    'churned_45d': f'{e("skull")} Ушли 31–45 дней',
    'churned_60d': f'{e("skull")} Ушли 46–60 дней',
    'churned_90d': f'{e("skull")} Ушли 61–90 дней',
    'churned_dead': f'{e("skull")} Ушли 90+ дней',
    'inactive_no_sub': f'{e("white")} Без подписки и оплат',
}

NEW_TRIAL_KEYS = ('new_trial', 'new_trial_d0', 'new_trial_d1', 'new_trial_d2',
                  'new_trial_d2_hot', 'new_trial_d3', 'new_trial_d3_hot')
EXPIRED_KEYS = ('expired_1d', 'expired_3d', 'expired_7d', 'expired_14d', 'expired_21d',
                'expired_30d', 'churned_45d', 'churned_60d', 'churned_90d', 'churned_dead')


def parse_mongo_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, dict) and '$date' in value:
        value = value['$date']
    if isinstance(value, str):
        raw = value.strip()
        try:
            return datetime.fromisoformat(raw.replace('Z', '+00:00') if raw.endswith('Z') else raw)
        except ValueError:
            pass
        try:
            return datetime.strptime(raw, '%d.%m.%Y %H:%M:%S')
        except ValueError:
            return None
    return None


def user_has_topup(info: dict) -> bool:
    for tr in info.get('transactions') or []:
        if isinstance(tr, list) and len(tr) >= 3:
            amount, descr = tr[0], str(tr[2] or '')
        elif isinstance(tr, dict):
            amount = tr.get('amount', 0)
            descr = str(tr.get('description') or tr.get('descr') or '')
        else:
            continue
        if isinstance(amount, (int, float)) and amount > 0 and 'Пополнение' in descr:
            return True
    return False


def _blank_counts() -> dict[str, int]:
    counts = {key: 0 for key in SEGMENT_USERS_LABELS}
    counts['new_trial'] = 0
    return counts


def _pipeline(admin_ids, moment) -> list[dict]:
    """Вся статистика одним проходом на стороне Mongo.

    Раньше бот вычитывал КАЖДЫЙ документ пользователя вместе с
    `info.transactions` — на боевой базе это сотни мегабайт по сети и
    минуты ожидания перед экраном /admin. Здесь наружу приезжает один
    небольшой документ со всеми суммами.

    `has_topup` берётся из growth (его считает почасовая задача сегментов),
    а не разбором транзакций: именно массив транзакций и был тяжёлым.
    """
    admins = list(admin_ids)
    counted = {'$eq': ['$is_admin', 0]}          # админов в срезы не берём

    def total_if(condition):
        return {'$sum': {'$cond': [{'$and': [counted, condition]}, 1, 0]}}

    def money_if(condition):
        return {'$sum': {'$cond': [{'$and': [counted, condition]}, '$balance', 0]}}

    return [
        {'$project': {
            'is_admin': {'$cond': [{'$in': ['$user_data.user_id', admins]}, 1, 0]},
            'balance': {'$ifNull': ['$info.balance', 0]},
            'ref_balance': {'$ifNull': ['$info.ref_stats.balance', 0]},
            'segment': {'$ifNull': ['$growth.segment', '']},
            'has_topup': {'$eq': ['$growth.has_topup', True]},
            'has_sub': {'$gt': [{'$strLenCP': {'$ifNull': ['$vpn.shortUuid', '']}}, 0]},
            # дата в документе может быть строкой (наследие старых записей) —
            # тогда полагаемся на флаг из сегментов, а не сравниваем типы
            'is_active': {'$cond': [
                {'$eq': [{'$type': '$vpn.expireAt'}, 'date']},
                {'$gt': ['$vpn.expireAt', moment]},
                {'$eq': ['$growth.is_active', True]},
            ]},
        }},
        {'$facet': {
            'totals': [{'$group': {
                '_id': None,
                'total_users': {'$sum': 1},
                'total_subs': total_if({'$eq': ['$has_sub', True]}),
                'active_subs': total_if({'$eq': ['$is_active', True]}),
                'active_with_topup': total_if({'$and': [{'$eq': ['$is_active', True]},
                                                        {'$eq': ['$has_topup', True]}]}),
                'balance_active': money_if({'$eq': ['$is_active', True]}),
                'balance_no_active': money_if({'$eq': ['$is_active', False]}),
                'ref_balance_sum': {'$sum': {'$cond': [counted, '$ref_balance', 0]}},
            }}],
            'segments': [
                {'$match': {'is_admin': 0}},
                {'$group': {'_id': '$segment', 'n': {'$sum': 1}}},
            ],
        }},
    ]


async def collect_stats(users_repo, admin_ids) -> dict:
    """Числа для экрана. Сначала пробуем сервер, иначе считаем сами.

    Ловим любую ошибку, а не только «не умею aggregate»: если агрегация
    не пойдёт на конкретной версии Mongo или споткнётся о неожиданный тип
    в документе, админка должна открыться медленно, но открыться.
    Строчка в логе покажет, что запасной путь включился.
    """
    moment = datetime.now(MSK)
    try:
        result = await users_repo.col.aggregate(
            _pipeline(admin_ids, moment), allowDiskUse=True).to_list(length=1)
    except (AttributeError, NotImplementedError, TypeError):
        return await _collect_in_python(users_repo, admin_ids, moment)
    except Exception as exc:
        log.warning('статистика: агрегация не удалась (%s), считаем перебором', exc)
        return await _collect_in_python(users_repo, admin_ids, moment)

    facet = (result or [{}])[0] or {}
    totals = ((facet.get('totals') or [{}])[0]) or {}

    counts = _blank_counts()
    unknown = 0
    for row in facet.get('segments') or []:
        if row.get('_id') in counts:
            counts[row['_id']] += row.get('n', 0)
        else:
            unknown += row.get('n', 0)

    return {'segments': counts, 'unknown': unknown,
            **{key: int(totals.get(key, 0) or 0) for key in (
                'total_users', 'total_subs', 'active_subs', 'active_with_topup',
                'balance_active', 'balance_no_active', 'ref_balance_sum')}}


async def _collect_in_python(users_repo, admin_ids, moment) -> dict:
    """Запасной путь: заглушка в тестах и старые Mongo без $facet."""
    users = users_repo.col
    numbers = dict.fromkeys(
        ('total_users', 'total_subs', 'active_subs', 'active_with_topup',
         'balance_active', 'balance_no_active', 'ref_balance_sum'), 0)
    counts = _blank_counts()
    unknown = 0

    async for doc in users.find({}, {
            'user_data.user_id': 1, 'info.balance': 1, 'info.ref_stats.balance': 1,
            'info.transactions': 1, 'vpn.shortUuid': 1, 'vpn.expireAt': 1, 'growth': 1}):
        numbers['total_users'] += 1
        if (doc.get('user_data') or {}).get('user_id') in set(admin_ids):
            continue

        info, vpn, growth = doc.get('info') or {}, doc.get('vpn') or {}, doc.get('growth') or {}
        balance = int(info.get('balance', 0) or 0)
        numbers['ref_balance_sum'] += int((info.get('ref_stats') or {}).get('balance', 0) or 0)

        expire = parse_mongo_date(vpn.get('expireAt'))
        if expire:
            expire = expire.replace(tzinfo=MSK) if expire.tzinfo is None else expire.astimezone(MSK)

        has_sub = bool(vpn.get('shortUuid'))
        is_active = bool(has_sub and expire and expire > moment)

        numbers['total_subs'] += int(has_sub)
        numbers['active_subs'] += int(is_active)
        if is_active:
            numbers['balance_active'] += balance
            numbers['active_with_topup'] += int(growth.get('has_topup')
                                                or user_has_topup(info))
        else:
            numbers['balance_no_active'] += balance

        segment = growth.get('segment')
        if segment in counts:
            counts[segment] += 1
        else:
            unknown += 1

    return {'segments': counts, 'unknown': unknown, **numbers}


async def build_stats_text(users_repo, admin_ids) -> str:
    """users_repo приезжает аргументом — статистику можно посчитать в тесте."""
    data = await collect_stats(users_repo, admin_ids)
    return render_stats(data)


def render_stats(data: dict) -> str:
    segment_counts = data['segments']
    total_users = data['total_users']
    total_subs, active_subs = data['total_subs'], data['active_subs']
    active_with_topup = data['active_with_topup']
    balance_active, balance_no_active = data['balance_active'], data['balance_no_active']
    ref_balance_sum, unknown_segments = data['ref_balance_sum'], data['unknown']

    lines = [f'• {e("new")} Новые триал (итого): <code>{sum(segment_counts[k] for k in NEW_TRIAL_KEYS)}</code>']
    lines += [f'  ↳ {SEGMENT_USERS_LABELS[k]}: <code>{segment_counts[k]}</code>'
              for k in NEW_TRIAL_KEYS if k in SEGMENT_USERS_LABELS]
    lines += [f'• {SEGMENT_USERS_LABELS[k]}: <code>{segment_counts[k]}</code>'
              for k in ('trial', 'active_no_topup', 'first_payment_active', 'active_paid', 'expiring_3d')]
    lines.append(f'• {e("renew")} Ушедшие (итого): <code>{sum(segment_counts[k] for k in EXPIRED_KEYS)}</code>')
    lines += [f'  ↳ {SEGMENT_USERS_LABELS[k]}: <code>{segment_counts[k]}</code>' for k in EXPIRED_KEYS]
    lines.append(f'• {SEGMENT_USERS_LABELS["inactive_no_sub"]}: <code>{segment_counts["inactive_no_sub"]}</code>')
    if unknown_segments:
        lines.append(f'• {e("question")} Не определён: <code>{unknown_segments}</code>')

    return (
        f'<b>{e("tools")} Админ-панель RS VPN</b>\n\n'
        f'<b>{e("friends")} Пользователи</b>\n'
        f'• Всего зарегистрировано: <code>{total_users}</code>\n\n'
        f'<b>{e("shield")} Подписки RS VPN</b>\n'
        f'• Всего подписок: <code>{total_subs}</code>\n'
        f'• Активных подписок: <code>{active_subs}</code>\n'
        f'• Активных подписок с пополнением: <code>{active_with_topup}</code>\n\n'
        f'<b>{e("money")} Балансы</b>\n'
        f'• Баланс с активной подпиской: <code>{balance_active}₽</code>\n'
        f'• Баланс без активной подписки: <code>{balance_no_active}₽</code>\n'
        f'• Суммарный реферальный баланс: <code>{ref_balance_sum}₽</code>\n\n'
        f'<b>{e("puzzle")} Сегменты пользователей</b>\n' + '\n'.join(lines)
    )


class StatsService:
    """Статистика с коротким кэшем.

    В /admin заходят подряд: открыл, зашёл в настройки, вернулся, открыл
    снова. Пересчитывать всю базу на каждый такой переход незачем — цифры
    за минуту не меняются настолько, чтобы это было заметно. Кнопка
    «Обновить статистику» считает заново, минуя кэш.
    """

    def __init__(self, users_repo, admin_ids, ttl: float = 60.0):
        self.users = users_repo
        self.admin_ids = admin_ids
        self.ttl = ttl
        self._text = ''
        self._at = 0.0

    async def text(self, force: bool = False) -> str:
        import time

        if not force and self._text and (time.monotonic() - self._at) < self.ttl:
            return self._text

        self._text = await build_stats_text(self.users, self.admin_ids)
        self._at = time.monotonic()
        return self._text

    def invalidate(self) -> None:
        self._at = 0.0
