"""Сверка лимита устройств: пакеты → база бота → панель.

Чисел здесь три, и разойтись могут любые два.

  * **Пакеты.** Сколько человек оплатил: бесплатный лимит плюс активные
    пакеты доп. устройств. Это единственная правда — она выводится из денег.
  * **База бота.** `vpn.hwidDeviceLimit` — то, что бот показывает на экране
    и считает при покупке следующего устройства.
  * **Панель.** `hwidDeviceLimit` подписки — то, сколько устройств у человека
    реально подключится.

Расходятся они молча. Бот меняет лимит в панели и только потом пишет к себе;
если панель в этот момент отказала, у человека остаётся старое число — на
экране одно, в жизни другое. Обратный случай тоже бывает: лимит правили
руками в панели, и бот об этом не знает.

Поэтому сверка разделена надвое. Внутренняя — пакеты против базы — стоит
одного прохода по коллекции и ничего не спрашивает у панели. Сверка с
панелью стоит запроса на человека, поэтому её запускают отдельно и с
ограничением.
"""

from __future__ import annotations

import logging

from app.core.errors import VpnPanelError
from app.services import bypass

log = logging.getLogger(__name__)

FIELDS = {'user_data.user_id': 1, 'vpn.uuid': 1, 'vpn.hwidDeviceLimit': 1,
          'vpn.extraDevices': 1, 'vpn.bypass_hwidDeviceLimit': 1}


def paid_limit(vpn: dict, base: int) -> int:
    """Сколько устройств человек оплатил: база плюс активные пакеты.

    Считается так же, как при покупке и при отключении пакета
    (DeviceBillingService), — иначе сверка чинила бы на своё число, а
    следующая покупка возвращала бы всё обратно.
    """
    extra = sum(int(item.get('amount', 0) or 0)
                for item in (vpn.get('extraDevices') or [])
                if item.get('active', True))
    return int(base) + extra


async def find_drift(users, base: int, limit: int = 0) -> list[dict]:
    """У кого база бота разошлась с оплаченным. Панель не спрашиваем.

    Курсором по всем, у кого есть подписка: расхождение бывает и у тех, кто
    доп. устройств не покупал — например, если лимит правили руками.
    """
    rows = []
    async for row in users.iterate({'vpn.uuid': {'$nin': ['', None]}}, FIELDS):
        vpn = row.get('vpn') or {}
        stored = int(vpn.get('hwidDeviceLimit') or 0)
        paid = paid_limit(vpn, base)
        if stored == paid:
            continue
        rows.append({'user_id': (row.get('user_data') or {}).get('user_id'),
                     'ref': vpn.get('uuid'), 'paid': paid, 'stored': stored,
                     'panel': None, 'where': 'bot'})
        if limit and len(rows) >= limit:
            break
    return rows


async def check_panel(users, panel, base: int, limit: int = 300) -> dict:
    """Спросить у панели настоящий лимит. Запрос на человека — отсюда предел.

    Проверяются все с подпиской, а не только те, у кого разошлись пакеты:
    самый неприятный случай — когда бот и пакеты согласны между собой, а
    панель молча осталась со старым числом.
    """
    report: dict = {'checked': 0, 'drift': [], 'failed': 0, 'stopped': False}

    async for row in users.iterate({'vpn.uuid': {'$nin': ['', None]}}, FIELDS):
        if limit and report['checked'] >= limit:
            report['stopped'] = True
            break

        vpn = row.get('vpn') or {}
        ref = vpn.get('uuid')
        user_id = (row.get('user_data') or {}).get('user_id')
        report['checked'] += 1

        try:
            found = await panel.get_subscription(ref)
        except VpnPanelError as exc:
            log.info('лимит устройств %s не проверен: %s', user_id, exc)
            report['failed'] += 1
            continue

        theirs = found.get('hwidDeviceLimit')
        if theirs is None:
            # Панель не вернула поле — сравнивать не с чем, и считать это
            # совпадением нельзя: так расхождение и прячется.
            report['failed'] += 1
            continue

        stored = int(vpn.get('hwidDeviceLimit') or 0)
        paid = paid_limit(vpn, base)
        if int(theirs) == paid == stored:
            continue

        report['drift'].append({
            'user_id': user_id, 'ref': ref, 'paid': paid, 'stored': stored,
            'panel': int(theirs),
            'where': 'panel' if stored == paid else 'both',
        })
    return report


async def repair(users, panel, rows: list[dict], apply: bool = False) -> dict:
    """Привести всё к оплаченному: сначала панель, потом база.

    Порядок тот же, что при покупке устройства: панель отказала — документ
    не трогаем. Иначе бот показывал бы новое число, которого в панели нет,
    а это ровно то расхождение, которое мы и чиним.
    """
    report = {'fixed': 0, 'failed': 0, 'total': len(rows)}
    if not apply:
        return report

    for row in rows:
        target = int(row['paid'])
        ref = row.get('ref')
        if ref:
            try:
                await panel.update_subscription(ref, device_limit=target)
            except Exception as exc:
                log.warning('лимит устройств %s не выставлен: %s',
                            row.get('user_id'), exc)
                report['failed'] += 1
                continue

        await users.set_vpn(row['user_id'], {'hwidDeviceLimit': target})
        # У ByPass свой лимит и своя запись в панели — без этого он остался
        # бы со старым числом, и расхождение просто переехало бы туда.
        await bypass.sync_devices(users, panel, row['user_id'], target)
        report['fixed'] += 1

    return report


async def one(users, panel, base: int, user_id: int) -> dict | None:
    """Все три числа по одному человеку — ответ на «почему у него другое»."""
    row = await users.get(user_id, FIELDS)
    if not row:
        return None

    vpn = row.get('vpn') or {}
    card: dict = {
        'user_id': user_id,
        'ref': vpn.get('uuid'),
        'base': int(base),
        'paid': paid_limit(vpn, base),
        'stored': int(vpn.get('hwidDeviceLimit') or 0),
        'bypass': vpn.get('bypass_hwidDeviceLimit'),
        'packages': [item for item in (vpn.get('extraDevices') or [])],
        'panel': None,
        'panel_error': '',
    }

    if not card['ref']:
        card['panel_error'] = 'подписки в панели нет'
        return card

    try:
        found = await panel.get_subscription(card['ref'])
        card['panel'] = found.get('hwidDeviceLimit')
        if card['panel'] is None:
            card['panel_error'] = 'панель не вернула лимит'
    except Exception as exc:
        card['panel_error'] = str(exc)
    return card
