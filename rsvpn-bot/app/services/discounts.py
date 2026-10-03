"""Скидки по аудиториям: одно место, где решается цена тарифа для человека.

Правило простое: скидка привязана не к пользователю, а к аудитории, в
которой он сейчас находится (`growth.segment` → аудитория). Ушёл из неё —
скидка сама перестала действовать, ничего сбрасывать вручную не нужно.

Аудиторий может подойти сразу две: «Всем 10%» и «Истёкшим 30%». Берём
**большую**. Складывать проценты нельзя (две акции по 60% дали бы цену
ниже нуля), а брать меньшую — значит наказать человека за то, что он попал
под вторую акцию.

Скидка касается только цены тарифа. Плата за доп. устройства не уценивается:
это отдельный тридцатидневный цикл со своей ценой за штуку, и скидка там
разъехалась бы с тем, что человеку уже насчитали в пакетах.

Почему сервис, а не функция в domain/: нужно читать настройки. Сама
арифметика по-прежнему чистая — domain/pricing.discounted().
"""

from __future__ import annotations

from app.domain.pricing import discounted
from app.domain.segments import AUDIENCES, audiences_of


class DiscountService:
    def __init__(self, settings):
        self.settings = settings

    async def rate(self, user: dict | None) -> float:
        """Доля скидки для пользователя: 0.2 — это 20%."""
        segment = str(((user or {}).get('growth') or {}).get('segment', '') or '')
        rates = [await self.settings.rate(f'discount.{code}')
                 for code in audiences_of(segment) if code in AUDIENCES]
        return max([r for r in rates if r > 0], default=0.0)

    async def price(self, user: dict | None, plan: dict | None) -> int:
        """Цена тарифа для этого человека — то, что и списывается, и рисуется."""
        base = int((plan or {}).get('price', 0) or 0)
        return discounted(base, await self.rate(user))

    async def notice(self, user: dict | None) -> str:
        """Строка «Скидка 20% уже в цене» — или пустая, если скидки нет.

        Показывать обязательно: цена в кнопке иначе не сходится с ценой в
        разделе «Тарифы», и это выглядит как ошибка бота, а не как акция.
        """
        rate = await self.rate(user)
        if not rate:
            return ''
        template = str(await self.settings.get('discount.notice') or '')
        return template.replace('{percent}', f'{round(rate * 100)}%') if template else ''
