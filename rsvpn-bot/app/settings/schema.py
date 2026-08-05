"""Описание всех runtime-настроек бота.

Все «крутилки» (цены, проценты, минимальные суммы, включение/выключение функций,
ссылки, тексты) описываются один раз в SCHEMA ниже. Дальше:

  * в коде бота значение берётся так:   await S.get('price.device_extra')
  * админка строится из SCHEMA автоматически — новых хендлеров писать не нужно.

Хранение и кэш — в app/settings/service.py, здесь только описание.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
        # ключ строится как pay.<код провайдера>_enabled — см. PaymentRegistry
        Setting('pay.cardlink_enabled', 'Cardlink (карты РФ)', 'bool', True),
        Setting('pay.wata_enabled', 'WATA (СБП)', 'bool', True),
        Setting('pay.heleket_enabled', 'Heleket (крипта)', 'bool', True),
        Setting('pay.severpay_enabled', 'SeverPay', 'bool', True),
        Setting('pay.tribute_enabled', 'Tribute (иностранные карты)', 'bool', True),
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
        Setting('price.trial_days', 'Длительность бесплатного периода', 'int', 3, unit=' дн.', min=0),
        Setting('price.default_device_limit', 'Устройств в новой подписке', 'int', 2, min=1),
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

    Group('payouts', '💸 Вывод реферальных средств', (
        Setting('payout.min_withdraw', 'Минимальная сумма вывода', 'int', 500, unit='₽', min=1),
        Setting('payout.cooldown_hours', 'Пауза между заявками', 'int', 24, unit=' ч', min=0),
        Setting('payout.min_card', 'Минимум на карту', 'int', 1000, unit='₽', min=0),
        Setting('payout.min_sbp', 'Минимум по СБП', 'int', 500, unit='₽', min=0),
        Setting('payout.min_crypto_usd', 'Минимум в USDT', 'int', 50, unit='$', min=0),
    )),

    Group('lifeline', '🪢 Lifeline (сервер после истечения)', (
        Setting('lifeline.enabled', 'Переводить истёкших на TG-сервер', 'bool', True),
        Setting('lifeline.grace_days', 'Сколько дней держать', 'int', 3, unit=' дн.', min=1),
        Setting('lifeline.squad_uuid', 'UUID сквада lifeline', 'str',
                '36df3a14-75d1-4c9a-a933-212fd29a7806'),
    )),

    Group('survey', '📊 Опрос с бонусом', (
        Setting('survey.enabled', 'Опрос включён', 'bool', True),
        Setting('survey.bonus', 'Бонус за прохождение', 'int', 25, unit='₽', min=0),
        Setting('survey.price_question_value', 'Цена в вопросе про подписку', 'int', 150, unit='₽'),
    )),

    Group('campaigns', '📣 Кампании и рассылки', (
        Setting('campaign.new_trial_enabled', 'Кампания «новые триал»', 'bool', True),
        Setting('campaign.expired_enabled', 'Кампания «истёкшие»', 'bool', True),
        Setting('campaign.trial_enabled', 'Кампания «триал»', 'bool', True),
        Setting('campaign.churn_survey_enabled', 'Опрос причин оттока', 'bool', True),
        Setting('campaign.interval_minutes', 'Интервал запуска кампаний', 'int', 60, unit='мин', min=5),
        Setting('campaign.hour_from', 'Не отправлять раньше', 'int', 9, unit=':00', min=0, max=23),
        Setting('campaign.hour_to', 'Не отправлять позже', 'int', 22, unit=':00', min=0, max=23),
        Setting('campaign.broadcast_delay_ms', 'Пауза между сообщениями рассылки', 'int', 40, unit='мс', min=0),
    )),

    Group('notify', '🔔 Уведомления админам', (
        # темы форума в админ-чате: сейчас эти числа зашиты в 17 местах кода
        Setting('notify.chat_id', 'Чат для уведомлений', 'int', -1002433849803),
        Setting('notify.topic_registration', 'Тема: регистрации', 'int', 2),
        Setting('notify.topic_topup', 'Тема: пополнения', 'int', 3),
        Setting('notify.topic_topup_try', 'Тема: попытки пополнения', 'int', 4),
        Setting('notify.topic_subscription', 'Тема: покупка/продление', 'int', 244),
        Setting('notify.topic_bypass', 'Тема: покупка трафика ByPass', 'int', 24236),
        Setting('notify.topic_devices', 'Тема: доп. устройства', 'int', 24238),
        Setting('notify.topic_email', 'Тема: привязка почты', 'int', 24240),
        Setting('notify.topic_promo', 'Тема: промокоды', 'int', 24484),
        Setting('notify.topic_campaigns', 'Тема: отчёты кампаний', 'int', 470234),
        Setting('notify.topic_payout', 'Тема: заявки на вывод', 'int', 279680),
        Setting('notify.enabled', 'Слать уведомления админам', 'bool', True),
    )),

    Group('links', '🔗 Ссылки и контакты', (
        Setting('link.support', 'Поддержка', 'str', 'https://t.me/RSConnectHelp_bot'),
        Setting('link.channel', 'Канал', 'str', 'https://t.me/rsconnect_vpn'),
        Setting('link.connect_base', 'База ссылки подключения', 'str', 'https://connect.rsvps.tech/'),
        Setting('link.web_cabinet', 'Личный кабинет', 'str', 'https://console.rscore.app/'),
        Setting('link.tribute', 'Tribute (карты)', 'str', 'https://t.me/tribute/app?startapp=dNvx'),
        Setting('link.bot_username', 'Юзернейм бота (для реф. ссылок)', 'str', 'rsconnect_bot'),
        Setting('link.offer', 'Публичная оферта', 'str',
                'https://telegra.ph/Polzovatelskoe-soglashenie-Publichnaya-oferta-RS-VPN-07-02'),
        Setting('link.privacy', 'Политика конфиденциальности', 'str',
                'https://telegra.ph/Politika-konfidencialnosti-RS-VPN-07-02'),
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


def cast_value(setting: Setting, raw: Any) -> Any:
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
