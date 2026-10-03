"""Единый слой настроек бота.

Все «крутилки» (цены, проценты, минимальные суммы, включение/выключение функций,
ссылки, тексты) описываются один раз в SCHEMA ниже. Дальше:

  * в коде бота значение берётся так:   await S.get('price.device_extra')
  * админка строится из SCHEMA автоматически — новых хендлеров писать не нужно.

Хранилище: коллекция bot_settings, по документу на настройку
({_id: 'price.device_extra', value: 90}). Отдельные документы, а не один
вложенный — потому что в ключах есть точки, а Mongo трактует точку в $set
как путь к вложенному полю и превратил бы values.price.device_extra
в {values: {price: {device_extra: ...}}}.

В памяти — кэш на CACHE_TTL_SEC секунд, поэтому изменение из админки
подхватывается и ботом, и FastAPI-процессом без перезапуска.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from loader import db

settings_col = db['bot_settings']
settings_audit_col = db['bot_settings_audit']

CACHE_TTL_SEC = 10.0

# Типы полей:
#   int     — целое число
#   float   — дробное число
#   percent — вводится в процентах (20), хранится как доля (0.2)
#   str     — короткая строка
#   text    — длинный текст / HTML (для сообщений пользователю)
#   bool    — переключатель, редактируется одной кнопкой
TYPES = ('int', 'float', 'percent', 'str', 'text', 'bool')


@dataclass(frozen=True)
class Setting:
    key: str
    title: str
    type: str = 'int'
    default: Any = 0
    hint: str = ''
    unit: str = ''
    min: float | None = None
    max: float | None = None


@dataclass(frozen=True)
class Group:
    code: str
    title: str
    items: tuple[Setting, ...]


# ─────────────────────────────────────────────────────────────────────────────
# ВСЁ, ЧТО МОЖНО МЕНЯТЬ ИЗ АДМИНКИ. Добавил строку — появилась кнопка.
# ─────────────────────────────────────────────────────────────────────────────
SCHEMA: tuple[Group, ...] = (
    Group('features', '🎛 Функции', (
        Setting('features.buy_enabled', 'Покупка подписки', 'bool', True,
                hint='Выключает кнопки покупки тарифов'),
        Setting('features.extend_enabled', 'Продление подписки', 'bool', True,
                hint='Кнопка «Продлить подписку» и автосписание в планировщике'),
        Setting('features.autorenew_enabled', 'Автопродление (списание с баланса)', 'bool', True),
        Setting('features.change_period_enabled', 'Смена длительности', 'bool', True),
        Setting('features.devices_enabled', 'Покупка доп. устройств', 'bool', True),
        Setting('features.bypass_enabled', 'ByPass (белые списки)', 'bool', True),
        Setting('features.gifts_enabled', 'Подарки', 'bool', True),
        Setting('features.referrals_enabled', 'Реферальная программа', 'bool', True),
        Setting('features.payouts_enabled', 'Вывод реф. баланса', 'bool', True),
        Setting('features.promo_enabled', 'Промокоды', 'bool', True),
        Setting('features.trial_enabled', 'Стартовый баланс новичкам', 'bool', True),
    )),

    Group('payments', '💳 Платёжные методы', (
        Setting('pay.sbp_enabled', 'СБП', 'bool', True),
        Setting('pay.cards_ru_enabled', 'Карты РФ', 'bool', True),
        Setting('pay.cards_eu_enabled', 'Иностранные карты', 'bool', True),
        Setting('pay.crypto_enabled', 'Криптовалюта', 'bool', True),
        Setting('pay.min_topup', 'Минимальное пополнение', 'int', 75, unit='₽', min=1),
        Setting('pay.min_topup_sbp', 'Минимум для СБП / загран. карт', 'int', 100, unit='₽', min=1),
        Setting('pay.fee_rate', 'Комиссия шлюза (WATA)', 'percent', 0.05,
                hint='Насколько уменьшать зачисление относительно оплаченного', min=0, max=1),
    )),

    Group('pricing', '💰 Цены и лимиты', (
        Setting('price.device_extra', 'Доп. устройство в месяц', 'int', 75, unit='₽', min=0),
        Setting('price.devices_free_limit', 'Бесплатных устройств в подписке', 'int', 2, min=1),
        Setting('price.start_balance', 'Стартовый баланс при регистрации', 'int', 18, unit='₽', min=0),
        Setting('price.gift_3years', 'Подарок «3 года» (списание)', 'int', 3000, unit='₽', min=0),
        # Сами тарифы (цена/дни/подарки) — отдельная сущность, см. core/plans.py
    )),

    Group('bonuses', '🎁 Бонусы и акции', (
        Setting('bonus.topup_rate', 'Бонус за пополнение', 'percent', 0.20, min=0, max=2,
                hint='Начисляется сверху к каждому пополнению'),
        Setting('bonus.topup_enabled', 'Бонус за пополнение включён', 'bool', True),
        Setting('bonus.tribute_extra_rate', 'Доп. бонус за оплату через Tribute', 'percent', 0.05, min=0, max=1),
        Setting('bonus.ab_new_trial_rate', 'A/B бонус новичкам', 'percent', 0.30, min=0, max=2),
        Setting('bonus.ref_rate', 'Реферальный процент', 'percent', 0.30, min=0, max=1),
        Setting('bonus.sleeping_days', 'Дней без подписки для «спящей» скидки', 'int', 7, min=1),
        Setting('bonus.sleeping_discount', 'Размер «спящей» скидки', 'percent', 0.40, min=0, max=1),
        Setting('bonus.churn_survey_reward', 'Бонус за ответ в опросе оттока', 'int', 8, unit='₽', min=0),
    )),

    Group('campaigns', '📣 Кампании и рассылки', (
        Setting('campaign.new_trial_enabled', 'Кампания «новые триал»', 'bool', True),
        Setting('campaign.expired_enabled', 'Кампания «истёкшие»', 'bool', True),
        Setting('campaign.trial_enabled', 'Кампания «триал»', 'bool', True),
        Setting('campaign.churn_survey_enabled', 'Опрос причин оттока', 'bool', True),
        Setting('campaign.interval_minutes', 'Интервал запуска кампаний', 'int', 60, unit='мин', min=5),
        Setting('campaign.broadcast_delay_ms', 'Пауза между сообщениями рассылки', 'int', 40, unit='мс', min=0),
    )),

    Group('links', '🔗 Ссылки и контакты', (
        Setting('link.support', 'Поддержка', 'str', 'https://t.me/RSConnectHelp_bot'),
        Setting('link.channel', 'Канал', 'str', 'https://t.me/rsconnect_vpn'),
        Setting('link.connect_base', 'База ссылки подключения', 'str', 'https://connect.rsvps.tech/'),
        Setting('link.web_cabinet', 'Личный кабинет', 'str', 'https://console.rscore.app/'),
        Setting('link.tribute', 'Tribute (карты)', 'str', 'https://t.me/tribute/app?startapp=dNvx'),
        Setting('admin.log_chat_id', 'Чат для админ-уведомлений', 'int', -1002433849803),
    )),

    Group('texts', '📝 Тексты', (
        Setting('text.sub_hint', 'Подсказка на экране выбора тарифа', 'text',
                'Выберите длительность подписки.'),
        Setting('text.devices_hint', 'Подсказка в менеджере устройств', 'text',
                'Каждое третье и последующее устройство платное.'),
        Setting('text.maintenance', 'Текст техработ', 'text',
                'Ведутся технические работы, попробуйте позже.'),
        Setting('features.maintenance_mode', 'Режим техработ (бот отвечает только этим текстом)', 'bool', False),
    )),
)

INDEX: dict[str, Setting] = {s.key: s for g in SCHEMA for s in g.items}
GROUPS: dict[str, Group] = {g.code: g for g in SCHEMA}


def _cast(setting: Setting, raw: Any) -> Any:
    try:
        if setting.type == 'bool':
            return bool(raw)
        if setting.type == 'int':
            return int(raw)
        if setting.type in ('float', 'percent'):
            return float(raw)
        return str(raw)
    except (TypeError, ValueError):
        return setting.default


class SettingsService:
    def __init__(self, collection, index: dict[str, Setting]):
        self._col = collection
        self._index = index
        self._cache: dict[str, Any] = {}
        self._loaded_at = 0.0

    # ── чтение ──────────────────────────────────────────────────────────────
    async def _load(self, force: bool = False) -> dict[str, Any]:
        if not force and self._cache and (time.monotonic() - self._loaded_at) < CACHE_TTL_SEC:
            return self._cache

        docs = await self._col.find({}).to_list(length=None)

        merged = {key: s.default for key, s in self._index.items()}
        for doc in docs:
            setting = self._index.get(doc.get('_id'))
            if setting is not None:
                merged[setting.key] = _cast(setting, doc.get('value'))

        self._cache = merged
        self._loaded_at = time.monotonic()
        return merged

    async def all(self) -> dict[str, Any]:
        return dict(await self._load())

    async def get(self, key: str, default: Any = None) -> Any:
        values = await self._load()
        if key in values:
            return values[key]
        setting = self._index.get(key)
        return setting.default if setting else default

    async def flag(self, key: str) -> bool:
        return bool(await self.get(key, False))

    async def int(self, key: str) -> int:
        return int(await self.get(key, 0) or 0)

    async def rate(self, key: str) -> float:
        """Процентная настройка как доля: 0.2 для 20%."""
        return float(await self.get(key, 0.0) or 0.0)

    # ── запись ──────────────────────────────────────────────────────────────
    async def set(self, key: str, value: Any, admin_id: int | None = None) -> Any:
        setting = self._index.get(key)
        if setting is None:
            raise KeyError(f'Неизвестная настройка: {key}')

        value = _cast(setting, value)
        before = await self.get(key)

        await self._col.update_one(
            {'_id': key},
            {'$set': {'value': value, 'updated_at': datetime.now()}},
            upsert=True,
        )
        self.invalidate()

        await settings_audit_col.insert_one({
            'key': key, 'before': before, 'after': value,
            'admin_id': admin_id, 'created_at': datetime.now(),
        })
        return value

    async def toggle(self, key: str, admin_id: int | None = None) -> bool:
        return bool(await self.set(key, not await self.flag(key), admin_id))

    async def reset(self, key: str, admin_id: int | None = None) -> Any:
        setting = self._index[key]
        return await self.set(key, setting.default, admin_id)

    def invalidate(self) -> None:
        self._loaded_at = 0.0


S = SettingsService(settings_col, INDEX)


# ── ввод/вывод значений для админки ─────────────────────────────────────────
def format_value(setting: Setting, value: Any) -> str:
    if setting.type == 'bool':
        return '✅ включено' if value else '❌ выключено'
    if setting.type == 'percent':
        return f'{round(float(value) * 100, 2):g}%'
    if setting.type == 'text':
        text = str(value)
        return text if len(text) <= 120 else text[:117] + '…'
    return f'{value}{setting.unit}' if setting.unit else str(value)


def parse_value(setting: Setting, raw: str) -> tuple[bool, Any, str]:
    """Возвращает (успех, значение, текст ошибки)."""
    raw = (raw or '').strip()

    if setting.type in ('str', 'text'):
        if not raw:
            return False, None, 'Пустое значение.'
        return True, raw, ''

    normalized = raw.replace(',', '.').replace('%', '').replace('₽', '').strip()

    if setting.type == 'int':
        try:
            value = int(float(normalized))
        except ValueError:
            return False, None, 'Нужно целое число.'
    elif setting.type == 'float':
        try:
            value = float(normalized)
        except ValueError:
            return False, None, 'Нужно число.'
    elif setting.type == 'percent':
        try:
            value = float(normalized) / 100
        except ValueError:
            return False, None, 'Нужно число в процентах, например 20.'
    else:
        return False, None, 'Это поле переключается кнопкой.'

    check = value * 100 if setting.type == 'percent' else value
    limit_lo = setting.min * 100 if (setting.type == 'percent' and setting.min is not None) else setting.min
    limit_hi = setting.max * 100 if (setting.type == 'percent' and setting.max is not None) else setting.max

    if limit_lo is not None and check < limit_lo:
        return False, None, f'Минимум: {limit_lo:g}'
    if limit_hi is not None and check > limit_hi:
        return False, None, f'Максимум: {limit_hi:g}'

    return True, value, ''


def input_hint(setting: Setting) -> str:
    if setting.type == 'percent':
        return 'Отправьте число в процентах (например 20 — это 20%).'
    if setting.type == 'int':
        return 'Отправьте целое число.'
    if setting.type == 'float':
        return 'Отправьте число (можно дробное).'
    if setting.type == 'text':
        return 'Отправьте текст (HTML разрешён).'
    return 'Отправьте новое значение одним сообщением.'


__all__ = [
    'S', 'SCHEMA', 'GROUPS', 'INDEX', 'Setting', 'Group',
    'format_value', 'parse_value', 'input_hint', 'replace',
]
