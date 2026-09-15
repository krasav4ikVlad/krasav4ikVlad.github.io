"""Переезд на числовые идентификаторы панели 3.x.

До Remnawave 3.0 пользователь панели опознавался по `uuid`. С 3.0 его
опознаёт числовой `id`, а поле `uuid` из ответов убрано совсем. Пути
остались той же формы — `/api/users/<чем-опознаём>`, — поэтому клиент
работает с обеими версиями сразу и просто передаёт то, что лежит в
`vpn.uuid`.

Проблема в другом: у всех, кто купил подписку до обновления, там лежит
именно uuid, а панель 3.x его больше не понимает. Такой человек не может
ни продлить, ни докупить устройства, ни быть отключённым — каждый запрос
про него панель отвергает. Сам по себе это не чинится: обратного поиска
по uuid в 3.x нет.

Чинится это через shortUuid. Он считается от telegram id, ни от какой
версии панели не зависит и — прямо сказано в примечаниях к выпуску —
ручка `/api/users/by-short-uuid/{shortUuid}` не менялась. То есть по
каждому человеку можно спросить панель заново и записать себе новый
идентификатор.

Проверка версии стоит одним запросом: берём первого попавшегося человека
и смотрим, чем его назвала панель. Пока панель отвечает uuid — переезжать
некуда, и задача выходит, ничего не тронув. Поэтому её можно держать
включённой всё время и не вспоминать про неё в день обновления.
"""

from __future__ import annotations

import logging

from app.integrations.vpn.remnawave import (is_numeric_ref, subscription_token,
                                            user_ref)

log = logging.getLogger(__name__)

# Пары «поле с идентификатором → поле с коротким идентификатором» и признак
# ByPass: у него своя запись в панели и свой shortUuid.
PAIRS = (('uuid', 'shortUuid', False), ('bypass_uuid', 'bypass_shortUuid', True))

FIELDS = {'user_data.user_id': 1, 'vpn.uuid': 1, 'vpn.shortUuid': 1,
          'vpn.bypass_uuid': 1, 'vpn.bypass_shortUuid': 1}


def refs_of(vpn: dict, user_id: int = 0) -> list[tuple[str, str, object]]:
    """Все ссылки на панель у человека: [(поле, короткий id, значение)].

    Короткий идентификатор берём из базы, а если его там нет — считаем сами.
    Он выводится из telegram id и всегда один и тот же, поэтому подписки,
    приехавшие из старого бота без этого поля, тоже переедут, а не останутся
    навсегда неуправляемыми.
    """
    rows = []
    for ref_field, short_field, bypass in PAIRS:
        ref = vpn.get(ref_field)
        if not ref:
            continue
        short = vpn.get(short_field) or (
            subscription_token(user_id, bypass=bypass) if user_id else '')
        if short:
            rows.append((ref_field, short, ref))
    return rows


def stale_refs(vpn: dict, user_id: int = 0,
               numeric: bool = True) -> list[tuple[str, str, object]]:
    """Ссылки не того вида, который понимает панель.

    numeric=True — панель 3.x, ей нужны числа, и переводим всё, что ещё
    uuid. numeric=False — панель откатили на 2.x, и переводить надо ровно
    наоборот. Второе направление не прихоть: без него обновление панели
    становится дверью без ручки с той стороны.
    """
    return [row for row in refs_of(vpn, user_id)
            if is_numeric_ref(row[2]) is not numeric]


async def panel_is_new(panel, short_uuid: str) -> bool | None:
    """Панель уже на числовых идентификаторах? None — не смогли выяснить.

    Один запрос на всю задачу. Разница между «панель старая» и «панель не
    ответила» важна: в первом случае может не оказаться работы, во втором
    нельзя делать вид, что её нет.
    """
    try:
        found = await panel.find_by_short_uuid(short_uuid)
    except Exception as exc:
        log.warning('версию панели выяснить не удалось: %s', exc)
        return None
    if not found:
        return None
    return is_numeric_ref(user_ref(found))


async def _any_ref(users) -> tuple[str, int]:
    """Первая попавшаяся ссылка — по ней спрашиваем версию панели."""
    async for row in users.iterate({'vpn.uuid': {'$nin': ['', None]}}, FIELDS):
        user_id = (row.get('user_data') or {}).get('user_id')
        for _, short, _ in refs_of(row.get('vpn') or {}, user_id):
            return short, user_id
    return '', 0


async def find_stale(users, numeric: bool = True, limit: int = 0) -> list[dict]:
    """Кого ещё не перевели. Курсором по всем — не первой тысячей."""
    rows = []
    async for row in users.iterate({'vpn.uuid': {'$nin': ['', None]}}, FIELDS):
        user_id = (row.get('user_data') or {}).get('user_id')
        pending = stale_refs(row.get('vpn') or {}, user_id, numeric)
        if not pending:
            continue
        rows.append({'user_id': user_id, 'pending': pending})
        if limit and len(rows) >= limit:
            break
    return rows


async def migrate(users, panel, apply: bool = False, limit: int = 0) -> dict:
    """Привести ссылки на панель к тому виду, который она понимает.

    Сначала спрашиваем панель, чем она называет пользователя, и только
    потом ищем несовпадающих. Раньше порядок был обратным — искали «ещё не
    переведённых», — и после отката панели на 2.x работы не находилось
    вовсе: переведённые числа считались нормой, а панель их уже не знала.

    Без apply — только посчитать: переезд трогает панель одним запросом на
    подписку, и запускать его вслепую, не увидев масштаба, не стоит.
    """
    report: dict = {'stale': 0, 'moved': 0, 'failed': 0, 'panel': '',
                    'direction': '', 'rows': []}

    sample, _ = await _any_ref(users)
    if not sample:
        report['panel'] = 'nothing'
        return report

    numeric = await panel_is_new(panel, sample)
    if numeric is None:
        report['panel'] = 'unknown'
        return report

    stale = await find_stale(users, numeric, limit)
    report['stale'] = len(stale)
    report['panel'] = 'new' if numeric else 'old'
    report['direction'] = 'to_id' if numeric else 'to_uuid'
    if not stale:
        report['panel'] = 'nothing'
        return report
    if not apply:
        report['rows'] = stale[:20]
        return report

    for row in stale:
        fields = {}
        for ref_field, short, old in row['pending']:
            try:
                found = await panel.find_by_short_uuid(short)
            except Exception as exc:
                log.warning('не смогли найти в панели %s: %s', short, exc)
                report['failed'] += 1
                continue
            ref = user_ref(found)
            if ref in (None, '') or is_numeric_ref(ref) is not numeric:
                # Панель ответила не тем видом, которого мы ждали, — значит
                # версию выясняли зря. Записать это молча хуже, чем сдаться.
                report['failed'] += 1
                continue
            fields[ref_field] = int(ref) if numeric else str(ref)

        if not fields:
            continue
        await users.set_vpn(row['user_id'], fields)
        report['moved'] += 1
        log.info('пользователь %s: ссылки на панель обновлены %s',
                 row['user_id'], fields)

    return report
