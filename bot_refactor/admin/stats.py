"""Статистика админки — один раз.

В admin.py тот же самый подсчёт написан дважды: в admin_panel() (команда /admin)
и в ветке call.data.endswith(':main'). Причём во второй копии уже потерялись
сегменты — классический симптом дублирования.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from loader import admins_ids, users

MSK = ZoneInfo('Europe/Moscow')

SEGMENT_USERS_LABELS = {
    'new_trial_d0': '🆕 Новые триал D0 (день 0)',
    'new_trial_d1': '🆕 Новые триал D1 (день 1)',
    'new_trial_d2': '🆕 Новые триал D2 (день 2)',
    'new_trial_d2_hot': '🔥 Новые триал D2 HOT (≤6ч)',
    'new_trial_d3': '🆕 Новые триал D3 (день 3)',
    'new_trial_d3_hot': '🔥 Новые триал D3 HOT (≤2ч)',
    'trial': '🧪 Триал',
    'active_no_topup': '🟡 Активные без пополнения',
    'first_payment_active': '💳 Активные с 1 оплатой',
    'active_paid': '🟢 Активные платящие',
    'expiring_3d': '⏳ Истекают до 3 дней',
    'expired_1d': '🔁 Истекли 0–1 день',
    'expired_3d': '🔁 Истекли 2–3 дня',
    'expired_7d': '🔁 Истекли 4–7 дней',
    'expired_14d': '🔁 Истекли 8–14 дней',
    'expired_21d': '🔁 Истекли 15–21 день',
    'expired_30d': '🔁 Истекли 22–30 дней',
    'churned_45d': '💀 Ушли 31–45 дней',
    'churned_60d': '💀 Ушли 46–60 дней',
    'churned_90d': '💀 Ушли 61–90 дней',
    'churned_dead': '💀 Ушли 90+ дней',
    'inactive_no_sub': '⚪ Без подписки и оплат',
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


async def build_stats_text() -> str:
    now = datetime.now(MSK)
    total_users = await users.count_documents({})

    cursor = users.find(
        {'user_data.user_id': {'$nin': list(admins_ids)}},
        {
            'user_data.user_id': 1, 'info.balance': 1, 'info.ref_stats.balance': 1,
            'info.transactions': 1, 'vpn.shortUuid': 1, 'vpn.expireAt': 1, 'growth.segment': 1,
        },
    )

    total_subs = active_subs = active_with_topup = 0
    balance_active = balance_no_active = ref_balance_sum = 0
    segment_counts = {key: 0 for key in SEGMENT_USERS_LABELS}
    segment_counts['new_trial'] = 0
    unknown_segments = 0

    async for doc in cursor:
        info = doc.get('info') or {}
        vpn = doc.get('vpn') or {}

        balance = int(info.get('balance', 0) or 0)
        ref_balance_sum += int((info.get('ref_stats') or {}).get('balance', 0) or 0)

        expire = parse_mongo_date(vpn.get('expireAt'))
        if expire:
            expire = expire.replace(tzinfo=MSK) if expire.tzinfo is None else expire.astimezone(MSK)

        has_sub = bool(vpn.get('shortUuid'))
        is_active = bool(has_sub and expire and expire > now)

        total_subs += int(has_sub)
        active_subs += int(is_active)
        if is_active:
            balance_active += balance
            active_with_topup += int(user_has_topup(info))
        else:
            balance_no_active += balance

        segment = (doc.get('growth') or {}).get('segment')
        if segment in segment_counts:
            segment_counts[segment] += 1
        else:
            unknown_segments += 1

    lines = [f'• 🆕 Новые триал (итого): <code>{sum(segment_counts[k] for k in NEW_TRIAL_KEYS)}</code>']
    lines += [f'  ↳ {SEGMENT_USERS_LABELS[k]}: <code>{segment_counts[k]}</code>'
              for k in NEW_TRIAL_KEYS if k in SEGMENT_USERS_LABELS]
    lines += [f'• {SEGMENT_USERS_LABELS[k]}: <code>{segment_counts[k]}</code>'
              for k in ('trial', 'active_no_topup', 'first_payment_active', 'active_paid', 'expiring_3d')]
    lines.append(f'• 🔁 Ушедшие (итого): <code>{sum(segment_counts[k] for k in EXPIRED_KEYS)}</code>')
    lines += [f'  ↳ {SEGMENT_USERS_LABELS[k]}: <code>{segment_counts[k]}</code>' for k in EXPIRED_KEYS]
    lines.append(f'• {SEGMENT_USERS_LABELS["inactive_no_sub"]}: <code>{segment_counts["inactive_no_sub"]}</code>')
    if unknown_segments:
        lines.append(f'• ❔ Не определён: <code>{unknown_segments}</code>')

    return (
        '<b>🛠 Админ-панель RS VPN</b>\n\n'
        '<b>👥 Пользователи</b>\n'
        f'• Всего зарегистрировано: <code>{total_users}</code>\n\n'
        '<b>🛡 Подписки RS VPN</b>\n'
        f'• Всего подписок: <code>{total_subs}</code>\n'
        f'• Активных подписок: <code>{active_subs}</code>\n'
        f'• Активных подписок с пополнением: <code>{active_with_topup}</code>\n\n'
        '<b>💰 Балансы</b>\n'
        f'• Баланс с активной подпиской: <code>{balance_active}₽</code>\n'
        f'• Баланс без активной подписки: <code>{balance_no_active}₽</code>\n'
        f'• Суммарный реферальный баланс: <code>{ref_balance_sum}₽</code>\n\n'
        '<b>🧩 Сегменты пользователей</b>\n' + '\n'.join(lines)
    )
