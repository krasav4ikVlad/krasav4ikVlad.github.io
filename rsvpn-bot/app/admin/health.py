"""Отметки «что и когда отработало» — для команды /diag.

Вопрос «работают ли сейчас напоминания и автопродление» не имеет ответа в
коде: одно живёт в планировщике бота, другое приходит вебхуком от панели, и
проверить оба разом можно только по факту работы. Логи это показывают, но
за ними надо идти на сервер, а с телефона это неудобно.

Здесь по документу на задачу: когда отработала и с каким результатом.
Пишется из тонких мест — обёртки задачи и маршрута вебхука, — поэтому ни
один сервис про эти отметки не знает и не усложняется ими.

Отсутствие отметки — тоже ответ, и самый важный: «панель ни разу не
позвала» выглядит именно так.
"""

from __future__ import annotations

import logging

from app.core.time import now

log = logging.getLogger(__name__)

RENEWAL = 'renewal'
DEVICES = 'devices'
PANEL_WEBHOOK = 'panel_webhook'
EXPIRY_SENT = 'expiry_sent'
CAMPAIGNS = 'campaigns'

TITLES = {
    RENEWAL: 'Автопродление',
    DEVICES: 'Плата за устройства',
    PANEL_WEBHOOK: 'Вебхук панели',
    EXPIRY_SENT: 'Напоминание отправлено',
    CAMPAIGNS: 'Кампании',
}


class HealthLog:
    def __init__(self, collection):
        self.col = collection

    async def mark(self, key: str, **info) -> None:
        """Запомнить, что задача отработала. Отказ базы не должен её ронять."""
        if self.col is None:
            return
        try:
            await self.col.update_one(
                {'_id': key},
                {'$set': {'at': now(), 'info': info}, '$inc': {'runs': 1}},
                upsert=True,
            )
        except Exception as exc:
            log.debug('отметка %s не записана: %s', key, exc)

    async def read(self) -> dict[str, dict]:
        if self.col is None:
            return {}
        try:
            docs = await self.col.find({}).to_list(length=None)
        except Exception as exc:
            log.warning('отметки не прочитаны: %s', exc)
            return {}
        return {doc['_id']: doc for doc in docs if doc.get('_id')}
