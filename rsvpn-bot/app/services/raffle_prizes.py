"""Выдача призов розыгрыша: деньги и дни подписки.

Победителей у акции несколько десятков, и руками это сорок заходов в
карточку — с шансом ошибиться в одном id и узнать об этом из жалобы.
Здесь тот же список, но одним списком и с отчётом: кому начислено, кому
не дошло письмо, кого не нашли.

Два вида приза и оба простые:

  * **деньги** — на баланс, обычным начислением с записью в журнал;
  * **дни** — сдвиг даты окончания в панели и у нас, ровно как это делает
    подарок. Если подписки нет вовсе, дни выдать некуда — такой случай
    попадает в отчёт, а не теряется.

Повторная выдача защищена отметкой в документе: команду зовут с телефона,
и второе нажатие «на всякий случай» не должно удваивать призы.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from app.core.time import now, parse_dt

log = logging.getLogger(__name__)

MONEY = 'money'
DAYS = 'days'


def parse_list(text: str) -> tuple[list[dict], list[str]]:
    """Разобрать список победителей: по строке на человека.

        802421217 5000
        802421217 30д
        802421217 7 дней

    Возвращает разобранное и то, что понять не удалось: молча пропускать
    строку нельзя — это чей-то приз.
    """
    winners, broken = [], []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line:
            continue

        parts = line.replace(',', ' ').split()
        if len(parts) < 2 or not parts[0].lstrip('-').isdigit():
            broken.append(line)
            continue

        amount = parts[1].rstrip('дднейяей')
        if not amount.isdigit() or int(amount) <= 0:
            broken.append(line)
            continue

        tail = line[len(parts[0]):]
        kind = DAYS if ('д' in tail or 'd' in tail.lower()) else MONEY
        winners.append({'user_id': int(parts[0]), 'amount': int(amount),
                        'kind': kind})
    return winners, broken


async def award(users, vpn, winners: list[dict], *, mark: str = '',
                reason: str = 'Приз розыгрыша') -> dict:
    """Начислить призы. Возвращает отчёт по каждому.

    Порядок внутри человека: сначала панель (если дни), потом наша запись —
    тот же, что и при покупке. Панель отказала — не пишем себе ничего, иначе
    бот показывал бы срок, которого в панели нет.
    """
    report = {'done': [], 'failed': [], 'skipped': []}

    for winner in winners:
        user_id = int(winner['user_id'])
        user = await users.get(user_id)
        if not user:
            report['failed'].append({**winner, 'why': 'нет такого пользователя'})
            continue

        if mark and (user.get('campaigns') or {}).get(mark):
            report['skipped'].append({**winner, 'why': 'приз уже выдан'})
            continue

        try:
            if winner['kind'] == MONEY:
                ok = await users.credit(user_id, winner['amount'], reason,
                                        kind='campaign', auto=True,
                                        meta={'raffle': mark or 'prize'})
                if not ok:
                    raise RuntimeError('начисление не прошло')
            else:
                await _add_days(users, vpn, user, winner['amount'])
        except Exception as exc:                  # noqa: BLE001 — отчёт нужен весь
            log.warning('приз %s не выдан: %s', user_id, exc)
            report['failed'].append({**winner, 'why': str(exc)})
            continue

        if mark:
            await users.col.update_one({'user_data.user_id': user_id},
                                       {'$set': {f'campaigns.{mark}': now()}})
        report['done'].append(winner)

    return report


async def _add_days(users, vpn, user: dict, days: int) -> None:
    """Сдвинуть дату окончания — как это делает подарок."""
    current = (user.get('vpn') or {})
    uuid = current.get('uuid')
    if not uuid:
        raise RuntimeError('подписки нет — дни выдавать некуда')

    expires = parse_dt(current.get('expireAt'))
    base = expires if expires and expires > now() else now()
    new_expire = base + timedelta(days=int(days))

    user_id = int(((user.get('user_data') or {}).get('user_id')) or 0)
    await vpn.update_subscription(uuid, expire_at=new_expire)
    await users.set_vpn(user_id, {'expireAt': new_expire})

    # Дни двигают основную дату — значит, и ByPass: иначе он отключится
    # раньше подписки, которую человек только что выиграл.
    from app.services import bypass

    await bypass.sync_expiry(users, vpn, user_id, new_expire, vpn=current)
