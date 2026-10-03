"""Lifeline: после истечения оставляем один рабочий сервер.

Перенос из lifeline.py. Смысл прежний: когда подписка кончилась, человека
переводят на TG-сквад с grace-днями, чтобы он мог зайти в бота и продлить
даже там, где Telegram блокируется. При продлении родные сквады возвращаются.

Что изменилось:
* секрет вебхука и UUID сквада — из конфига и настроек, а не константами
  в файле (секрет лежал открытым текстом в репозитории);
* число grace-дней и сам тумблер — настройки админки;
* обращение к панели идёт через общий клиент, а не через переданную снаружи
  функцию remna_update с описанным в докстроке контрактом.
"""

from __future__ import annotations

import logging

from app.core.errors import VpnPanelError
from app.core.time import now, parse_dt, plus_days

log = logging.getLogger(__name__)


class LifelineService:
    def __init__(self, users, settings, vpn, notifier=None):
        self.users = users
        self.settings = settings
        self.vpn = vpn
        self.notifier = notifier

    async def on_expired(self, user: dict) -> dict:
        """Событие user.expired: переводим на lifeline-сквад."""
        if not await self.settings.flag('lifeline.enabled'):
            return {'note': 'disabled'}

        vpn = user.get('vpn') or {}
        uuid = vpn.get('uuid')
        squad = str(await self.settings.get('lifeline.squad_uuid') or '')
        if not uuid or not squad:
            return {'note': 'not_configured'}

        grace_until = plus_days(await self.settings.int('lifeline.grace_days'))

        if vpn.get('in_lifeline'):
            # grace кончился, а человек не продлился. Раньше окно продлевалось
            # молча и на следующем событии истечения — тоже, то есть вечно:
            # панель гасит подписку через три дня, присылает `user.expired`,
            # и мы снова выдаём три дня. Запасной сервер, задуманный как
            # «успеть открыть бота и продлить», превращался в бесплатную
            # подписку навсегда для каждого, кто хоть раз платил.
            if self._grace_over(vpn, await self.settings.int('lifeline.max_days')):
                log.info('lifeline: %s сидит дольше предела — больше не продлеваем',
                         uuid)
                return {'note': 'grace_over'}

            await self.vpn.update_subscription(uuid, expire_at=grace_until, squads=[squad])
            return {'note': 'grace_extended'}

        original = [s for s in (vpn.get('activeInternalSquads') or [])
                    if isinstance(s, str) and s and s != squad]
        if not original:
            # без сохранённого набора вернуть человека будет некуда
            log.error('lifeline: у %s пустой activeInternalSquads, пропускаем', uuid)
            return {'note': 'no_original_squads'}

        try:
            await self.vpn.update_subscription(uuid, expire_at=grace_until, squads=[squad])
        except VpnPanelError as exc:
            log.error('lifeline: перевод не удался uuid=%s: %s', uuid, exc)
            return {'note': 'panel_error'}

        await self.users.col.update_one(
            {'_id': user['_id']},
            {'$set': {'vpn.in_lifeline': True, 'vpn.orig_squads': original,
                      'vpn.lifeline_at': now()}},
        )
        log.info('lifeline: %s переведён на запасной сервер до %s', uuid, grace_until)
        return {'note': 'moved', 'until': grace_until, 'original': original}

    @staticmethod
    def _grace_over(vpn: dict, max_days: int) -> bool:
        """Дольше ли человек на запасном сервере, чем мы готовы его держать.

        Считаем от первого перевода (`lifeline_at`), а не от последнего
        продления: иначе предел никогда не наступит — каждое продление
        обнуляло бы счёт. 0 — держать без ограничения, как было раньше.
        """
        if max_days <= 0:
            return False
        since = parse_dt(vpn.get('lifeline_at'))
        if not since:
            # Не знаем, когда перевели, — значит перевели давно, до того как
            # мы начали это записывать. Держать вечно тем более незачем.
            return True
        return (now() - since).days > max_days

    async def restore(self, user_id: int) -> bool:
        """Вернуть родные сквады. Вызывается сразу после продления."""
        user = await self.users.get(user_id)
        vpn = (user or {}).get('vpn') or {}
        if not user or not vpn.get('in_lifeline'):
            return False

        uuid = vpn.get('uuid')
        original = vpn.get('orig_squads') or []
        expire = parse_dt(vpn.get('expireAt'))

        if not (uuid and original and expire):
            log.error('lifeline: нечем восстановить %s (сквады/дата отсутствуют)', user_id)
            return False

        try:
            await self.vpn.update_subscription(uuid, expire_at=expire, squads=original)
        except VpnPanelError as exc:
            log.error('lifeline: возврат не удался uuid=%s: %s', uuid, exc)
            return False

        await self.users.col.update_one(
            {'_id': user['_id']},
            {'$set': {'vpn.in_lifeline': False}, '$unset': {'vpn.orig_squads': ''}},
        )
        log.info('lifeline: %s возвращён на свои серверы', uuid)
        return True

    async def reconcile(self) -> int:
        """Страховка: вернуть всех, кто продлился, но остался на lifeline.

        Нужна, потому что мгновенный возврат после оплаты может не случиться —
        упал процесс, не ответила панель.
        """
        restored = 0
        async for user in self.users.iterate({'vpn.in_lifeline': True},
                                             {'_id': 1, 'vpn': 1, 'user_data.user_id': 1}):
            expire = parse_dt((user.get('vpn') or {}).get('expireAt'))
            if expire and expire > now():
                user_id = self.users.pick(user, 'user_data.user_id')
                if user_id and await self.restore(user_id):
                    restored += 1
        return restored
