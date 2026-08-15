"""ByPass-подписка: та же дата окончания, что и у основной.

ByPass — отдельный пользователь в панели, и это источник расхождений: любое
место, которое двигает `vpn.expireAt`, обязано двигать и `bypass_expireAt`.
Мест таких много — продление, триал, подарок, доступ к личному серверу, — и
каждое помнило об этом по-своему: где-то синхронизация была, где-то нет,
а где-то дата писалась в базу даже после отказа панели.

Поэтому синхронизация здесь одна на всех, и правило простое: сначала панель,
и только если она согласилась — база. Иначе в базе стоит дата, которой в
панели нет, и человек видит один срок, а доступ теряет в другой.
"""

from __future__ import annotations

import logging

from app.core.time import parse_dt

log = logging.getLogger(__name__)

# Насколько дата в панели и в базе может разойтись, оставаясь «той же».
# Секунды набегают на округлении при сериализации, и гонять из-за них
# запросы в панель незачем.
TOLERANCE_SEC = 60


async def sync_expiry(users, panel, user_id: int, expire_at, vpn: dict | None = None,
                      status: str | None = None) -> str:
    """Выровнять ByPass по основной подписке.

    Возвращает, что произошло: no_bypass | no_date | same | synced | panel_error.
    Ошибку панели не поднимаем: ByPass — дополнение, и ронять из-за него
    продление или выдачу сервера нельзя. Расхождение останется видимым и
    чинится следующим проходом.

    `status` нужен там, где основную подписку включают заново (повторный
    бесплатный период): выключенный ByPass с правильной датой всё равно не
    работает.
    """
    expire_at = parse_dt(expire_at)
    if not expire_at:
        return 'no_date'

    if vpn is None:
        user = await users.get(user_id, {'vpn.bypass_uuid': 1, 'vpn.bypass_expireAt': 1})
        vpn = (user or {}).get('vpn') or {}

    uuid = vpn.get('bypass_uuid')
    if not uuid:
        return 'no_bypass'

    current = parse_dt(vpn.get('bypass_expireAt'))
    if current and not status and abs(
            (current - expire_at).total_seconds()) <= TOLERANCE_SEC:
        return 'same'

    try:
        if status:
            await panel.update_subscription(uuid, expire_at=expire_at, status=status)
        else:
            await panel.update_subscription(uuid, expire_at=expire_at)
    except Exception as exc:
        log.warning('ByPass %s не выровнен по основной подписке: %s', user_id, exc)
        return 'panel_error'

    await users.set_vpn(user_id, {'bypass_expireAt': expire_at})
    log.info('ByPass %s выровнен: %s → %s', user_id, current, expire_at)
    return 'synced'


async def find_drift(users, limit: int = 0) -> list[dict]:
    """У кого даты разъехались. Сравниваем в питоне, а не запросом.

    В базе даты лежат то datetime, то строкой (наследство старого бота), и
    запрос с $expr на таких полях сравнивает типы, а не моменты времени.

    Идём курсором по всем, у кого есть ByPass. Раньше здесь стоял
    `to_list(length=1000)`, и это было хуже, чем кажется: Mongo отдаёт одну
    и ту же первую тысячу, поэтому все, кто дальше по коллекции, не
    проверялись никогда — сверка «отработала без расхождений», а у людей
    ByPass отключался посреди оплаченного месяца.

    limit — только предохранитель на случай, если проверять окажется нечего
    (0 — без ограничения).
    """
    drift = []
    async for row in users.iterate(
        {'vpn.bypass_uuid': {'$nin': ['', None]}},
        {'user_data.user_id': 1, 'vpn.expireAt': 1,
         'vpn.bypass_expireAt': 1, 'vpn.bypass_uuid': 1},
    ):
        vpn = row.get('vpn') or {}
        main = parse_dt(vpn.get('expireAt'))
        theirs = parse_dt(vpn.get('bypass_expireAt'))
        if not main:
            continue
        if theirs and abs((theirs - main).total_seconds()) <= TOLERANCE_SEC:
            continue
        drift.append({'user_id': (row.get('user_data') or {}).get('user_id'),
                      'main': main, 'bypass': theirs})
        if limit and len(drift) >= limit:
            break
    return drift


async def repair(users, panel, apply: bool = False, limit: int = 0) -> dict:
    """Починить расхождения. Без apply — только посчитать.

    Разделение нарочное: правка трогает панель по одному запросу на человека,
    и запускать её вслепую, не увидев масштаба, не стоит.
    """
    drift = await find_drift(users, limit)
    report = {'checked': len(drift), 'fixed': 0, 'failed': 0, 'rows': drift[:20]}
    if not apply:
        return report

    for row in drift:
        result = await sync_expiry(users, panel, row['user_id'], row['main'])
        if result == 'synced':
            report['fixed'] += 1
        elif result == 'panel_error':
            report['failed'] += 1
    return report
