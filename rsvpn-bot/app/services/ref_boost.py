"""Повышенный процент рефоводу — чей он и на кого действует.

Ставка лежит на метке (`/reftag`, раздел «Партнёры»), но принадлежит она
человеку. Акция объявляется ему — «эту неделю у тебя 50 вместо 30», — и
по-человечески это значит: неделю все его пополняющие приносят по 50%.
Все: и пришедшие по этой метке, и по другой его метке, и по обычной
числовой ссылке, и приведённые год назад.

Поэтому ставка ищется по ПРИГЛАСИВШЕМУ, а не по метке в карточке
приведённого: деньги идут пригласившему, значит и процент его.

Кончился срок — процент сам вернулся к обычному: ставка нигде не
записывается ни в карточку приведённого, ни в настройки, а считается
в момент оплаты. Уже начисленное при этом не пересчитывается — оно
посчиталось тогда, по той ставке, которая действовала.

Одно место на весь бот: по этой же ставке считаются деньги при пополнении
и она же показывается человеку на экране рефералки. Разъехаться им нельзя —
«обещали 50, начислили 30» это худшее, что может случиться с партнёром.
"""

from __future__ import annotations

import logging

from app.core.time import now, parse_dt
from app.domain import ref_tags as domain

log = logging.getLogger(__name__)


async def active(ref_tags, user_id) -> dict | None:
    """Действующая акция человека или None. Метка — с её ставкой и сроком."""
    if ref_tags is None or not user_id:
        return None
    try:
        rows = await ref_tags.of_user(int(user_id))
    except Exception as exc:      # noqa: BLE001 — акция не должна ломать оплату
        log.warning('акции пригласившего %s не прочитаны: %s', user_id, exc)
        return None
    return domain.best_boost(rows, now())


async def offer(ref_tags, user_id, *, base: float) -> dict:
    """Ставка этого человека сейчас — и всё, что о ней нужно сказать.

    Акция ниже обычного процента игнорируется: снизить процент отдельному
    человеку через неё нельзя — это не то, зачем она есть, и молчаливое
    «у тебя стало хуже» никому не нужно.
    """
    partner = await active(ref_tags, user_id)
    boost = domain.boost_rate(partner or {}, now())
    if boost <= base:
        return {'rate': base, 'base': base, 'boost': False, 'until': None}

    log.info('пригласивший %s: повышенный процент %s вместо %s',
             user_id, boost, base)
    return {'rate': boost, 'base': base, 'boost': True,
            'until': parse_dt((partner or {}).get('boost_until'))}


async def rate_for(ref_tags, user_id, *, base: float) -> float:
    """Процент, по которому считать начисление этому пригласившему."""
    return (await offer(ref_tags, user_id, base=base))['rate']
