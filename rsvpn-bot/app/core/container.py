"""Контейнер зависимостей — единственное место, где всё связывается.

Ни один модуль не импортирует другой «через глобальную переменную» (как было
с `from loader import users, bot`). Всё собирается здесь и передаётся дальше
явно: боту — через middleware, FastAPI — через app.state.container, тестам —
через подмену коллекций на заглушки.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core import db as names
from app.core.config import Config
from app.repositories.payments import PaymentsRepository
from app.repositories.plans import PlansRepository
from app.repositories.users import UsersRepository
from app.settings.service import SettingsService

log = logging.getLogger(__name__)


@dataclass
class Container:
    config: Config
    db: Any

    users: UsersRepository = field(init=False)
    plans: PlansRepository = field(init=False)
    payments_repo: PaymentsRepository = field(init=False)
    settings: SettingsService = field(init=False)

    # заполняются в build(): требуют http-клиента и бота
    vpn: Any = None
    payments: Any = None
    topup: Any = None
    billing: Any = None
    notifier: Any = None
    analytics: Any = None
    expiry: Any = None
    squads: Any = None
    lifeline: Any = None
    renewal: Any = None
    device_billing: Any = None
    promo: Any = None
    gifts: Any = None
    payouts: Any = None
    survey: Any = None
    devices: Any = None
    entities: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        from app.services.payouts import PayoutService
        from app.services.promo import PromoService
        from app.services.survey import SurveyService

        self.users = UsersRepository(self.db[names.USERS])
        self.plans = PlansRepository(self.db[names.PLANS])
        self.payments_repo = PaymentsRepository(self.db[names.PAYMENTS])
        self.settings = SettingsService(
            self.db[names.BOT_SETTINGS], self.db[names.SETTINGS_AUDIT])

        # сервисы без внешних зависимостей доступны сразу, в том числе в тестах
        self.promo = PromoService(self.users, self.db[names.PROMO_CODES],
                                  self.db[names.PROMO_USAGES], self.settings)
        self.payouts = PayoutService(self.users, self.settings)
        self.survey = SurveyService(self.users, self.db['survey_bonus'], self.settings)

    # ── медиа ───────────────────────────────────────────────────────────────
    def media(self, key: str) -> str | None:
        """Путь к картинке экрана. Нет файла — экран отправится текстом."""
        path = Path(self.config.media_dir) / f'{key}.png'
        return str(path) if path.exists() else None

    # ── контент ─────────────────────────────────────────────────────────────
    async def notify(self, bot, topic_key: str, text: str) -> None:
        """Уведомление в админ-чат. Номера тем — настройки, а не числа в коде."""
        if not await self.settings.flag('notify.enabled'):
            return
        try:
            await bot.send_message(
                chat_id=await self.settings.int('notify.chat_id'),
                message_thread_id=await self.settings.int(f'notify.topic_{topic_key}') or None,
                text=text,
            )
        except Exception as exc:  # уведомление не должно ломать основной сценарий
            log.warning('админ-уведомление не отправлено: %s', exc)

    async def reload_texts(self) -> None:
        from app.content import texts
        docs = await self.db[names.CONTENT_OVERRIDES].find({}).to_list(length=None)
        texts.set_overrides({d['_id']: d.get('value', '') for d in docs if d.get('value')})

    # ── старт ───────────────────────────────────────────────────────────────
    async def startup(self) -> None:
        """Индексы, сиды, прогрев кэшей. Идемпотентно — можно вызывать всегда."""
        for repo in (self.users, self.plans, self.payments_repo):
            await repo.ensure_indexes()
        for service in (self.promo, self.survey):
            await service.ensure_indexes()
        if self.gifts:
            await self.gifts.ensure_indexes()
        await self.plans.seed()
        await self.reload_texts()
        log.info('контейнер готов: тарифов=%s', len(await self.plans.all(only_enabled=False)))

    @classmethod
    def build(cls, config: Config | None = None, db=None) -> 'Container':
        from motor.motor_asyncio import AsyncIOMotorClient

        from app.admin.entities import build_entities
        from app.integrations.payments.registry import PaymentRegistry, build_providers
        from app.integrations.vpn.remnawave import RemnawaveClient
        from app.services.billing import BillingService
        from app.services.topup import TopupService

        config = config or Config.from_env()
        if db is None:
            db = AsyncIOMotorClient(config.mongo_uri, tz_aware=True)[config.mongo_db]

        container = cls(config=config, db=db)

        import httpx
        http = httpx.AsyncClient(timeout=20)

        from app.services.squads import SquadService
        squads = SquadService(container.settings, container.db['settings_collection'],
                              container.db[names.FINGERPRINTS])
        container.squads = squads
        container.vpn = RemnawaveClient(config.vpn.base_url, config.vpn.token, http,
                                        container.settings, squads,
                                        dry_run=config.vpn.dry_run)
        container.payments = PaymentRegistry(build_providers(config, http), container.settings)
        container.topup = TopupService(
            container.users, container.payments_repo, container.settings, container=container)
        container.billing = BillingService(
            container.users, container.plans, container.settings,
            container.vpn, container.topup)
        from app.services.gifts import GiftService
        container.gifts = GiftService(container.users, container.db[names.GIFTS],
                                      container.plans, container.settings, container.vpn)
        container.promo.vpn = container.vpn
        container.entities = build_entities(container)
        return container

    def attach_bot(self, bot) -> None:
        """Сервисы, которым нужен Bot: вебхуки шлют сообщения пользователям."""
        from app.bot.keyboards.common import campaign_keyboards
        from app.campaigns.sender import Sender
        from app.services.expiry import ExpiryNotifier
        from app.services.lifeline import LifelineService

        from app.services.devices import DeviceBillingService
        from app.services.renewal import RenewalService

        self.lifeline = LifelineService(self.users, self.settings, self.vpn)
        self.expiry = ExpiryNotifier(self.users, self.settings, Sender(), bot,
                                     campaign_keyboards(), self.lifeline)
        self.renewal = RenewalService(self.users, self.plans, self.settings, self.vpn,
                                      self.topup, self.lifeline, self.expiry)
        self.device_billing = DeviceBillingService(self.users, self.settings, self.vpn)
        self.devices = self.device_billing
