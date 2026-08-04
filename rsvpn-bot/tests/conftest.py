"""Заглушки Mongo и Telegram: тесты идут без сети и без БД.

Это не «моки ради моков» — так проверяются денежные сценарии (списание,
идемпотентность, повторная отправка кампании), которые иначе можно
проверить только в проде на живых пользователях.
"""

from __future__ import annotations

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


class FakeResult:
    def __init__(self, matched=0, modified=0, upserted_id=None):
        self.matched_count = matched
        self.modified_count = modified
        self.upserted_id = upserted_id


class FakeCollection:
    """Минимальный Mongo: точечные пути, $set/$inc/$push/$addToSet, $exists/$gte/$nin."""

    def __init__(self, name='fake'):
        self.name = name
        self.docs: list[dict] = []
        self._auto_id = 0

    # ── чтение ──────────────────────────────────────────────────────────────
    @staticmethod
    def _get(doc, path):
        current = doc
        for part in path.split('.'):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    def _match(self, doc, query):
        for key, condition in query.items():
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
            elif value != condition:
                return False
        return True

    def find(self, query=None, projection=None):
        return FakeCursor([d for d in self.docs if self._match(d, query or {})])

    async def find_one(self, query, projection=None):
        return next((d for d in self.docs if self._match(d, query)), None)

    async def count_documents(self, query=None, limit=None):
        found = [d for d in self.docs if self._match(d, query or {})]
        return len(found[:limit] if limit else found)

    # ── запись ──────────────────────────────────────────────────────────────
    async def insert_one(self, doc):
        doc = dict(doc)
        if '_id' not in doc:
            self._auto_id += 1
            doc['_id'] = self._auto_id
        self.docs.append(doc)
        return FakeResult(upserted_id=doc['_id'])

    async def insert_many(self, docs):
        for doc in docs:
            await self.insert_one(doc)

    async def delete_one(self, query):
        for i, doc in enumerate(self.docs):
            if self._match(doc, query):
                self.docs.pop(i)
                return FakeResult(matched=1, modified=1)
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
        for key, value in (update.get('$addToSet') or {}).items():
            items = self._get(doc, key) or []
            if value not in items:
                items = items + [value]
            self._set_path(doc, key, items)

        return FakeResult(matched=1, modified=0 if upserted else 1, upserted_id=upserted)

    @staticmethod
    def _set_path(doc, path, value):
        parts = path.split('.')
        current = doc
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

    async def create_index(self, *args, **kwargs):
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
