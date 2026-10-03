"""Сторож: лично в Telegram, когда бот перестал доставать до базы.

25 сентября Mongo стала недоступна, и узнали мы об этом не от бота, а от
людей — через несколько часов. Всё, что у бота было для таких случаев,
писалось в админ-чат через `notify.chat_id`, а этот номер лежит… в базе.
То есть ровно в том случае, когда он нужен, прочитать его нельзя.

Поэтому сторож устроен так, чтобы работать без базы вообще:

* кому писать — берётся из `ADMIN_IDS` в .env, а не из настроек;
* писать или нет — решается по состоянию в памяти процесса, а не по
  отметке в коллекции;
* сам пинг идёт с коротким таймаутом: висящая проверка — это не проверка.

Сообщений ровно два на аварию: «упало» и «поднялось». Плюс напоминание
раз в полчаса, пока лежит, — чтобы ночную аварию не проспать, но и не
получить двести сообщений подряд.
"""

from __future__ import annotations

import asyncio
import logging

from app.content.emoji import e, plain
from app.core.time import fmt, now

log = logging.getLogger(__name__)

# Сколько ждать ответа на ping. База отвечает за миллисекунды; всё, что
# дольше пяти секунд, для бота уже недоступность.
PING_TIMEOUT_SEC = 5

# Как часто напоминать, пока авария продолжается.
REMIND_MINUTES = 30

# Сколько подряд неудачных проверок нужно, чтобы поднять тревогу. Одна
# может быть морганием сети, и будить из-за неё незачем.
FAILS_BEFORE_ALARM = 2


class Watchdog:
    """Проверяет базу и пишет админам лично. Состояние — в памяти."""

    def __init__(self, db, bot, admin_ids, name: str = 'база'):
        self.db = db
        self.bot = bot
        self.admin_ids = [int(item) for item in (admin_ids or [])]
        self.name = name

        self.fails = 0
        self.down = False
        self.since = None
        self.last_told = None

    async def alive(self) -> tuple[bool, str]:
        """Отвечает ли база. Возвращает (жива, что не так)."""
        try:
            await asyncio.wait_for(self.db.command('ping'),
                                   timeout=PING_TIMEOUT_SEC)
            return True, ''
        except asyncio.TimeoutError:
            return False, f'не ответила за {PING_TIMEOUT_SEC} секунд'
        except Exception as exc:      # noqa: BLE001 — причина нужна целиком
            return False, str(exc)[:200]

    async def check(self) -> bool:
        """Один проход. Возвращает, жива ли база."""
        ok, why = await self.alive()

        if ok:
            if self.down:
                await self._tell(self._recovered())
                log.warning('%s снова отвечает', self.name)
            self.fails = 0
            self.down = False
            self.since = None
            self.last_told = None
            return True

        self.fails += 1
        log.error('%s не отвечает (%s-я проверка подряд): %s',
                  self.name, self.fails, why)

        if not self.down and self.fails >= FAILS_BEFORE_ALARM:
            self.down = True
            self.since = now()
            await self._tell(self._alarm(why))
        elif self.down and self._time_to_remind():
            await self._tell(self._reminder(why))
        return False

    # ── тексты ──────────────────────────────────────────────────────────────
    def _alarm(self, why: str) -> str:
        return (f'{e("attention")} <b>Бот не достаёт до базы</b>\n\n'
                f'<code>{why}</code>\n\n'
                f'Люди сейчас видят «база не отвечает» вместо экранов. '
                f'Подписки при этом работают: VPN живёт в панели, а не '
                f'в базе.\n\n'
                f'<blockquote>Что смотреть на сервере:\n'
                f'<code>cd /home/rsvpn-bot && ./scripts/why.sh</code>\n\n'
                f'Чаще всего это сама база (упала, кончилось место) или '
                f'сеть между ней и ботом.</blockquote>')

    def _reminder(self, why: str) -> str:
        minutes = int((now() - self.since).total_seconds() // 60) if self.since else 0
        return (f'{e("attention")} <b>База не отвечает уже {minutes} мин.</b>\n'
                f'<code>{why}</code>')

    def _recovered(self) -> str:
        started = fmt(self.since) if self.since else '—'
        minutes = int((now() - self.since).total_seconds() // 60) if self.since else 0
        return (f'{e("ok")} <b>База снова отвечает</b>\n\n'
                f'Лежала {minutes} мин., с {started}.\n\n'
                f'<blockquote>Оплаты, которые не зачислились за это время, '
                f'покажет <code>/outage</code> — поимённо, с суммами.</blockquote>')

    def _time_to_remind(self) -> bool:
        if self.last_told is None:
            return True
        return (now() - self.last_told).total_seconds() >= REMIND_MINUTES * 60

    # ── отправка ────────────────────────────────────────────────────────────
    async def _tell(self, text: str) -> None:
        """Лично каждому админу. Ни базы, ни настроек здесь не касаемся."""
        self.last_told = now()
        if self.bot is None:
            return

        for admin_id in self.admin_ids:
            try:
                with plain():      # кастомные значки Telegram иногда отклоняет
                    await self.bot.send_message(admin_id, text)
            except Exception as exc:      # noqa: BLE001
                log.warning('не удалось написать админу %s: %s', admin_id, exc)
