"""Журнал движения денег — отдельной коллекцией, навсегда.

Зачем отдельно от `info.transactions` в документе пользователя: тот массив
обрезан до 500 записей и обрезан не просто так — документ Mongo ограничен
16 МБ, и активный человек за пару лет упрётся в предел, после чего
перестанет работать вообще всё, включая списание баланса. Для бота этого
хватает: ему нужны последние операции на экране.

Операторам нужно другое — вся история, поиск по периоду, фильтр по типу
операции и ответ на вопрос «за что списали 75₽ третьего числа». Это
коллекция, а не массив: по ней есть индексы, её можно листать и
агрегировать, и она не растёт внутри чужого документа.

Правило записи одно: журнал пишется ПОСЛЕ успешного изменения баланса и
никогда не роняет саму операцию. Запись, которой нет, — потерянная строка
в истории; исключение на этом месте — потерянные деньги.
"""

from __future__ import annotations

import logging

from app.core.time import now
from app.repositories.base import Repository

log = logging.getLogger(__name__)

# Машинные коды операций. Панель оператора и аналитика группируют по ним, а
# не по тексту описания: описания меняются от сборки к сборке, и любой отчёт,
# построенный на подстроках, однажды тихо разъезжается.
KINDS: dict[str, str] = {
    'topup': 'Пополнение',
    'plan': 'Покупка подписки',
    'renewal': 'Продление подписки',
    'devices': 'Доп. устройства',
    'bypass': 'Трафик ByPass',
    'promo': 'Промокод',
    'referral': 'Реферальное начисление',
    'campaign': 'Бонус кампании',
    'survey': 'Бонус за опрос',
    'gift': 'Подарок',
    'payout': 'Вывод средств',
    'private_server': 'Личный сервер',
    'refund': 'Возврат',
    'admin': 'Правка администратора',
    'other': 'Прочее',
}

# Если код не передали — угадываем по описанию. Не чтобы разрешить забывать
# код, а чтобы ни одна операция не осталась без категории: строка «прочее»
# в отчёте бесполезна, а найти её потом невозможно.
RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (('возврат',), 'refund'),
    (('пополнение', 'topup'), 'topup'),
    (('промокод',), 'promo'),
    (('реферальн',), 'referral'),
    (('бонус за возвращение', 'кампани'), 'campaign'),
    (('опрос',), 'survey'),
    (('подар',), 'gift'),
    (('устройств',), 'devices'),
    (('bypass', 'байпас'), 'bypass'),
    (('личный сервер', 'сервер'), 'private_server'),
    (('продление подписки',), 'renewal'),
    (('покупка подписки', 'тариф'), 'plan'),
    (('вывод', 'реферального баланса'), 'payout'),
)


def guess_kind(description: str) -> str:
    text = (description or '').lower()
    for words, kind in RULES:
        if any(word in text for word in words):
            return kind
    return 'other'


class BalanceLogRepository(Repository):
    async def ensure_indexes(self) -> None:
        # карточка пользователя: его операции, свежие сверху
        await self.ensure_index([('user_id', 1), ('at', -1)])
        # сводки за период и выгрузки
        await self.ensure_index([('at', -1)])
        await self.ensure_index('kind')

    async def record(self, user_id: int, amount: int, description: str,
                     kind: str = '', auto: bool = False, admin_id: int = 0,
                     balance_after: int | None = None, account: str = 'balance',
                     meta: dict | None = None) -> None:
        """Одна строка журнала. Ошибку глотаем: деньги важнее записи о них."""
        kind = kind or guess_kind(description)
        document = {
            'user_id': int(user_id),
            'at': now(),
            # Знак — главное поле: плюс это приход, минус расход. Старый бот
            # писал сумму операции без знака, и продление на 100₽ выглядело
            # в панели как «+100», то есть как пополнение.
            'amount': int(amount),
            'direction': 'in' if amount >= 0 else 'out',
            'kind': kind,
            'title': KINDS.get(kind, KINDS['other']),
            'description': description,
            # Балансов два: обычный и реферальный. Складывать их в одну
            # колонку значит показать пополнение там, где денег на балансе не
            # прибавилось: реферальный процент падает на свой счёт, и снять
            # его можно только заявкой на вывод.
            'account': account,
            # кто двинул деньги: человек сам, бот по расписанию или админ
            'source': 'admin' if admin_id else ('auto' if auto else 'user'),
            'auto': bool(auto),
            'admin_id': int(admin_id or 0),
            'balance_after': balance_after,
            'meta': meta or {},
        }
        try:
            await self.col.insert_one(document)
        except Exception as exc:
            log.warning('журнал баланса %s не записан: %s', user_id, exc)

    async def for_user(self, user_id: int, limit: int = 100,
                       skip: int = 0) -> list[dict]:
        return await self.col.find({'user_id': int(user_id)}).sort(
            'at', -1).skip(skip).to_list(length=limit)
