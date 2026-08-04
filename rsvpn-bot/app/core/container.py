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
    entities: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.users = UsersRepository(self.db[names.USERS])
        self.plans = PlansRepository(self.db[names.PLANS])
        self.payments_repo = PaymentsRepository(self.db[names.PAYMENTS])
        self.settings = SettingsService(
            self.db[names.BOT_SETTINGS], self.db[names.SETTINGS_AUDIT])

    # ── медиа ───────────────────────────────────────────────────────────────
    def media(self, key: str) -> str | None:
        """Путь к картинке экрана. Нет файла — экран отправится текстом."""
        path = Path(self.config.media_dir) / f'{key}.png'
        return str(path) if path.exists() else None

    # ── контент ─────────────────────────────────────────────────────────────
    async def reload_texts(self) -> None:
        from app.content import texts
        docs = await self.db[names.CONTENT_OVERRIDES].find({}).to_list(length=None)
        texts.set_overrides({d['_id']: d.get('value', '') for d in docs if d.get('value')})

    # ── старт ───────────────────────────────────────────────────────────────
    async def startup(self) -> None:
        """Индексы, сиды, прогрев кэшей. Идемпотентно — можно вызывать всегда."""
        for repo in (self.users, self.plans, self.payments_repo):
            await repo.ensure_indexes()
        await self.plans.seed()
        await self.reload_texts()
        log.info('контейнер готов: тарифов=%s', len(await self.plans.all(only_enabled=False)))

    @classmethod
    def build(cls, config: Config | None = None, db=None) -> 'Container':
        from motor.motor_asyncio import AsyncIOMotorClient

        from app.admin.entities import build_entities
        from app.integrations.payments.cardlink import CardlinkProvider
        from app.integrations.payments.registry import PaymentRegistry
        from app.integrations.vpn.remnawave import RemnawaveClient
        from app.services.billing import BillingService
        from app.services.topup import TopupService

        config = config or Config.from_env()
        if db is None:
            db = AsyncIOMotorClient(config.mongo_uri, tz_aware=True)[config.mongo_db]

        container = cls(config=config, db=db)

        import httpx
        http = httpx.AsyncClient(timeout=20)

        container.vpn = RemnawaveClient(
            config.vpn.base_url, config.vpn.token, http, config.vpn.base_squad_id)
        container.payments = PaymentRegistry(
            [CardlinkProvider(config.payments.cardlink_token, http=http)],
            container.settings,
        )
        container.topup = TopupService(
            container.users, container.payments_repo, container.settings)
        container.billing = BillingService(
            container.users, container.plans, container.settings,
            container.vpn, container.topup)
        container.entities = build_entities(container)
        return container
