"""Заглушки Mongo и Telegram: тесты идут без сети и без БД.

Это не «моки ради моков» — так проверяются денежные сценарии (списание,
идемпотентность, повторная отправка кампании), которые иначе можно
проверить только в проде на живых пользователях.
"""

from __future__ import annotations

import copy
from datetime import datetime

import pytest

from app.core.config import Config, PaymentsConfig, VpnPanelConfig


class FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, field, direction=1):
        self._docs.sort(key=lambda d: (d.get(field) is None, d.get(field, 0)),
                        reverse=direction < 0)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs) if length is None else list(self._docs[:length])

    def __aiter__(self):
        self._it = iter(list(self._docs))
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class DuplicateKeyError(Exception):
    """Аналог pymongo.errors.DuplicateKeyError для заглушки."""


class MongoError(Exception):
    """Ошибка Mongo с кодом — как OperationFailure у pymongo."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class FakeResult:
    def __init__(self, matched=0, modified=0, upserted_id=None, deleted=0):
        self.matched_count = matched
        self.modified_count = modified
        self.upserted_id = upserted_id
        self.deleted_count = deleted


class FakeCollection:
    """Минимальный Mongo: точечные пути, $set/$inc/$push/$addToSet, $exists/$gte/$nin."""

    def __init__(self, name='fake'):
        self.name = name
        self.docs: list[dict] = []
        self._auto_id = 0
        self.unique_keys: list[tuple[str, ...]] = []
        self.indexes: dict[str, bool] = {}

    # ── чтение ──────────────────────────────────────────────────────────────
    @staticmethod
    def _get(doc, path):
        """Как Mongo: 'arr.0' — индекс, 'arr.field' — значения поля у всех элементов."""
        current = doc
        for part in path.split('.'):
            if isinstance(current, list):
                if part.isdigit():
                    index = int(part)
                    current = current[index] if index < len(current) else None
                else:
                    current = [x.get(part) for x in current if isinstance(x, dict)]
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    def _match(self, doc, query):
        for key, condition in query.items():
            if key == '$or':
                if not any(self._match(doc, sub) for sub in condition):
                    return False
                continue
            if key == '$and':
                if not all(self._match(doc, sub) for sub in condition):
                    return False
                continue
            value = doc.get('_id') if key == '_id' else self._get(doc, key)
            if isinstance(condition, dict):
                for op, expected in condition.items():
                    if op == '$exists' and (value is not None) != expected:
                        return False
                    if op == '$gte' and not (value is not None and value >= expected):
                        return False
                    if op == '$lte' and not (value is not None and value <= expected):
                        return False
                    if op == '$gt' and not (value is not None and value > expected):
                        return False
                    if op == '$lt' and not (value is not None and value < expected):
                        return False
                    if op == '$ne' and value == expected:
                        return False
                    if op == '$in' and value not in expected:
                        return False
                    if op == '$nin' and value in expected:
                        return False
                    if op == '$elemMatch':
                        if not isinstance(value, list):
                            return False
                        if self._elem_index(value, expected) is None:
                            return False
            elif isinstance(value, list) and not isinstance(condition, list):
                # равенство по полю массива: подходит, если совпал любой элемент
                if condition not in value:
                    return False
            elif value != condition:
                return False
        return True

    @staticmethod
    def _elem_index(items, conditions) -> int | None:
        """Индекс первого элемента, удовлетворяющего ВСЕМ условиям ($elemMatch)."""
        for i, item in enumerate(items):
            if isinstance(item, dict) and all(item.get(k) == v for k, v in conditions.items()):
                return i
        return None

    def _positional_index(self, doc, query, array_path):
        """Как настоящий Mongo выбирает элемент для оператора `$`.

        При $elemMatch — первый элемент, подходящий под все условия сразу.
        При отдельных условиях вида 'arr.field' — первый элемент, подошедший
        под ПЕРВОЕ такое условие; остальные условия Mongo проверяет по массиву
        целиком, поэтому они могут совпасть на других элементах. Именно на этом
        и ломался старый код списания за устройства.
        """
        items = self._get(doc, array_path) or []
        condition = query.get(array_path)
        if isinstance(condition, dict) and '$elemMatch' in condition:
            return self._elem_index(items, condition['$elemMatch'])

        prefix = f'{array_path}.'
        for key, expected in query.items():
            if key.startswith(prefix):
                field = key[len(prefix):]
                for i, item in enumerate(items):
                    if isinstance(item, dict) and item.get(field) == expected:
                        return i
        return None

    def find(self, query=None, projection=None):
        return FakeCursor([d for d in self.docs if self._match(d, query or {})])

    async def find_one(self, query, projection=None):
        return next((d for d in self.docs if self._match(d, query)), None)

    async def count_documents(self, query=None, limit=None):
        found = [d for d in self.docs if self._match(d, query or {})]
        return len(found[:limit] if limit else found)

    # ── запись ──────────────────────────────────────────────────────────────
    async def insert_one(self, doc):
        # уникальные индексы — не декорация: на них держится защита от
        # повторной активации промокода
        for keys in self.unique_keys:
            probe = {k: self._get(doc, k) for k in keys}
            if all(v is not None for v in probe.values()) and await self.find_one(probe):
                raise DuplicateKeyError(f'{self.name}: дубль по {keys}')

        # как настоящая БД: коллекция владеет своими данными, а не ссылками
        # на словари из теста (иначе правка документа меняет и ожидаемое значение)
        doc = copy.deepcopy(doc)
        if '_id' not in doc:
            self._auto_id += 1
            doc['_id'] = self._auto_id
        self.docs.append(doc)
        return FakeResult(upserted_id=doc['_id'])

    async def insert_many(self, docs):
        for doc in docs:
            await self.insert_one(doc)

    async def delete_many(self, query):
        keep = [doc for doc in self.docs if not self._match(doc, query or {})]
        removed = len(self.docs) - len(keep)
        self.docs[:] = keep
        return FakeResult(matched=removed, modified=removed, deleted=removed)

    async def delete_one(self, query):
        for i, doc in enumerate(self.docs):
            if self._match(doc, query):
                self.docs.pop(i)
                return FakeResult(matched=1, modified=1, deleted=1)
        return FakeResult()

    async def update_one(self, query, update, upsert=False):
        doc = await self.find_one(query)
        upserted = None
        if doc is None:
            if not upsert:
                return FakeResult()
            doc = {}
            for key, value in query.items():
                if not isinstance(value, dict):
                    self._set_path(doc, key, value)
            await self.insert_one(doc)
            doc = self.docs[-1]
            upserted = doc['_id']

        for key, value in (update.get('$set') or {}).items():
            if '.$.' in key:
                array_path, field = key.split('.$.', 1)
                index = self._positional_index(doc, query, array_path)
                if index is not None:
                    (self._get(doc, array_path) or [])[index][field] = value
                continue
            self._set_path(doc, key, value)
        for key, value in (update.get('$setOnInsert') or {}).items():
            if upserted is not None:
                self._set_path(doc, key, value)
        for key, value in (update.get('$inc') or {}).items():
            self._set_path(doc, key, (self._get(doc, key) or 0) + value)
        for key, value in (update.get('$push') or {}).items():
            items = self._get(doc, key) or []
            if isinstance(value, dict) and '$each' in value:
                items = (items + list(value['$each']))[value.get('$slice', 0) or None:]
            else:
                items = items + [value]
            self._set_path(doc, key, items)
        for key, _ in (update.get('$unset') or {}).items():
            parts = key.split('.')
            target = doc
            for part in parts[:-1]:
                target = target.get(part) if isinstance(target, dict) else None
                if target is None:
                    break
            if isinstance(target, dict):
                target.pop(parts[-1], None)
        for key, value in (update.get('$addToSet') or {}).items():
            items = self._get(doc, key) or []
            if value not in items:
                items = items + [value]
            self._set_path(doc, key, items)
        for key, value in (update.get('$pull') or {}).items():
            # значение — либо сам элемент, либо шаблон полей, как в Mongo
            items = self._get(doc, key) or []
            if isinstance(value, dict):
                kept = [item for item in items
                        if not (isinstance(item, dict)
                                and all(item.get(k) == v for k, v in value.items()))]
            else:
                kept = [item for item in items if item != value]
            self._set_path(doc, key, kept)

        return FakeResult(matched=1, modified=0 if upserted else 1, upserted_id=upserted)

    async def update_many(self, query, update):
        """То же обновление ко всем подходящим документам.

        Через update_one по _id: позиционный $ при таком обходе не сработает,
        но массовые операции его и не используют — там $set и $unset.
        """
        ids = [doc['_id'] for doc in self.docs if self._match(doc, query or {})]
        for doc_id in ids:
            await self.update_one({'_id': doc_id}, update)
        return FakeResult(matched=len(ids), modified=len(ids))

    @staticmethod
    def _set_path(doc, path, value):
        parts = path.split('.')
        current = doc
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

    async def find_one_and_update(self, query, update, upsert=False,
                                  return_document=True):
        """Возвращает документ ПОСЛЕ изменения — как Mongo с ReturnDocument.AFTER.

        Искать его повторно по исходному фильтру нельзя: обновление обычно
        как раз и выводит документ из-под условия (used_count < max_uses).
        """
        found = await self.find_one(query)
        result = await self.update_one(query, update, upsert=upsert)
        if found is None and not result.upserted_id:
            return None
        doc_id = found['_id'] if found else result.upserted_id
        return await self.find_one({'_id': doc_id})

    async def create_index(self, keys, unique=False, **kwargs):
        from app.repositories.base import index_name

        name = index_name(keys)
        existing = self.indexes.get(name)
        if existing is not None and existing != unique:
            raise MongoError('An existing index has the same name', code=86)

        fields = ((keys,) if isinstance(keys, str)
                  else tuple(k if isinstance(k, str) else k[0] for k in keys))

        if unique:
            partial = kwargs.get('partialFilterExpression')
            seen = set()
            for doc in self.docs:
                if partial and not self._match(doc, partial):
                    continue      # документ вне условия индекса — не участвует
                key = tuple(self._get(doc, f) for f in fields)
                # без partialFilterExpression отсутствующее поле = null,
                # и второй такой документ Mongo считает дублем
                if key in seen:
                    raise MongoError('duplicate key', code=11000)
                seen.add(key)
            if fields not in self.unique_keys:
                self.unique_keys.append(fields)

        self.indexes[name] = unique
        return name

    async def drop_index(self, name):
        unique = self.indexes.pop(name, None)
        if unique:
            self.unique_keys = [k for k in self.unique_keys
                                if '_'.join(f'{f}_1' for f in k) != name]
        return None


class FakeDB(dict):
    def __getitem__(self, name):
        if name not in self:
            super().__setitem__(name, FakeCollection(name))
        return super().__getitem__(name)


class FakeBot:
    """Записывает отправленные сообщения вместо обращения к Telegram."""

    def __init__(self, fail_for: set[int] | None = None):
        self.sent: list[tuple[int, str]] = []
        self.fail_for = fail_for or set()

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        if chat_id in self.fail_for:
            from aiogram.exceptions import TelegramForbiddenError
            raise TelegramForbiddenError(method=None, message='bot was blocked')
        self.sent.append((chat_id, text))
        return True


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def config():
    return Config(
        bot_token='123:TEST', mongo_uri='memory', mongo_db='test',
        admin_ids=(1,), vpn=VpnPanelConfig('', '', ''), payments=PaymentsConfig(),
        environment='test',
    )


@pytest.fixture
def container(config, db):
    from app.core.container import Container
    return Container(config=config, db=db)


@pytest.fixture
def user_factory(db):
    counter = {'n': 0}

    async def make(**overrides):
        counter['n'] += 1
        doc = {
            'user_data': {'user_id': counter['n'], 'first_name': f'User{counter["n"]}',
                          'date_joined': datetime.now()},
            'info': {'balance': 0, 'email': 'Не привязана', 'transactions': [],
                     'ref_stats': {'referrals': []}},
            'vpn': {'shortUuid': '', 'period': 0, 'hwidDeviceLimit': 2},
            'growth': {'balance': 0, 'has_topup': False, 'topups_count': 0},
            'campaigns': {},
        }
        for path, value in overrides.items():
            FakeCollection._set_path(doc, path, value)
        await db['users'].insert_one(doc)
        return db['users'].docs[-1]  # с проставленным _id

    return make
