"""Торренты: предупредить человека, повторного — отключить.

Плагин Torrent Blocker на ноде ловит bittorrent и закрывает IP на своё
время (`blockDuration` в его конфигурации). Для нас это событие важнее,
чем для него: из-за торрентов блокируют сервер целиком, и без доступа
остаются все, а не один.

Панель присылает это вебхуком `torrent_blocker.report` (с версии 2.7.0).
Мы его не создаём и не перепроверяем — только объясняем человеку, что
случилось, и считаем, сколько раз это было.

Лестница из трёх ступеней, пороги — в настройках:

  * первое нарушение — только разговор: IP человеку уже закрыл сам плагин,
    мы объясняем, что было и что будет дальше;
  * второе — замораживаем подписку на полчаса своими руками; доступ
    возвращает планировщик, когда срок выйдет;
  * третье — отключаем навсегда, и включить заново нельзя даже новой
    покупкой.

Под каждым сообщением — кнопка «я не качаю торренты»: проверить это
автоматически нечем, поэтому жалоба уходит в админ-чат вместе с числами,
по которым настоящая раздача отличается от случайного срабатывания.

Чего не делаем: не закрываем бота. Человек должен видеть, за что, и уметь
написать в поддержку — и, если это была ошибка, получить доступ обратно.

Время блокировки берём из самого отчёта, а не из настроек бота: в конфиге
плагина оно меняется в один клик, и зашитое в текст число однажды станет
враньём.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from app.content import texts
from app.content.emoji import e
from app.core.time import fmt, now, parse_dt
from app.domain import torrents as domain

log = logging.getLogger(__name__)

EVENT = 'torrent_blocker.report'
REASON = 'торренты'

# По умолчанию — полчаса: достаточно, чтобы человек заметил и выключил
# клиент, и недостаточно, чтобы день был испорчен.
DEFAULT_FREEZE_MIN = 30


# Лестница помнит неделю: три срабатывания за год — это три разных вечера,
# а не злостный нарушитель.
DEFAULT_WINDOW_DAYS = 7

# Отчёт приходит не по одному: торрент-клиент за сессию даёт их пачкой.
# Окно тишины — чтобы человек получил одно сообщение на одну блокировку,
# а не сорок. Нарушением считается окно, а не отчёт.
DEFAULT_COOLDOWN_MIN = 30


class TorrentGuard:
    def __init__(self, users, settings, sender, bot, moderation, notifier=None):
        self.users = users
        self.settings = settings
        self.sender = sender
        self.bot = bot
        self.moderation = moderation
        self.notifier = notifier

    async def handle(self, data: dict) -> dict:
        if not await self.settings.flag('torrents.enabled'):
            return {'ok': True, 'note': 'disabled'}

        action = ((data.get('report') or {}).get('actionReport') or {})
        node = str((data.get('node') or {}).get('name') or '')
        panel_user = data.get('user') or {}

        user = await self._find_user(panel_user)
        if not user:
            # Не находим — значит подписка не наша (или чужая панель).
            # Строка в лог: молчание здесь неотличимо от «вебхук не дошёл».
            log.warning('торрент: пользователь панели %s не найден в базе',
                        panel_user.get('username') or panel_user.get('uuid'))
            return {'ok': True, 'note': 'user_not_found'}

        user_id = int(self.users.pick(user, 'user_data.user_id') or 0)
        if not user_id:
            return {'ok': True, 'note': 'no_user_id'}

        count = await self._strike(user_id, action, node)
        if count is None:
            return {'ok': True, 'note': 'same_block', 'user_id': user_id}

        step = domain.stage(
            count,
            freeze_at=int(await self.settings.int('torrents.freeze_at') or 0),
            block_at=int(await self.settings.int('torrents.block_at') or 0))

        minutes = int(await self.settings.int('torrents.freeze_min')
                      or DEFAULT_FREEZE_MIN)
        if step == domain.FREEZE and not self.moderation.vpn_locked(user):
            await self.moderation.lock_vpn(
                user_id, f'{REASON}: нарушение №{count}',
                until=now() + timedelta(minutes=minutes))
        elif step == domain.BLOCK and not self.moderation.locked_forever(user):
            await self.moderation.lock_vpn(user_id,
                                           f'{REASON}: нарушение №{count}')

        delivered = await self._tell(user, user_id, count, action, step, minutes)
        await self._tell_admins(user_id, count, action, node, step)

        log.warning('торрент у %s: нарушение %s, нода %s, %s',
                    user_id, count, node or '?', step)
        return {'ok': True, 'user_id': user_id, 'count': count,
                'note': f'{step}_{count}', 'delivered': delivered}

    # ── разморозка ──────────────────────────────────────────────────────────
    async def thaw(self) -> int:
        """Вернуть доступ тем, у кого срок заморозки вышел.

        Счётчик нарушений при этом НЕ обнуляется: заморозка — ступень
        лестницы, а не прощение. Обнуляет его только человек, разобравший
        жалобу.
        """
        thawed = 0
        for user in await self.moderation.expired_locks():
            user_id = int(self.users.pick(user, 'user_data.user_id') or 0)
            if not user_id:
                continue
            await self.moderation.unlock_vpn(user_id, reset=False)
            thawed += 1
            if await self.settings.flag('torrents.warn_user'):
                await self.sender.send(
                    self.bot, user_id,
                    texts.render('torrent.thawed',
                                 name=self.users.pick(user, 'user_data.first_name')))
        if thawed:
            log.info('разморожено подписок после торрентов: %s', thawed)
        return thawed

    # ── жалоба «я ничего не качаю» ──────────────────────────────────────────
    async def appeal(self, user_id: int) -> bool:
        """Человек говорит, что это ошибка. Проверить нечем — зовём человека.

        Содержимого трафика у нас нет и быть не должно, так что решение
        всегда за админом. Наше дело — сложить в карточку то, по чему это
        решение принимается: сколько отчётов, за какой срок, с каких нод.
        """
        user = await self.users.get(user_id)
        stats = (self.users.pick(user or {}, 'moderation.torrent') or {})
        if not stats:
            return False

        await self.users.col.update_one(
            {'user_data.user_id': user_id},
            {'$set': {'moderation.torrent.appealed_at': now()}})

        if self.notifier is None:
            return False
        code, hint = domain.verdict(stats)
        return await self.notifier.torrent_appeal(
            user_id, stats=stats, hint=hint, code=code,
            dm=await self.settings.flag('torrents.dm_admins'),
            ladder=domain.recent(stats.get('strikes'), days=await self.window_days()),
            locked=self.moderation.locked_forever(user))

    async def decide(self, user_id: int, *, trust: bool, admin_id: int = 0) -> bool:
        """Решение по жалобе: сбросить лестницу — или оставить как есть.

        Одобрение не делает человека неприкасаемым: оно обнуляет ступени
        и возвращает доступ. Следующее срабатывание у него снова первое,
        с предупреждения. Так ошибка исправляется, а лазейка «пожаловался
        один раз — качай сколько хочешь» не появляется.

        False — жалобу уже закрыли. Карточка приходит и в чат, и в личку,
        кнопок под ней четыре, и нажать вторую ничего не должно стоить:
        ни лишнего сообщения человеку, ни второй отметки о прощении.
        """
        claimed = await self.users.col.update_one(
            {'user_data.user_id': user_id,
             'moderation.torrent.appealed_at': {'$exists': True}},
            {'$unset': {'moderation.torrent.appealed_at': ''}})
        if getattr(claimed, 'modified_count', 0) != 1:
            return False

        if trust:
            await self.moderation.unlock_vpn(user_id, admin_id=admin_id)
            await self.users.col.update_one(
                {'user_data.user_id': user_id},
                {'$set': {'moderation.torrent.forgiven_at': now(),
                          'moderation.torrent.forgiven_by': int(admin_id or 0)},
                 '$inc': {'moderation.torrent.forgiven': 1}})

        await self.sender.send(
            self.bot, user_id,
            texts.render('torrent.appeal_ok' if trust else 'torrent.appeal_no',
                         support_url=await self.settings.get('link.support')))
        return True

    # ── счёт нарушений ──────────────────────────────────────────────────────
    async def _strike(self, user_id: int, action: dict, node: str) -> int | None:
        """Засчитать нарушение. None — это та же блокировка, что и минуту назад.

        Окно закрывается атомарно самим update: панель ретраит вебхуки, а
        торрент-клиент даёт пачку отчётов, и оба случая должны стоить
        человеку ровно одного сообщения.
        """
        window = int(await self.settings.int('torrents.cooldown_min')
                     or DEFAULT_COOLDOWN_MIN)
        border = now() - timedelta(minutes=window)

        claimed = await self.users.col.update_one(
            {'user_data.user_id': user_id,
             '$or': [{'moderation.torrent.last_at': {'$lt': border}},
                     {'moderation.torrent.last_at': None},
                     {'moderation.torrent.last_at': {'$exists': False}}]},
            {'$inc': {'moderation.torrent.count': 1,
                      'moderation.torrent.reports': 1},
             '$set': {'moderation.torrent.last_at': now(),
                      'moderation.torrent.last_ip': str(action.get('ip') or ''),
                      'moderation.torrent.last_node': node},
             # Отметки времени, а не только счётчик: лестница смотрит на
             # последнюю неделю, и без дат отличить «три раза за вечер» от
             # «три раза за год» нечем.
             '$push': {'moderation.torrent.strikes': {
                 '$each': [now()], '$slice': -domain.KEEP_STRIKES}},
             # Ноды копим списком: раздача с двух серверов за вечер — это
             # уже не «сосед по вайфаю», это его клиент.
             '$addToSet': {'moderation.torrent.nodes': node}})

        if getattr(claimed, 'modified_count', 0) != 1:
            # Отчёт всё равно считаем: по этому числу видно, качает человек
            # постоянно или один раз забыл выключить клиент.
            await self.users.col.update_one(
                {'user_data.user_id': user_id},
                {'$inc': {'moderation.torrent.reports': 1}})
            return None

        fresh = await self.users.get(user_id, {'moderation.torrent': 1})
        return domain.recent(
            self.users.pick(fresh or {}, 'moderation.torrent.strikes'),
            days=await self.window_days())

    async def window_days(self) -> int:
        value = await self.settings.int('torrents.window_days')
        return DEFAULT_WINDOW_DAYS if value is None else int(value)

    async def window_text(self) -> str:
        """«за 7 дней» — готовой фразой, а не числом: при выключенном окне
        в тексте получилось бы «за 0 дней»."""
        days = await self.window_days()
        return f'за {days} дн.' if days else 'за всё время' 

    # ── разговор ────────────────────────────────────────────────────────────
    async def _tell(self, user: dict, user_id: int, count: int, action: dict,
                    step: str, freeze_min: int) -> bool:
        if not await self.settings.flag('torrents.warn_user'):
            return False

        seconds = int(action.get('blockDuration') or 0)
        text = texts.render(
            f'torrent.{step}',
            name=self.users.pick(user, 'user_data.first_name'),
            # Сколько закрыт адрес — из самого отчёта: в конфиге плагина это
            # число меняется в один клик, и зашитое в текст станет враньём.
            minutes=max(1, round(seconds / 60)) if seconds else '',
            until=fmt(parse_dt(action.get('willUnblockAt')), '%H:%M'),
            freeze=freeze_min,
            count=count,
            window=await self.window_text(),
            support_url=await self.settings.get('link.support'),
        )
        return await self.sender.send(self.bot, user_id, text,
                                      await self._appeal_button())

    async def _appeal_button(self):
        """«Это ошибка» — под каждым сообщением, начиная с первого.

        Раньше кнопки: человек, у которого срабатывает ложно, должен иметь
        возможность сказать об этом до того, как ему отключат подписку.
        """
        if not await self.settings.flag('torrents.appeal'):
            return None

        from aiogram.utils.keyboard import InlineKeyboardBuilder

        from app.bot.callbacks import Torrent

        kb = InlineKeyboardBuilder()
        kb.button(text=f'{e("question")} Я не качаю торренты',
                  callback_data=Torrent(action='appeal').pack())
        return kb.as_markup()

    async def _tell_admins(self, user_id: int, count: int, action: dict,
                           node: str, step: str) -> None:
        if self.notifier is None:
            return
        try:
            await self.notifier.torrent(user_id, count=count, node=node,
                                        ip=str(action.get('ip') or ''),
                                        step=step,
                                        dm=await self.settings.flag(
                                            'torrents.dm_admins'))
        except Exception as exc:      # noqa: BLE001 — уведомление не главное
            log.warning('торрент: админ-уведомление не ушло: %s', exc)

    # ── поиск человека ──────────────────────────────────────────────────────
    async def _find_user(self, panel_user: dict) -> dict | None:
        """У нас имя пользователя в панели — это telegram id строкой.

        Поэтому сначала по нему, а потом по всему остальному, что панель
        прислала: у ByPass-подписки имя другое (`<id>_bypass`), а у старых
        записей могло не быть telegramId.
        """
        for field, value in (
            ('user_data.user_id', self._as_id(panel_user.get('username'))),
            ('user_data.user_id', self._as_id(panel_user.get('telegramId'))),
            ('vpn.uuid', panel_user.get('uuid')),
            ('vpn.bypass_uuid', panel_user.get('uuid')),
            ('vpn.shortUuid', panel_user.get('shortUuid')),
            ('vpn.bypass_shortUuid', panel_user.get('shortUuid')),
        ):
            if value:
                found = await self.users.col.find_one({field: value})
                if found:
                    return found
        return None

    @staticmethod
    def _as_id(value) -> int | None:
        """'802421217' и '802421217_bypass' — один и тот же человек."""
        raw = str(value or '').strip().split('_')[0]
        try:
            return int(raw) or None
        except (TypeError, ValueError):
            return None
