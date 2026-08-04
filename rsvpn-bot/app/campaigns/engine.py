"""Один движок для всех кампаний.

Сейчас campaigns.py, campaigns_expired.py и сampaigns_trial.py содержат по
своей копии: safe_send, _is_good_hour, _notify_admin, _days_since, цикл по
курсору, установка флага, счётчики. Отличаются они только запросом, окном
времени и текстом — то есть данными.

Здесь: шаг кампании описывается объектом CampaignStep (см. definitions.py),
а движок один. Добавить касание = добавить строку в таблицу.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from app.core.time import days_since, hours_since, now
from app.repositories.users import UsersRepository

log = logging.getLogger(__name__)

# Функция текста получает документ пользователя и контекст расчётов
TextFn = Callable[[dict, 'StepContext'], str]


@dataclass(frozen=True)
class StepContext:
    """Что известно движку о конкретной отправке."""
    name: str | None
    balance: int
    credited: int
    topups_count: int

    @property
    def is_multi(self) -> bool:
        return self.topups_count >= 2

    @property
    def total_balance(self) -> int:
        return self.balance + self.credited


@dataclass(frozen=True)
class CampaignStep:
    code: str                       # уникальный ключ, он же флаг в campaigns.*
    title: str                      # для отчёта админам
    query: dict                     # фильтр по пользователям
    text: TextFn                    # функция текста
    keyboard: str = 'topup'         # ключ клавиатуры из KEYBOARDS
    settings_key: str | None = None  # тумблер в админке; None — всегда включено

    # окно отправки относительно поля с датой
    since_field: str = 'growth.joined_at'
    min_hours: float | None = None
    max_hours: float | None = None
    respect_night: bool = True      # False для HOT-касаний: шлём в любое время

    # цепочка касаний
    after_step: str | None = None   # код предыдущего шага
    delay_days: float = 0           # минимальная пауза после него

    # деньги
    credit_to: int = 0              # добить баланс до суммы (если баланс < 4)
    bonus: int = 0                  # фиксированное начисление

    def flag(self) -> str:
        return f'{self.code}_sent'


@dataclass
class StepReport:
    step: str
    matched: int = 0
    sent: int = 0
    failed: int = 0
    credited: int = 0
    skipped_window: int = 0
    skipped_claimed: int = 0

    def as_text(self) -> str:
        return (f'📨 <b>{self.step}</b>\n'
                f'✅ Отправлено: <code>{self.sent}</code>\n'
                f'⛔️ Не доставлено: <code>{self.failed}</code>\n'
                f'💰 Начислено: <code>{self.credited}₽</code>')


@dataclass
class CampaignReport:
    steps: list[StepReport] = field(default_factory=list)

    @property
    def sent(self) -> int:
        return sum(s.sent for s in self.steps)

    @property
    def credited(self) -> int:
        return sum(s.credited for s in self.steps)


class CampaignEngine:
    """Прогоняет шаги кампаний. Ничего не знает про конкретные тексты и сегменты."""

    def __init__(self, bot: Bot, users: UsersRepository, settings, sender,
                 keyboards: dict[str, Callable[[], InlineKeyboardMarkup]],
                 runs_collection=None, pause_between: float = 0.05):
        self.bot = bot
        self.users = users
        self.settings = settings
        self.sender = sender
        self.keyboards = keyboards
        self.runs = runs_collection
        self.pause = pause_between

    async def run(self, steps: list[CampaignStep]) -> CampaignReport:
        report = CampaignReport()
        for step in steps:
            if step.settings_key and not await self.settings.flag(step.settings_key):
                log.info('[%s] пропущен: выключен в админке', step.code)
                continue
            report.steps.append(await self.run_step(step))
        return report

    async def run_step(self, step: CampaignStep) -> StepReport:
        report = StepReport(step=step.title)
        query = dict(step.query)
        query[f'campaigns.{step.flag()}'] = {'$exists': False}
        if step.after_step:
            query[f'campaigns.{step.after_step}_sent'] = {'$exists': True}

        night = step.respect_night and not await self._is_good_hour()

        async for user in self.users.iterate(query):
            report.matched += 1

            if not self._in_window(user, step, night):
                report.skipped_window += 1
                continue

            user_id = self.users.pick(user, 'user_data.user_id')
            if not user_id:
                continue

            balance = int(self.users.pick(user, 'growth.balance', 0) or 0)
            credited = self._credit_amount(step, balance)

            # Слот занимаем ДО отправки и вместе с начислением — одним запросом.
            # Иначе при падении между отправкой и записью флага пользователь
            # получит сообщение и бонус повторно на следующем прогоне.
            if not await self.users.claim_campaign_slot(
                user['_id'], step.flag(), self._money_update(credited)
            ):
                report.skipped_claimed += 1
                continue

            report.credited += credited
            context = StepContext(
                name=self.users.pick(user, 'user_data.first_name'),
                balance=balance,
                credited=credited,
                topups_count=int(self.users.pick(user, 'growth.topups_count', 0) or 0),
            )

            markup = self.keyboards.get(step.keyboard)
            ok = await self.sender.send(
                self.bot, user_id, step.text(user, context),
                markup() if markup else None,
            )
            if ok:
                report.sent += 1
            else:
                report.failed += 1

            await asyncio.sleep(self.pause)

        if self.runs is not None and report.matched:
            await self.runs.insert_one({
                'step': step.code, 'created_at': now(),
                'matched': report.matched, 'sent': report.sent,
                'failed': report.failed, 'credited': report.credited,
            })

        log.info('[%s] найдено=%s отправлено=%s ошибок=%s начислено=%s₽',
                 step.code, report.matched, report.sent, report.failed, report.credited)
        return report

    # ── внутреннее ──────────────────────────────────────────────────────────
    def _in_window(self, user: dict, step: CampaignStep, night: bool) -> bool:
        if step.after_step and step.delay_days:
            previous = self.users.pick(user, f'campaigns.{step.after_step}_sent')
            if previous and days_since(previous) < step.delay_days:
                return False

        if step.min_hours is None and step.max_hours is None:
            return not night

        since = self.users.pick(user, step.since_field)
        if not since:
            return False

        passed = hours_since(since)
        if step.min_hours is not None and passed < step.min_hours:
            return False
        if step.max_hours is not None and passed > step.max_hours:
            return False
        return not night

    @staticmethod
    def _credit_amount(step: CampaignStep, balance: int) -> int:
        if step.bonus:
            return step.bonus
        # добиваем до целевой суммы только тем, у кого баланса реально нет
        if step.credit_to and balance < 4:
            return max(0, step.credit_to - balance)
        return 0

    @staticmethod
    def _money_update(credited: int) -> dict | None:
        if credited <= 0:
            return None
        return {
            '$inc': {'info.balance': credited},
            '$push': {'info.transactions': {
                'amount': credited, 'dt': now(), 'description': 'Бонус за возвращение',
            }},
        }

    async def _is_good_hour(self) -> bool:
        start = await self.settings.int('campaign.hour_from')
        end = await self.settings.int('campaign.hour_to')
        return start <= now().hour <= end
