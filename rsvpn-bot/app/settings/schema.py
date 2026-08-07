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
        Setting('gifts.aliases', 'Старые коды подарков', 'str', '3years:3year',
                hint='старый:новый через запятую — для ссылок, выданных раньше'),
        Setting('features.referrals_enabled', 'Реферальная программа', 'bool', True),
        Setting('features.payouts_enabled', 'Вывод реф. баланса', 'bool', True),
        Setting('features.promo_enabled', 'Промокоды', 'bool', True),
        Setting('features.trial_enabled', 'Бесплатный период новичкам', 'bool', True,
                hint='Кнопка «получить бесплатно» — см. раздел «Бесплатный период»'),
    )),

    Group('payments', '💳 Платёжные методы', (
        # ключ строится как pay.<код провайдера>_enabled — см. PaymentRegistry
        # Значения по умолчанию отражают то, что реально подключено:
        # СБП — Cardlink, карты — Tribute, крипта — Heleket. Остальные выключены,
        # чтобы случайно найденный в .env ключ не добавил кнопку в меню.
        Setting('pay.cardlink_enabled', 'Cardlink (СБП)', 'bool', True),
        Setting('pay.heleket_enabled', 'Heleket (крипта)', 'bool', True),
        Setting('pay.tribute_enabled', 'Tribute (карта РФ)', 'bool', True),
        Setting('pay.tribute_eu_enabled', 'Tribute (карта иностранная)', 'bool', True),
        Setting('pay.wata_enabled', 'WATA (СБП)', 'bool', False),
        Setting('pay.severpay_enabled', 'SeverPay', 'bool', False),
        Setting('pay.severpay_web_enabled', 'SeverPay (запасной)', 'bool', False),
        Setting('pay.cards_ru_enabled', 'CloudPayments', 'bool', False),
        Setting('pay.min_topup', 'Минимальное пополнение', 'int', 75, unit='₽', min=1),
        Setting('pay.min_topup_sbp', 'Минимум для СБП / загран. карт', 'int', 100, unit='₽', min=1),
        Setting('pay.success_url', 'Куда возвращать после оплаты', 'str',
                'https://t.me/rsconnect_bot'),
        Setting('pay.callback_base', 'Адрес приёма вебхуков', 'str',
                'https://webhook.rsvps.tech'),
        Setting('pay.severpay_mid', 'Merchant ID SeverPay', 'int', 1058),
        Setting('pay.fee_rate', 'Комиссия шлюза (WATA)', 'percent', 0.05,
                hint='Насколько уменьшать зачисление относительно оплаченного', min=0, max=1),
    )),

    Group('pricing', '💰 Цены и лимиты', (
        Setting('price.device_extra', 'Доп. устройство в месяц', 'int', 75, unit='₽', min=0),
        Setting('price.devices_free_limit', 'Бесплатных устройств в подписке', 'int', 2, min=1),
        Setting('price.start_balance', 'Стартовый баланс при регистрации', 'int', 0,
                unit='₽', min=0,
                hint='0 — ничего не начисляем: вместо денег новичок получает '
                     'бесплатный период за подписку на канал'),
        # Цены подарков сюда не переезжают: подарок — это тот же тариф, и
        # списывается ровно plan['price'] из раздела «Тарифы». Отдельная
        # настройка означала бы две цены на одно и то же и расхождение
        # между витриной и списанием.

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

    Group('squads', '🖥 Серверы (сквады панели)', (
        Setting('squads.base', 'Базовый сквад', 'str',
                '727b7629-6c08-47dc-8741-33501be0e5b7'),
        Setting('squads.extra', 'Ротационные сквады', 'str', '',
                hint='UUID через запятую — выдаются новым подпискам по кругу'),
        Setting('squads.fingerprint', 'Сквады для «отпечатка»', 'str', '',
                hint='UUID через запятую'),
        Setting('squads.fingerprint_pick', 'Сколько выдавать из них', 'int', 5, min=0),
    )),

    Group('trial', '🎁 Бесплатный период', (
        Setting('price.trial_days', 'Сколько дней выдавать', 'int', 3, unit=' дн.', min=1),
        Setting('trial.require_subscription', 'Требовать подписку на канал', 'bool', True),
        Setting('trial.channel', 'Канал для проверки', 'str', '@rsconnect_vpn',
                hint='@юзернейм или числовой id. Бот должен быть админом канала — '
                     'иначе Telegram не даст проверить подписку'),
    )),

    Group('moderation', '🚫 Блокировки', (
        Setting('moderation.ban_silent', 'Молча игнорировать забаненных', 'bool', False,
                hint='Выключено — бот один раз отвечает текстом ниже'),
        Setting('moderation.ban_message', 'Что видит забаненный', 'text',
                '🚫 Доступ к боту ограничен. Если это ошибка — напишите в поддержку.'),
    )),

    Group('bypass', '🚧 ByPass (белые списки)', (
        Setting('bypass.squad_uuid', 'Сквад ByPass', 'str',
                'ac03f8c3-0de7-4380-9774-00079d0385ce'),
        Setting('bypass.external_squad_uuid', 'Внешний сквад ByPass', 'str',
                'dd4fd59f-415a-4fab-8af7-afde9db099bc'),
        Setting('bypass.default_traffic_gb', 'Трафик по умолчанию', 'int', 1, unit=' Гб', min=1),
        Setting('bypass.price_per_gb', 'Цена за гигабайт', 'int', 5, unit='₽', min=0),
        Setting('bypass.packages', 'Пакеты трафика', 'str', '5:50,15:90,30:170,100:500',
                hint='гигабайты:цена через запятую. Пусто — считается по цене за гигабайт'),
    )),

    Group('renewal', '🔁 Автопродление', (
        Setting('renewal.window_hours', 'За сколько часов продлевать', 'int', 24,
                unit=' ч', min=1),
        Setting('renewal.grace_hours', 'Сколько часов продлевать просроченные', 'int', 48,
                unit=' ч', min=0,
                hint='Если бот лежал, подписка не должна умереть при живом балансе'),
    )),

    Group('expiry', '⏰ Напоминания об истечении', (
        Setting('expiry.notify_enabled', 'Напоминания включены', 'bool', True,
                hint='Приходят вебхуками от панели, а не опросом базы'),
        Setting('expiry.send_3d', 'За 3 дня', 'bool', True),
        Setting('expiry.send_2d', 'За 2 дня', 'bool', False),
        Setting('expiry.send_1d', 'За сутки', 'bool', True),
        Setting('expiry.send_12h', 'За 12 часов', 'bool', True),
        Setting('expiry.send_6h', 'За 6 часов', 'bool', True),
        Setting('expiry.send_3h', 'За 3 часа', 'bool', True),
        Setting('expiry.send_1h', 'За час', 'bool', True),
        Setting('expiry.send_expired', 'В момент истечения', 'bool', True),
        Setting('expiry.send_expired_24h', 'Через сутки после', 'bool', True),
        Setting('expiry.send_expired_72h', 'Через трое суток после', 'bool', False),
    )),

    Group('payouts', '💸 Вывод реферальных средств', (
        Setting('payout.min_withdraw', 'Минимальная сумма вывода', 'int', 500, unit='₽', min=1),
        Setting('payout.cooldown_hours', 'Пауза между заявками', 'int', 24, unit=' ч', min=0),
        Setting('payout.min_card', 'Минимум на карту', 'int', 1000, unit='₽', min=0),
        Setting('payout.min_sbp', 'Минимум по СБП', 'int', 500, unit='₽', min=0),
        Setting('payout.min_crypto_usd', 'Минимум в USDT', 'int', 50, unit='$', min=0),
        Setting('payout.note', 'Подпись на экране вывода', 'text',
                'Выберите способ вывода и нажмите «Заказать вывод». '
                'Минимум по СБП: 500₽, по картам МИР: 1000₽, в USDT: 50$.'),
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
        Setting('survey.questions', 'Вопросы по порядку', 'str', 'stability,price',
                hint='Ключи через запятую'),
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
        Setting('link.ref_aliases', 'Именные реф. ссылки', 'str', '',
                hint='имя:user_id через запятую, например blog:123456'),
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
