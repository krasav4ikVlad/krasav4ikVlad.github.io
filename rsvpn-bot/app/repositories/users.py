"""Всё про документ пользователя.

Здесь же — атомарные операции, которые в старом коде были размазаны:
списание баланса с проверкой (сейчас можно уйти в минус при двойном клике)
и «захват слота» кампании (сейчас копия в каждом файле кампаний).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.core.time import now
from app.repositories.base import Repository

# отдельный логгер: `grep money` даёт только денежные события
money = logging.getLogger('money')


class UsersRepository(Repository):
    # Журнал движения денег (app/repositories/balance_log.py). Ставится
    # контейнером; без него бот работает как раньше, просто без истории —
    # ронять списание из-за ненастроенного журнала нельзя.
    journal = None

    async def record_money(self, user_id: int, amount: int, description: str,
                           kind: str = '', auto: bool = False, admin_id: int = 0,
                           balance_after: int | None = None,
                           account: str = 'balance',
                           meta: dict | None = None) -> None:
        """Записать движение денег в журнал операторов.

        Вынесено сюда, а не в каждый сервис: баланс двигают пять разных мест,
        и три из них делают это одним атомарным запросом мимо credit/charge
        (плата за устройства, реферальные, бонус кампании). Общий метод
        гарантирует, что строка в журнале выглядит одинаково независимо от
        того, откуда пришла.
        """
        if self.journal is None:
            return
        await self.journal.record(user_id, amount, description, kind=kind,
                                  auto=auto, admin_id=admin_id, account=account,
                                  balance_after=balance_after, meta=meta)

    async def balance_of(self, user_id: int) -> int:
        user = await self.get(user_id, {'info.balance': 1})
        return int(self.pick(user or {}, 'info.balance', 0) or 0)

    async def ensure_indexes(self) -> None:
        # only_existing: документы без user_id (мусор из старых версий)
        # не должны мешать уникальному индексу
        await self.ensure_index('user_data.user_id', unique=True, only_existing=True)
        await self.ensure_index('growth.segment')
        await self.ensure_index('vpn.expireAt')

    # ── чтение ──────────────────────────────────────────────────────────────
    async def get(self, user_id: int, projection: dict | None = None) -> dict | None:
        return await self.col.find_one({'user_data.user_id': user_id}, projection)

    async def exists(self, user_id: int) -> bool:
        return await self.col.count_documents({'user_data.user_id': user_id}, limit=1) > 0

    # ── создание ────────────────────────────────────────────────────────────
    async def create(self, document: dict) -> dict:
        await self.col.insert_one(document)
        return document

    async def promise_bonus(self, user_id: int, rate: float) -> bool:
        """Пообещать прибавку к следующему пополнению. Одноразовая.

        Кладётся в info.bonus_multiplier, который TopupService прибавляет к
        обычному бонусу и тут же обнуляет.

        Через $max, а не $set: обещание уже могло быть выдано за что-то
        другое, и отбирать его, выдавая второе, нечестно — человек его не
        получал и даже не знает, что оно было. Побеждает большее.
        """
        if rate <= 0:
            return False
        result = await self.col.update_one(
            {'user_data.user_id': user_id},
            {'$max': {'info.bonus_multiplier': float(rate)}},
        )
        return bool(result.matched_count)

    # ── баланс ──────────────────────────────────────────────────────────────
    async def credit(self, user_id: int, amount: int, description: str,
                     auto: bool = False, kind: str = '', admin_id: int = 0,
                     meta: dict | None = None) -> bool:
        """Начисление. Транзакция пишется единым форматом-словарём.

        auto=True — деньги двинул бот, а не человек: автопродление, плата за
        устройства, реферальное начисление. В журнале такие идут отдельной
        категорией, иначе на платформе операторов «списал сам» и «списалось
        само» выглядят одинаково, а вопросы по ним разные.
        """
        # find_one_and_update, а не update_one: журналу нужен баланс ПОСЛЕ
        # операции, а отдельным чтением его не получить — между запросами
        # может пройти чужое списание, и в истории окажется чужая цифра.
        after = await self.col.find_one_and_update(
            {'user_data.user_id': user_id},
            {
                '$inc': {'info.balance': amount},
                '$push': {'info.transactions': {
                    '$each': [self._transaction(amount, description)],
                    '$slice': -500,
                }},
            },
            return_document=True,
        )
        result = type('_R', (), {'matched_count': 1 if after else 0})()
        # Деньги — в лог всегда и из одного места: сервисов, которые их
        # двигают, полдесятка, и логировать в каждом значит однажды забыть.
        # Поддержке нужен ответ на «за что списали», а не только результат.
        if result.matched_count == 1:
            money.info('%s +%s₽  %s', user_id, amount, description)
            await self.log(user_id, self.ACTION_AUTO if auto else self.ACTION_CREDIT,
                           f'+{amount}₽ {description}')
            await self.record_money(
                user_id, amount, description, kind=kind, auto=auto,
                admin_id=admin_id, meta=meta,
                balance_after=int(self.pick(after or {}, 'info.balance', 0) or 0))
        return result.matched_count == 1

    async def charge(self, user_id: int, amount: int, description: str,
                     auto: bool = False, kind: str = '', admin_id: int = 0,
                     meta: dict | None = None) -> bool:
        """Списание с проверкой в самом запросе.

        Условие 'info.balance': {'$gte': amount} внутри update гарантирует, что
        двойное нажатие кнопки не уведёт баланс в минус: второй запрос просто
        не найдёт документ. Возвращает False, если средств не хватило.
        """
        after = await self.col.find_one_and_update(
            {'user_data.user_id': user_id, 'info.balance': {'$gte': amount}},
            {
                '$inc': {'info.balance': -amount},
                '$push': {'info.transactions': {
                    '$each': [self._transaction(-amount, description)],
                    '$slice': -500,
                }},
            },
            return_document=True,
        )
        result = type('_R', (), {'modified_count': 1 if after else 0})()
        if result.modified_count == 1:
            money.info('%s −%s₽  %s', user_id, amount, description)
            await self.log(user_id, self.ACTION_AUTO if auto else self.ACTION_CHARGE,
                           f'−{amount}₽ {description}')
            await self.record_money(
                user_id, -amount, description, kind=kind, auto=auto,
                admin_id=admin_id, meta=meta,
                balance_after=int(self.pick(after or {}, 'info.balance', 0) or 0))
        else:
            money.info('%s не хватило %s₽  %s', user_id, amount, description)
            await self.log(user_id, self.ACTION_AUTO if auto else self.ACTION_CHARGE,
                           f'не хватило {amount}₽ {description}')
        return result.modified_count == 1

    # ── кто заблокировал бота ───────────────────────────────────────────────
    #
    # Telegram отвечает TelegramForbiddenError, и это не ошибка, а факт:
    # писать такому человеку больше нельзя. Без отметки каждая рассылка
    # заново тратила бы на него попытку и место в отчёте, а «не доставлено»
    # росло без объяснения.
    BLOCKED = 'growth.blocked_bot'

    async def mark_blocked(self, user_id: int) -> None:
        await self.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {self.BLOCKED: True, 'growth.blocked_at': now()}})

    async def unmark_blocked(self, query: dict | None = None) -> int:
        """Снять отметку — человек мог вернуться и разблокировать бота.

        Проверить это заранее нельзя: Telegram не сообщает о разблокировке
        и не отвечает на вопрос «можно ли писать». Единственный способ
        узнать — попробовать отправить, поэтому отметку и надо уметь
        сбрасывать целиком.
        """
        result = await self.col.update_many(
            {**(query or {}), self.BLOCKED: True},
            {'$unset': {self.BLOCKED: '', 'growth.blocked_at': ''}})
        return int(getattr(result, 'modified_count', 0) or 0)

    async def blocked_count(self, query: dict | None = None) -> int:
        return await self.col.count_documents({**(query or {}), self.BLOCKED: True})

    @staticmethod
    def _transaction(amount: int, description: str) -> dict:
        return {'amount': amount, 'dt': now(), 'description': description}

    # ── подписка ────────────────────────────────────────────────────────────
    async def set_vpn(self, user_id: int, vpn_fields: dict) -> None:
        await self.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {f'vpn.{key}': value for key, value in vpn_fields.items()}},
        )

    # ── кампании ────────────────────────────────────────────────────────────
    async def claim_campaign_slot(self, doc_id: Any, flag: str,
                                  extra_update: dict | None = None) -> bool:
        """Атомарно ставит флаг кампании, если его ещё нет.

        True — слот наш, сообщение отправляем; False — уже отправляли.
        Это защита от повторной отправки и повторного начисления бонуса,
        когда планировщик запускается раз в час и пересекается сам с собой.
        """
        update: dict[str, dict] = {'$set': {f'campaigns.{flag}': now()}}
        for operator, payload in (extra_update or {}).items():
            update.setdefault(operator, {})
            update[operator].update(payload)

        result = await self.col.update_one(
            {'_id': doc_id, f'campaigns.{flag}': {'$exists': False}},
            update,
        )
        return result.modified_count == 1

    # ── логи действий ───────────────────────────────────────────────────────
    #
    # Журнал читает не только этот код: у операторов своя платформа, которая
    # разбирает записи по полю action и ждёт время строкой в том же виде,
    # что был у старого бота. Поэтому набор категорий фиксированный, а
    # формат времени менять нельзя — это чужой контракт, а не наш выбор.
    ACTION_USER = 'Действие пользователя'      # нажатие или команда
    ACTION_CHARGE = 'Списание'                 # деньги ушли
    ACTION_CREDIT = 'Начисление'               # деньги пришли
    ACTION_AUTO = 'Автоматическое действие'    # сделал бот, человек не нажимал

    TIME_FORMAT = '%d.%m.%Y %H:%M:%S'

    async def log(self, user_id: int, action: str, details: str = '') -> None:
        """История действий человека — в его же документе, последние 350.

        Именно в документе, а не отдельной коллекцией: поддержке нужна
        история конкретного человека, и она читается тем же запросом, что
        и всё остальное про него. Ограничение сверху обязательно — без
        него активный пользователь за год раздувает документ до предела
        Mongo в 16 МБ, и тогда перестаёт работать вообще всё, включая
        списание баланса.

        Ошибка записи гасится: история полезна, но ради неё нельзя ронять
        то действие, которое она описывает. Зато она видна в логе.
        """
        try:
            await self.col.update_one(
                {'user_data.user_id': user_id},
                {'$push': {'logs': {
                    '$each': [{'action': action, 'details': details,
                               'timestamp': now().strftime(self.TIME_FORMAT)}],
                    '$slice': -350,
                }}},
            )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                'история %s не записана: %s', user_id, exc)
