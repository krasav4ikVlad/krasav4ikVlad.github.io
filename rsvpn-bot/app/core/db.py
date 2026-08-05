"""Имена коллекций и подключение к Mongo.

Имена — константы, а не строки по коду. В текущем loader.py у четырёх
коллекций в конце имени стоит пробел:

    support_quick_replies = db['support_quick_replies ']
    churn_surveys         = db['churn_surveys ']
    promo_codes           = db['promo_codes ']
    promo_usages          = db['promo_usages ']

Это рабочие коллекции с боевыми данными, просто названы с опечаткой. Заметить
её невозможно, пока строки разбросаны по файлам. Здесь имена собраны в одном
месте, а миграция m0003 переносит данные в имена без пробела.
"""

from __future__ import annotations

USERS = 'users'
SUBSCRIPTIONS = 'subscriptions'
PLANS = 'plans'
GIFTS = 'gifts'
CARDLINK_BILLS = 'cardlink_bills'
FINGERPRINTS = 'fingerprint_assignments'

BOT_SETTINGS = 'bot_settings'
SETTINGS_AUDIT = 'bot_settings_audit'
CONTENT_OVERRIDES = 'content_overrides'
CAMPAIGN_RUNS = 'campaign_runs'
MIGRATIONS = 'migrations'

PAYMENTS = 'payments'
PAYMENT_WEBHOOKS = 'payments_webhooks'

QUICK_REPLIES = 'support_quick_replies'
CHURN_SURVEYS = 'churn_surveys'
PROMO_CODES = 'promo_codes'
PROMO_USAGES = 'promo_usages'

# Старые имена с пробелом на конце → новые. Используются только миграцией.
LEGACY_RENAMES: dict[str, str] = {
    'support_quick_replies ': QUICK_REPLIES,
    'churn_surveys ': CHURN_SURVEYS,
    'promo_codes ': PROMO_CODES,
    'promo_usages ': PROMO_USAGES,
}


def create_client(uri: str):
    """Импорт драйвера ленивый: модуль с именами коллекций должен читаться
    и в тестах, где motor не нужен."""
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(uri, tz_aware=True)


def get_database(client, name: str):
    return client[name]
