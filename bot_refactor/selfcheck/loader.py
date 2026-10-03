"""Заглушка loader.py для проверки модулей вне проекта бота."""


class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, field, direction=1):
        self._docs = sorted(self._docs, key=lambda d: (d.get(field) is None, d.get(field, 0)),
                            reverse=direction < 0)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs if length is None else self._docs[:length])

    def __aiter__(self):
        self._it = iter(list(self._docs))
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, name):
        self.name = name
        self.docs = []

    def _match(self, doc, flt):
        for k, v in flt.items():
            cur = doc
            for part in k.split('.'):
                cur = (cur or {}).get(part) if isinstance(cur, dict) else None
            if isinstance(v, dict) and '$nin' in v:
                if cur in v['$nin']:
                    return False
            elif cur != v:
                return False
        return True

    def find(self, flt=None, projection=None):
        flt = flt or {}
        return FakeCursor([d for d in self.docs if self._match(d, flt)])

    async def find_one(self, flt, projection=None):
        return next((d for d in self.docs if self._match(d, flt)), None)

    async def count_documents(self, flt):
        return len([d for d in self.docs if self._match(d, flt)])

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def insert_many(self, docs):
        self.docs.extend(dict(d) for d in docs)

    async def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if self._match(d, flt):
                self.docs.pop(i)
                return

    async def update_one(self, flt, update, upsert=False):
        doc = await self.find_one(flt)
        if doc is None:
            if not upsert:
                return
            doc = dict(flt)
            self.docs.append(doc)
        for key, value in (update.get('$set') or {}).items():
            cur = doc
            parts = key.split('.')
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = value
        for key, value in (update.get('$inc') or {}).items():
            doc[key] = doc.get(key, 0) + value

    async def create_index(self, *args, **kwargs):
        return None


class FakeDB(dict):
    def __getitem__(self, name):
        if name not in self:
            super().__setitem__(name, FakeCollection(name))
        return super().__getitem__(name)


db = FakeDB()
users = db['users']
promo_codes = db['promo_codes']
support_quick_replies = db['support_quick_replies']
churn_surveys = db['churn_surveys']
admins_ids = [802421217, 1107871653]
bot = None
