"""Конфиг из окружения — то, что нельзя менять на лету.

Правило разделения:
  * здесь — секреты и инфраструктура (токены, URL БД, ключи провайдеров).
    Меняется через .env и требует перезапуска;
  * в app/settings/ — бизнес-параметры (цены, проценты, тумблеры).
    Меняются из админки на лету.

Никакого os.getenv по коду проекта: всё читается один раз здесь, дальше
конфиг передаётся явно через контейнер зависимостей.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f'Не задана переменная окружения {name}')
    return value or ''


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_ids(name: str) -> tuple[int, ...]:
    raw = os.getenv(name, '')
    return tuple(int(x) for x in raw.replace(' ', '').split(',') if x.lstrip('-').isdigit())


@dataclass(frozen=True)
class VpnPanelConfig:
    base_url: str
    token: str
    base_squad_id: str


@dataclass(frozen=True)
class PaymentsConfig:
    """Ключи провайдеров. Пустой ключ = провайдер выключен на уровне инфраструктуры."""
    cardlink_token: str = ''
    wata_token: str = ''
    heleket_key: str = ''
    severpay_key: str = ''
    severpay_web_key: str = ''
    tribute_key: str = ''
    cloudpayments_key: str = ''


@dataclass(frozen=True)
class Config:
    bot_token: str
    mongo_uri: str
    mongo_db: str
    admin_ids: tuple[int, ...]
    vpn: VpnPanelConfig
    payments: PaymentsConfig
    log_level: str = 'INFO'
    timezone: str = 'Europe/Moscow'
    environment: str = 'production'
    api_host: str = '0.0.0.0'
    api_port: int = 8000
    media_dir: str = 'media'

    @property
    def is_production(self) -> bool:
        return self.environment == 'production'

    @classmethod
    def from_env(cls) -> 'Config':
        return cls(
            bot_token=_env('BOT_TOKEN', required=True),
            mongo_uri=_env('MONGO_URI', 'mongodb://localhost:27017'),
            mongo_db=_env('MONGO_DB', 'rsvpn'),
            admin_ids=_env_ids('ADMIN_IDS'),
            vpn=VpnPanelConfig(
                base_url=_env('VPN_PANEL_URL'),
                token=_env('VPN_PANEL_TOKEN'),
                base_squad_id=_env('VPN_BASE_SQUAD_ID'),
            ),
            payments=PaymentsConfig(
                cardlink_token=_env('CARDLINK_TOKEN'),
                wata_token=_env('WATA_TOKEN'),
                heleket_key=_env('HELEKET_API_KEY'),
                severpay_key=_env('SEVERPAY_API_KEY'),
                severpay_web_key=_env('SEVERPAY_WEB_API_KEY'),
                tribute_key=_env('TRIBUTE_API_KEY'),
                cloudpayments_key=_env('CLOUDPAYMENTS_KEY'),
            ),
            log_level=_env('LOG_LEVEL', 'INFO'),
            timezone=_env('TZ', 'Europe/Moscow'),
            environment=_env('ENVIRONMENT', 'production'),
            api_port=_env_int('API_PORT', 8000),
            media_dir=_env('MEDIA_DIR', 'media'),
        )
