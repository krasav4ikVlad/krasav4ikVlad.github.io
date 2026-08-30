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
    media_cache: Any = field(init=False)
    stats: Any = field(init=False)

    # заполняются в build(): требуют http-клиента и бота
    vpn: Any = None
    payments: Any = None
    topup: Any = None
    billing: Any = None
    notifier: Any = None
    analytics: Any = None
    expiry: Any = None
    private: Any = None
    squads: Any = None
    lifeline: Any = None
    renewal: Any = None
    device_billing: Any = None
    promo: Any = None
    gifts: Any = None
    payouts: Any = None
    survey: Any = None
    devices: Any = None
    links: Any = None
    trial: Any = None
    moderation: Any = None
    discounts: Any = None
    wipe: Any = None
    audiences: Any = None
    health: Any = None
    entities: dict = field(default_factory=dict)

    def collection(self, name: str):
        """Коллекция с учётом LEGACY_COLLECTIONS: см. app/core/db.collection_name."""
        return self.db[names.collection_name(name, self.config.legacy_collections)]

    def __post_init__(self) -> None:
        from app.services.payouts import PayoutService
        from app.services.promo import PromoService
        from app.services.survey import SurveyService

        self.users = UsersRepository(self.db[names.USERS])
        self.plans = PlansRepository(self.db[names.PLANS])
        self.payments_repo = PaymentsRepository(self.db[names.PAYMENTS])
        self.settings = SettingsService(
            self.db[names.BOT_SETTINGS], self.db[names.SETTINGS_AUDIT],
            on_change=self.apply_setting)

        from app.content.media import MediaCache
        self.media_cache = MediaCache(self.db[names.MEDIA_CACHE])

        # сервисы без внешних зависимостей доступны сразу, в том числе в тестах
        self.promo = PromoService(self.users, self.collection(names.PROMO_CODES),
                                  self.collection(names.PROMO_USAGES), self.settings)
        self.payouts = PayoutService(self.users, self.settings)

        from app.admin.stats import StatsService
        self.stats = StatsService(self.users, self.config.admin_ids)

        from app.admin.audiences import AudienceCounts
        self.audiences = AudienceCounts(self.users)

        from app.admin.health import HealthLog
        self.health = HealthLog(self.db[names.JOB_RUNS])

        from app.services.moderation import ModerationService
        self.moderation = ModerationService(self.users, self.settings, vpn=None)

        from app.services.discounts import DiscountService
        self.discounts = DiscountService(self.settings)

        # Журнал движения денег: панели операторов нужна вся история, а не
        # последние 500 записей в документе пользователя. Ставится на
        # репозиторий, потому что баланс двигают пять разных мест, и все они
        # ходят через users.
        from app.repositories.balance_log import BalanceLogRepository
        self.balance_log = BalanceLogRepository(self.db[names.BALANCE_LOG])
        self.users.journal = self.balance_log

        from app.repositories.private_servers import PrivateServersRepository
        from app.services.private_servers import PrivateServerService
        # vpn и bot проставляются позже: покупка сервера возможна и без них,
        # а выдача доступа — нет
        from app.repositories.server_pool import ServerPoolRepository
        self.private = PrivateServerService(
            self.users, PrivateServersRepository(self.db[names.PRIVATE_SERVERS]),
            self.settings, vpn=None,
            pool=ServerPoolRepository(self.db[names.SERVER_POOL]))

        from app.services.wipe import WipeService
        # vpn проставляется в build(): без панели чистится только база
        self.wipe = WipeService(self.users, self.db, vpn=None,
                                legacy=self.config.legacy_collections)

        from app.services.trial import TrialService
        # plans нужен, чтобы записать в vpn.period существующий тариф:
        # длина бесплатного периода тарифом не является, и автопродление
        # по ней ничего не находило
        self.trial = TrialService(self.users, self.settings, vpn=None, plans=self.plans)
        # именно collection(): в старой базе коллекция называется
        # 'churn_surveys ' — с пробелом на конце, см. app/core/db.py
        self.survey = SurveyService(self.users,
                                    self.collection(names.CHURN_SURVEYS),
                                    self.settings)

    # ── медиа ───────────────────────────────────────────────────────────────
    MEDIA_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp')

    # Имена файлов из старого проекта: картинки можно просто скопировать
    # в media/ как есть, не переименовывая.
    MEDIA_ALIASES = {
        'profile': ('new_profile',),
        'subscription': ('new_type_sub',),
        'subscription_active': ('new_your_sub',),
        'subscription_creating': ('new_sub_creating',),
        'subscription_extended': ('new_sub_extended',),
        'subscription_expired': ('new_sub_was_expire',),
        'no_funds': ('new_no_funds',),
        'devices': ('new_limit_devices',),
        'devices_list': ('new_your_devices',),
        'devices_added': ('new_devices_added',),
        'payment': ('new_top_up', 'new_edit_top_up'),
        'payment_created': ('new_payment_created',),
        'balance_added': ('new_balance_added',),
        'referrals': ('new_referrals',),
        'gifts': ('new_gifts',),
        'gift_accepted': ('new_gift_accepted',),
        'bypass': ('new_your_bypass_sub', 'new_by_gb'),
        'bypass_buying': ('new_gb_buying',),
        'email': ('new_email',),
        'duration': ('new_duration',),
        'error': ('new_error',),
    }

    def media_path(self, key: str) -> str | None:
        """Путь к картинке экрана. Нет файла — экран отправится текстом.

        Ищутся: media/<ключ>.<png|jpg|jpeg|webp>, затем имена из старого
        проекта (MEDIA_ALIASES) — чтобы картинки работали сразу после копирования.
        """
        folder = Path(self.config.media_dir)
        for name in (key, *self.MEDIA_ALIASES.get(key, ())):
            for extension in self.MEDIA_EXTENSIONS:
                path = folder / f'{name}{extension}'
                if path.exists():
                    return str(path)
        return None

    def media(self, key: str):
        """Картинка экрана для Screen(image=...).

        Возвращает Photo, а не путь: он умеет отдать уже загруженный в Telegram
        file_id вместо файла. Без этого бот заливал PNG заново на каждое
        нажатие кнопки — отсюда и пауза перед обновлением сообщения.
        """
        from app.content.media import Photo, bot_scope, file_token

        path = self.media_path(key)
        if not path:
            return None
        # номер бота в ключе: file_id чужого бота Telegram не примет
        token = file_token(path, bot_scope(self.config.bot_token))
        return Photo(path=path, token=token, cache=self.media_cache)

    # ── контент ─────────────────────────────────────────────────────────────
    async def notify(self, bot, topic_key: str, text: str) -> None:
        """Уведомление в админ-чат. Номера тем — настройки, а не числа в коде."""
        if not await self.settings.flag('notify.enabled'):
            return
        try:
            from app.content.emoji import plain
            with plain():      # админ-чат — на обычных значках, как и админка
                await bot.send_message(
                    chat_id=await self.settings.int('notify.chat_id'),
                    message_thread_id=await self.settings.int(f'notify.topic_{topic_key}') or None,
                    text=text,
                )
        except Exception as exc:  # уведомление не должно ломать основной сценарий
            log.warning('админ-уведомление не отправлено: %s', exc)

    async def apply_setting(self, key: str, value) -> None:
        """Настройка изменилась из админки — применить её к процессу.

        Всё, что читается через settings.get(), подхватится само: кэш
        сброшен. Здесь только то, что вдобавок лежит в памяти.

        Процессов два (бот и вебхуки), и память у них своя. Для эмодзи это
        неважно: вебхуки отправляют считанные уведомления. Если такого
        станет больше — сюда придёт общий сигнал через базу.
        """
        if key == 'content.custom_emoji':
            from app.content import emoji
            emoji.set_enabled(bool(value))

    async def reload_texts(self) -> None:
        from app.content import emoji, texts
        docs = await self.db[names.CONTENT_OVERRIDES].find({}).to_list(length=None)
        texts.set_overrides({d['_id']: d.get('value', '') for d in docs if d.get('value')})
        emoji.set_enabled(await self.settings.flag('content.custom_emoji'))

    async def warn_about_squads(self) -> list[str]:
        """Сквад с опечаткой — это отказ панели в момент покупки.

        Деньги в этом случае возвращаются (см. BillingService), но подписки
        человек не получает, а в логах остаётся только 400 от панели. Строка
        при старте называет виноватую настройку прямым текстом.
        """
        from app.services.squads import SquadService

        # Container.build() уже собрал сервис; без него (тесты, миграции)
        # хватит настроек — problems() в состояние не ходит.
        service = self.squads or SquadService(self.settings, None)
        try:
            broken = await service.problems()
        except Exception:      # проверка не должна мешать старту
            return []

        if broken:
            log.warning('сквады не похожи на UUID, панель их не примет: %s',
                        '; '.join(broken))
        return broken

    async def warn_about_legacy_leftovers(self) -> list[str]:
        """Данные остались в коллекции с пробелом, а бот смотрит в чистую.

        Ровно так «пропадают» промокоды и быстрые ответы после переименования:
        бот молча читает пустую коллекцию, и понять это можно только по
        жалобам пользователей. Одна строка в логе при старте дешевле.
        """
        if self.config.legacy_collections:
            return []

        stranded = []
        for old_name, new_name in names.LEGACY_RENAMES.items():
            try:
                in_old = await self.db[old_name].count_documents({}, limit=1)
                in_new = await self.db[new_name].count_documents({}, limit=1)
            except Exception:      # прав на пересчёт может не быть — не мешаем старту
                continue
            if in_old and not in_new:
                stranded.append(old_name)

        if stranded:
            log.warning('данные остались в старых коллекциях: %s. Бот читает '
                        'имена без пробела и их не увидит — переименуйте '
                        'коллекции или поставьте LEGACY_COLLECTIONS=1',
                        ', '.join(repr(name) for name in stranded))
        return stranded

    # ── старт ───────────────────────────────────────────────────────────────
    async def startup(self, strict: bool = False) -> None:
        """Индексы, сиды, прогрев кэшей. Идемпотентно — можно вызывать всегда.

        strict=True — не проглатывать неудачу с индексами. Нужно миграции:
        иначе она запишется как выполненная, хотя индекс не создан, и повторный
        запуск скажет «уже применена». Боту наоборот важно подняться и работать,
        поэтому у него strict=False.
        """
        for repo in (self.users, self.plans, self.payments_repo,
                     self.private.servers, self.private.pool, self.balance_log):
            await repo.ensure_indexes()
        for service in (self.promo, self.survey):
            await service.ensure_indexes()
        if self.gifts:
            await self.gifts.ensure_indexes()
        await self.plans.seed()
        await self.reload_texts()
        await self.media_cache.load()
        if self.payments is not None:
            await self.payments.configure()

        await self.warn_about_legacy_leftovers()
        await self.warn_about_squads()

        failed = [name for repo in (self.users, self.plans, self.payments_repo,
                                    self.private.servers, self.private.pool)
                  for name in repo.failed_indexes]
        if failed and strict:
            raise RuntimeError('не созданы индексы: ' + ', '.join(failed))

        log.info('контейнер готов: тарифов=%s%s',
                 len(await self.plans.all(only_enabled=False)),
                 f', проблемных индексов: {len(failed)}' if failed else '')

    @classmethod
    def build(cls, config: Config | None = None, db=None) -> 'Container':
        from motor.motor_asyncio import AsyncIOMotorClient

        from app.admin.entities import build_entities
        from app.integrations.payments.registry import PaymentRegistry, build_providers
        from app.integrations.vpn.links import LinkEncryptor
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
        container.links = LinkEncryptor(
            http, rsa_public_key=config.vpn.happ_rsa_public_key,
            incy_script=config.vpn.incy_script, incy_cwd=config.vpn.incy_cwd)
        container.payments = PaymentRegistry(
            build_providers(config, http, container.db[names.CARDLINK_BILLS]),
            container.settings)
        container.topup = TopupService(
            container.users, container.payments_repo, container.settings, container=container)
        container.billing = BillingService(
            container.users, container.plans, container.settings,
            container.vpn, container.topup, discounts=container.discounts)
        from app.services.gifts import GiftService
        container.gifts = GiftService(container.users, container.db[names.GIFTS],
                                      container.plans, container.settings, container.vpn,
                                      discounts=container.discounts)
        container.trial.vpn = container.vpn
        container.moderation.vpn = container.vpn
        container.wipe.vpn = container.vpn
        container.promo.vpn = container.vpn
        container.private.vpn = container.vpn
        container.entities = build_entities(container)
        return container

    # Сервисы, без которых бот работает только наполовину: без них падают
    # хендлеры устройств, молчат админ-уведомления и вхолостую крутится
    # планировщик. Собраны списком, чтобы забытый attach_bot был виден при
    # старте, а не всплывал ошибкой в чате через неделю.
    REQUIRED_AFTER_ATTACH = ('devices', 'device_billing', 'renewal',
                             'expiry', 'lifeline', 'notifier')

    def missing_services(self) -> list[str]:
        return [name for name in self.REQUIRED_AFTER_ATTACH if getattr(self, name) is None]

    def attach_bot(self, bot) -> None:
        """Сервисы, которым нужен Bot: вебхуки шлют сообщения пользователям."""
        from app.bot.keyboards.common import campaign_keyboards
        from app.campaigns.sender import Sender
        from app.services.expiry import ExpiryNotifier
        from app.services.lifeline import LifelineService
        from app.services.notifier import Notifier

        from app.services.devices import DeviceBillingService
        from app.services.renewal import RenewalService

        # Notifier раздаётся сервисам явно: без него все админ-уведомления
        # (регистрации, пополнения, заявки на вывод) молча никуда не уходят
        self.notifier = Notifier(bot, self.settings, self.users)
        # личным серверам bot нужен, чтобы сказать владельцу о приостановке,
        # а notifier — чтобы заявка дошла до админ-чата
        if self.private is not None:
            self.private.bot = bot
            self.private.notifier = self.notifier
        for service in (self.topup, self.billing, self.gifts, self.payouts):
            if service is not None:
                service.notifier = self.notifier

        if self.trial is not None:
            self.trial.bot = bot        # getChatMember проверяет подписку на канал
        if self.topup is not None:
            # зачисление приходит вебхуком, а сказать об этом надо человеку
            self.topup.bot = bot
            self.topup.sender = Sender(on_blocked=self.users.mark_blocked)
        self.lifeline = LifelineService(self.users, self.settings, self.vpn)
        self.expiry = ExpiryNotifier(self.users, self.settings,
                                     Sender(on_blocked=self.users.mark_blocked), bot,
                                     campaign_keyboards(), self.lifeline)
        self.renewal = RenewalService(self.users, self.plans, self.settings, self.vpn,
                                      self.topup, self.lifeline, self.expiry,
                                      notifier=self.notifier, discounts=self.discounts)
        # ручное продление идёт тем же путём, что и автоматическое
        if self.billing is not None:
            self.billing.renewal = self.renewal
        self.device_billing = DeviceBillingService(self.users, self.settings, self.vpn,
                                                   notifier=self.notifier)
        self.devices = self.device_billing
