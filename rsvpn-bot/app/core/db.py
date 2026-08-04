"""Подключение к Mongo и все коллекции в одном месте.

Имена коллекций — константы, а не строки по коду. В старом loader.py у трёх
коллекций в конце имени был пробел ('promo_codes '), и это невозможно было
заметить, потому что строки писались в разных файлах.
"""

from __future__ import annotations

USERS = 'users'
PLANS = 'plans'
BOT_SETTINGS = 'bot_settings'
SETTINGS_AUDIT = 'bot_settings_audit'
PROMO_CODES = 'promo_codes'
PROMO_USAGES = 'promo_usages'
PAYMENTS = 'payments'
PAYMENT_WEBHOOKS = 'payment_webhooks'
QUICK_REPLIES = 'support_quick_replies'
CHURN_SURVEYS = 'churn_surveys'
GIFTS = 'gifts'
CAMPAIGN_RUNS = 'campaign_runs'
FINGERPRINTS = 'fingerprint_assignments'
CONTENT_OVERRIDES = 'content_overrides'
MIGRATIONS = 'migrations'


def create_client(uri: str):
    """Импорт драйвера ленивый: модуль с именами коллекций должен читаться
    и в тестах, где motor не нужен."""
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(uri, tz_aware=True)


def get_database(client, name: str):
    return client[name]
