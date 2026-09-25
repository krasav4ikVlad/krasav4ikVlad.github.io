"""Показ идентификаторов: целиком или под звёздочками.

Нужно ровно для одного случая — когда экран бота снимают на видео или
кладут скриншотом в канал. Идентификатор Telegram сам по себе не секрет,
но показывать чужие на публику незачем, а свой на записи с подведением
итогов видно крупным планом.

Поэтому не «убрать id из интерфейса» (он нужен: по нему ищут человека,
им же начисляют призы), а режим показа. Включается на время съёмки и
выключается обратно — командой `/mask`.

Режим живёт в ContextVar по образцу «обычных значков» (app/content/emoji):
он читается из настроек один раз за апдейт и не тащится параметром через
десяток функций, которые собирают текст.

В выгрузки (Excel, CSV) маска не идёт нарочно: файлы — рабочий инструмент,
из них копируют id для начислений, и звёздочки там означали бы «файл
сломан».
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

# Сколько цифр оставить видимыми с каждого края: 7095687 → 70***87.
# Двух хватает, чтобы свой id узнать глазами, и мало, чтобы чужой набрать.
KEEP = 2
STAR = '*'

_masked: ContextVar[bool] = ContextVar('mask_ids', default=False)


def mask(value) -> str:
    """7095687 → 70***87. Короткое — звёздочками целиком."""
    raw = str(value or '').strip()
    if not raw:
        return ''
    if len(raw) <= KEEP * 2:
        return STAR * len(raw)
    return raw[:KEEP] + STAR * (len(raw) - KEEP * 2) + raw[-KEEP:]


def show(value) -> str:
    """Идентификатор так, как его сейчас положено показывать."""
    raw = str(value or '').strip()
    return mask(raw) if _masked.get() and raw else raw


def hidden() -> bool:
    return _masked.get()


def set_hidden(value: bool) -> None:
    """Проставляется раз за апдейт — из настройки privacy.mask_ids."""
    _masked.set(bool(value))


@contextmanager
def visible():
    """Внутри блока id показываются целиком, чем бы ни был режим.

    Нужно выгрузкам и всему, откуда id копируют для работы: маска там —
    это не приватность, а испорченный файл.
    """
    token = _masked.set(False)
    try:
        yield
    finally:
        _masked.reset(token)
