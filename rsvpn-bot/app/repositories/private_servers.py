"""Личные серверы: доступ к коллекции.

Участники лежат прямо в документе сервера, а не отдельной коллекцией: их
максимум пятнадцать, читаются они всегда вместе с сервером, и отдельная
таблица дала бы join там, где хватает одного запроса.
"""

from __future__ import annotations

import secrets

from app.core.time import now
from app.domain import private_servers as ps
from app.repositories.base import Repository


class PrivateServersRepository(Repository):
    async def ensure_indexes(self) -> None:
        await self.ensure_index('owner_id')
        await self.ensure_index('status')
        await self.ensure_index('members.user_id')
        await self.ensure_index('invites.code')
        await self.ensure_index('squad_uuid')

    # ── чтение ──────────────────────────────────────────────────────────────
    async def get(self, server_id: str) -> dict | None:
        return await self.col.find_one({'_id': server_id})

    async def of_owner(self, user_id: int) -> dict | None:
        """Активный (или готовящийся) сервер владельца. Закрытые не считаются."""
        return await self.col.find_one({'owner_id': user_id,
                                        'status': {'$in': list(ps.LIVE_STATUSES)}})

    async def of_member(self, user_id: int) -> list[dict]:
        return await self.col.find({'members.user_id': user_id,
                                    'status': {'$in': list(ps.LIVE_STATUSES)}}
                                   ).to_list(length=50)

    async def by_invite(self, code: str) -> dict | None:
        return await self.col.find_one({'invites.code': code})

    async def on_squad(self, squad_uuid: str) -> list[dict]:
        """Живые серверы на этой машине.

        Для долевого тарифа их несколько: у каждого свой владелец, свои
        участники и своя оплата, а сквад — общий.
        """
        if not squad_uuid:
            return []
        return await self.col.find({'squad_uuid': squad_uuid,
                                    'status': {'$in': list(ps.LIVE_STATUSES)}}
                                   ).to_list(length=50)

    async def pending(self) -> list[dict]:
        return await self.col.find({'status': ps.REQUESTED}).sort('created_at', 1
                                                                  ).to_list(length=100)

    async def due(self, moment=None) -> list[dict]:
        """Серверы, за которые пора списать очередной месяц."""
        return await self.col.find({'status': ps.ACTIVE,
                                    'next_charge_at': {'$lte': moment or now()}}
                                   ).to_list(length=500)

    async def soon_due(self, border) -> list[dict]:
        """Кому пора сказать, что через несколько дней спишется месяц.

        Отметка charge_warned_at сбрасывается после каждого списания, поэтому
        одно предупреждение на цикл, а не одно на всю жизнь сервера.
        """
        return await self.col.find({'status': ps.ACTIVE, 'autorenew': True,
                                    'charge_warned_at': None,
                                    'next_charge_at': {'$lte': border}}
                                   ).to_list(length=500)

    async def overdue(self, border) -> list[dict]:
        """Приостановленные дольше отсрочки — их пора закрывать."""
        return await self.col.find({'status': ps.SUSPENDED,
                                    'suspended_at': {'$lte': border}}
                                   ).to_list(length=500)

    # ── запись ──────────────────────────────────────────────────────────────
    async def create(self, owner_id: int, plan: ps.ServerPlan, title: str,
                     location: str = '', profile: str = '', traffic_gb: int = 0) -> dict:
        document = {
            '_id': f'srv_{secrets.token_hex(4)}',
            'owner_id': owner_id,
            'plan': plan.code, 'slots': plan.slots, 'price': plan.price,
            'title': title, 'status': ps.REQUESTED,
            'squad_uuid': '',
            # локацию и протокол выбирает покупатель, а не админ при выдаче:
            # от них зависит цена площадки и скорость, и переиграть их молча
            # значит продать не то, за что заплатили
            'location': location, 'profile': profile, 'traffic_gb': traffic_gb,
            'members': [], 'invites': [],
            'autorenew': True, 'charge_warned_at': None,
            'created_at': now(), 'activated_at': None,
            'paid_until': None, 'next_charge_at': None,
        }
        await self.col.insert_one(document)
        return document

    async def set(self, server_id: str, **fields) -> None:
        await self.col.update_one({'_id': server_id}, {'$set': fields})

    async def delete(self, server_id: str) -> bool:
        """Стереть сервер совсем. Для тестов: закрытый остаётся в истории."""
        result = await self.col.delete_one({'_id': server_id})
        return getattr(result, 'deleted_count', 0) == 1

    async def add_member(self, server_id: str, user_id: int, limit: int) -> bool:
        """Занять слот атомарно.

        Проверка «есть ли место» живёт внутри запроса: два человека могут
        открыть одну ссылку одновременно, и питоновская проверка перед
        записью пропустила бы обоих.
        """
        result = await self.col.update_one(
            {'_id': server_id,
             'members.user_id': {'$ne': user_id},
             f'members.{limit - 1}': {'$exists': False}},
            {'$push': {'members': {'user_id': user_id, 'joined_at': now()}}},
        )
        return result.modified_count == 1

    async def remove_member(self, server_id: str, user_id: int) -> bool:
        result = await self.col.update_one(
            {'_id': server_id}, {'$pull': {'members': {'user_id': user_id}}})
        return result.modified_count == 1

    # ── приглашения ─────────────────────────────────────────────────────────
    async def add_invite(self, server_id: str) -> str:
        """Одноразовый код. Хранится в сервере — искать его больше негде."""
        code = secrets.token_urlsafe(9).replace('-', '').replace('_', '')[:12]
        await self.col.update_one(
            {'_id': server_id},
            # $slice отрезает старые: без него список растёт вечно, а нужны
            # только свежие — просроченные приглашения никто не ищет
            {'$push': {'invites': {'$each': [{'code': code, 'created_at': now(),
                                              'used_by': None, 'used_at': None}],
                                   '$slice': -50}}},
        )
        return code

    async def use_invite(self, server_id: str, code: str, user_id: int) -> bool:
        """Погасить код. Одноразовость — условием запроса, а не проверкой в коде."""
        result = await self.col.update_one(
            {'_id': server_id, 'invites': {'$elemMatch': {'code': code, 'used_by': None}}},
            {'$set': {'invites.$.used_by': user_id, 'invites.$.used_at': now()}},
        )
        return result.modified_count == 1

    async def release_invite(self, server_id: str, code: str) -> None:
        """Вернуть код в оборот, если присоединиться в итоге не вышло."""
        await self.col.update_one(
            {'_id': server_id, 'invites.code': code},
            {'$set': {'invites.$.used_by': None, 'invites.$.used_at': None}})

    async def drop_invite(self, server_id: str, code: str) -> None:
        await self.col.update_one({'_id': server_id},
                                  {'$pull': {'invites': {'code': code}}})
