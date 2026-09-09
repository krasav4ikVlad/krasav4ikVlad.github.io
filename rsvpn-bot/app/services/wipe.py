"""Полное удаление пользователя — для тестовых аккаунтов.

Нужно ровно одному сценарию: проверить бота глазами новичка. Твинк проходит
регистрацию, триал, покупку — и, чтобы пройти это ещё раз, должен исчезнуть
без следа.

Двух вещей тут мало.

Первая — панель. Удалить документ из Mongo недостаточно: shortUuid считается
от user_id, поэтому при повторной регистрации панель ответит «User short UUID
already exists», и подписка не создастся. Ровно этот отказ уже ловили при
продлении. Поэтому сначала панель, потом база.

Вторая — хвосты в других коллекциях. Идемпотентность платежей, использованные
промокоды, выданные подарки, отпечатки сквадов: без их чистки твинк вернётся
«новым», но промокод повторно не активирует, а прежний подарок будет числиться
за ним. Это не гипотеза — на этом и ломается повторное тестирование.

Пользовательских данных это не бережёт и беречь не должно: команда нужна
только для тестов, поэтому в админке она защищена подтверждением, а
администратора удалить нельзя вовсе.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core import db as names

log = logging.getLogger(__name__)


@dataclass
class WipeResult:
    ok: bool
    user_id: int = 0
    panel_deleted: int = 0        # сколько подписок убрано из панели
    panel_failed: int = 0
    removed: dict[str, int] = field(default_factory=dict)   # коллекция → сколько

    @property
    def total(self) -> int:
        return sum(self.removed.values())


class WipeService:
    """Удаление пользователя из панели и из всех коллекций бота."""

    # Коллекция → поля, в которых лежит идентификатор человека. Список
    # именно здесь, а не по коду: забытая коллекция означает «твинк вроде
    # удалён, а ведёт себя как старый», и искать это потом дорого.
    TRACES: tuple[tuple[str, tuple[str, ...]], ...] = (
        (names.PAYMENTS, ('user_id',)),
        (names.PAYMENT_WEBHOOKS, ('user_id',)),
        (names.GIFTS, ('from_user_id', 'to_user_id')),
        (names.PROMO_USAGES, ('user_id',)),
        (names.CHURN_SURVEYS, ('user_id',)),
        (names.FINGERPRINTS, ('user_id',)),
        (names.CARDLINK_BILLS, ('user_id',)),
        (names.SUBSCRIPTIONS, ('user_id',)),
    )

    def __init__(self, users, db, vpn=None, legacy: bool = False):
        self.users = users
        self.db = db
        self.vpn = vpn
        self.legacy = legacy

    async def preview(self, user_id: int) -> dict[str, int]:
        """Что будет удалено. Ничего не меняет — для экрана подтверждения."""
        found: dict[str, int] = {}
        for name, fields in self.TRACES:
            count = await self._count(name, fields, user_id)
            if count:
                found[name] = count
        return found

    async def wipe(self, user_id: int) -> WipeResult:
        user = await self.users.get(user_id)
        if not user:
            return WipeResult(False, user_id)

        result = WipeResult(True, user_id)

        # 1. Панель — первой. Если база уже пуста, а панель нет, uuid'ы
        #    потеряны, и удалять оттуда придётся руками.
        for uuid in self._panel_uuids(user):
            if await self._delete_from_panel(uuid):
                result.panel_deleted += 1
            else:
                result.panel_failed += 1

        # 2. Хвосты в остальных коллекциях
        for name, fields in self.TRACES:
            removed = await self._delete(name, fields, user_id)
            if removed:
                result.removed[name] = removed

        # 3. Сам пользователь — последним: пока он на месте, операцию можно
        #    повторить и дочистить то, что не удалилось.
        deleted = await self.users.col.delete_one({'user_data.user_id': user_id})
        result.removed[names.USERS] = int(getattr(deleted, 'deleted_count', 0) or 0)

        log.warning('пользователь %s удалён: панель=%s, документов=%s',
                    user_id, result.panel_deleted, result.total)
        return result

    # ── внутреннее ──────────────────────────────────────────────────────────
    @staticmethod
    def _panel_uuids(user: dict) -> list[str]:
        vpn = user.get('vpn') or {}
        return [uuid for uuid in (vpn.get('uuid'), vpn.get('bypass_uuid')) if uuid]

    async def _delete_from_panel(self, uuid: str) -> bool:
        if self.vpn is None:
            return False
        try:
            return bool(await self.vpn.delete_subscription(uuid))
        except Exception as exc:      # панель недоступна — база всё равно чистится
            log.warning('подписка %s не удалена из панели: %s', uuid, exc)
            return False

    def _collection(self, name: str):
        return self.db[names.collection_name(name, self.legacy)]

    @staticmethod
    def _query(fields: tuple[str, ...], user_id: int) -> dict:
        if len(fields) == 1:
            return {fields[0]: user_id}
        return {'$or': [{field: user_id} for field in fields]}

    async def _count(self, name: str, fields: tuple[str, ...], user_id: int) -> int:
        try:
            return await self._collection(name).count_documents(
                self._query(fields, user_id))
        except Exception:      # коллекции может не быть вовсе — это не ошибка
            return 0

    async def _delete(self, name: str, fields: tuple[str, ...], user_id: int) -> int:
        try:
            result = await self._collection(name).delete_many(
                self._query(fields, user_id))
        except Exception as exc:
            log.warning('%s: не удалось почистить %s: %s', name, user_id, exc)
            return 0
        return int(getattr(result, 'deleted_count', 0) or 0)
