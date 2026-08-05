"""Универсальный CRUD для списочных сущностей админки.

Тарифы, быстрые ответы поддержки и промокоды в текущем коде редактируются
тремя почти одинаковыми наборами хендлеров. Здесь описание сущности — это
данные, а хендлеры (в admin/panel.py) написаны один раз на все сущности.

Чтобы добавить новую сущность в админку, допишите EntityAdmin(...) в ENTITIES.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from app.core import db as db_names

from app.settings.schema import Setting


@dataclass(frozen=True)
class EntityAdmin:
    code: str                       # префикс в callback_data, короткий
    title: str                      # заголовок раздела
    collection: Any                 # motor-коллекция
    id_field: str                   # уникальное поле-идентификатор
    fields: tuple[Setting, ...]     # редактируемые поля (key = имя поля в документе)
    label: Callable[[dict], str]    # подпись кнопки в списке
    sort_field: str = 'order'
    sort_dir: int = 1
    toggle_field: str | None = 'enabled'
    allow_create: bool = True
    allow_delete: bool = True
    id_hint: str = 'Отправьте уникальный код (латиница, цифры, «-», «_»).'
    on_change: Callable[[], None] | None = None
    defaults: dict[str, Any] = field(default_factory=dict)

    def field_by_key(self, key: str) -> Setting | None:
        return next((f for f in self.fields if f.key == key), None)

    # ── операции ────────────────────────────────────────────────────────────
    async def list(self) -> list[dict]:
        return await self.collection.find({}, {'_id': 0}).sort(
            self.sort_field, self.sort_dir).to_list(length=200)

    async def get(self, item_id: str) -> dict | None:
        return await self.collection.find_one({self.id_field: item_id}, {'_id': 0})

    async def set_field(self, item_id: str, key: str, value: Any) -> None:
        await self.collection.update_one(
            {self.id_field: item_id},
            {'$set': {key: value, 'updated_at': datetime.now()}},
        )
        self._changed()

    async def toggle(self, item_id: str) -> bool:
        item = await self.get(item_id) or {}
        new_value = not item.get(self.toggle_field, True)
        await self.set_field(item_id, self.toggle_field, new_value)
        return new_value

    async def create(self, item_id: str) -> dict:
        doc = {self.id_field: item_id, 'created_at': datetime.now()}
        for f in self.fields:
            doc.setdefault(f.key, f.default)
        doc.update(self.defaults)
        doc[self.id_field] = item_id
        if self.toggle_field:
            doc.setdefault(self.toggle_field, True)
        if self.sort_field not in doc:
            doc[self.sort_field] = (await self.collection.count_documents({}) + 1) * 10
        await self.collection.insert_one(doc)
        self._changed()
        return doc

    async def delete(self, item_id: str) -> None:
        await self.collection.delete_one({self.id_field: item_id})
        self._changed()

    async def move(self, item_id: str, direction: int) -> bool:
        """Сдвиг в списке: направление -1 (вверх) или +1 (вниз)."""
        items = await self.list()
        index = next((i for i, x in enumerate(items) if x.get(self.id_field) == item_id), None)
        if index is None:
            return False
        target = index + direction
        if target < 0 or target >= len(items):
            return False

        a, b = items[index], items[target]
        # значения порядка берём до записи: после первого update словарь a
        # может оказаться уже обновлённым, и обмен превратится в присваивание
        order_a = a.get(self.sort_field, 0)
        order_b = b.get(self.sort_field, 0)
        await self.collection.update_one({self.id_field: a[self.id_field]},
                                         {'$set': {self.sort_field: order_b}})
        await self.collection.update_one({self.id_field: b[self.id_field]},
                                         {'$set': {self.sort_field: order_a}})
        self._changed()
        return True

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()


# ─────────────────────────────────────────────────────────────────────────────
# Регистр сущностей: собирается из контейнера, чтобы коллекции приходили извне
# ─────────────────────────────────────────────────────────────────────────────
PLAN_FIELDS = (
    Setting('title', 'Название', 'str', 'Новый тариф'),
    Setting('price', 'Цена', 'int', 100, unit='₽', min=0),
    Setting('days', 'Длительность', 'int', 30, unit=' дн.', min=1),
    Setting('gift_type', 'Тип подарка', 'str', '', hint='1day / 1month, пусто — без подарка'),
    Setting('gift_count', 'Кол-во подарков', 'int', 0, min=0),
    Setting('order', 'Порядок', 'int', 100),
)

QUICK_REPLY_FIELDS = (
    Setting('title', 'Заголовок', 'str', 'Новый ответ'),
    Setting('text', 'Текст ответа', 'text', 'Текст'),
    Setting('order', 'Порядок', 'int', 100),
)

PROMO_FIELDS = (
    Setting('reward_type', 'Тип награды', 'str', 'balance', hint='balance или traffic_gb'),
    Setting('reward_value', 'Размер награды', 'int', 100, min=1),
    Setting('max_uses', 'Лимит активаций', 'int', 0, min=0, hint='0 — без лимита'),
)

TEXT_FIELDS = (
    Setting('value', 'Текст', 'text', ''),
)


def build_entities(container) -> dict[str, EntityAdmin]:
    """Что редактируется списками в админке. Добавить раздел = добавить объект."""
    entities = (
        EntityAdmin(
            code='plan', title='💰 Тарифы',
            collection=container.plans.col, id_field='code', fields=PLAN_FIELDS,
            label=lambda p: f'{p.get("title", "?")} — {p.get("price", 0)}₽ / {p.get("days", 0)} дн.',
            on_change=container.plans.invalidate,
        ),
        EntityAdmin(
            code='qr', title='🧩 Быстрые ответы поддержки',
            collection=container.collection(db_names.QUICK_REPLIES), id_field='key',
            fields=QUICK_REPLY_FIELDS, label=lambda x: x.get('title', '?'),
            toggle_field='active',
            id_hint='Отправьте ключ ответа (например: how_to_connect).',
        ),
        EntityAdmin(
            code='promo', title='🎁 Промокоды',
            collection=container.collection(db_names.PROMO_CODES), id_field='code',
            fields=PROMO_FIELDS,
            label=lambda p: (f'{p.get("code")} · {p.get("reward_value")} '
                             f'{"₽" if p.get("reward_type") == "balance" else "Гб"} '
                             f'({p.get("used_count", 0)}/{p.get("max_uses") or "∞"})'),
            sort_field='created_at', sort_dir=-1, toggle_field='is_active',
            id_hint='Отправьте код промокода (например: WELCOME100).',
            defaults={'used_count': 0, 'expires_at': None},
        ),
        EntityAdmin(
            code='text', title='📝 Тексты бота',
            collection=container.db[db_names.CONTENT_OVERRIDES], id_field='_id',
            fields=TEXT_FIELDS, label=lambda x: x.get('title') or x.get('_id', '?'),
            sort_field='_id', toggle_field=None, allow_create=False, allow_delete=True,
            on_change=container.reload_texts,
        ),
    )
    return {e.code: e for e in entities}
