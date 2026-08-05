"""Конфиг из окружения — то, что нельзя менять на лету.

Правило разделения:
  * здесь — секреты и инфраструктура (токены, URL БД, ключи провайдеров).
    Меняется через .env и требует перезапуска;
  * в app/settings/ — бизнес-параметры (цены, проценты, тумблеры).
    Меняются из админки на лету.

Имена переменных совпадают с текущим config.py, чтобы перенос значений был
копированием один в один. Значения GIFT_PRICES и GIFT_DAYS сюда НЕ переезжают:
это бизнес-данные, им место в тарифах и настройках (админка их правит).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_env_file(path: str | Path = '.env') -> None:
    """Читает .env, если он есть, и кладёт значения в окружение.

    Без зависимостей и без перетирания уже заданных переменных — то есть в
    докере и на сервере выигрывают настоящие переменные окружения, а локально
    (в том числе при запуске из PyCharm) достаточно файла .env рядом с проектом.
    """
    file = Path(path)
    if not file.is_file():
        # запуск из подпапки: ищем .env на уровень выше, рядом с pyproject.toml
        file = Path(__file__).resolve().parents[2] / '.env'
    if not file.is_file():
        return

    for line in file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env(*names: str, default: str = '', required: bool = False) -> str:
    """Первое непустое значение из перечисленных переменных."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    if required:
        raise RuntimeError(f'Не задана переменная окружения {names[0]}')
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_ids(name: str, default: tuple[int, ...] = ()) -> tuple[int, ...]:
    raw = os.getenv(name, '')
    ids = tuple(int(x) for x in raw.replace(' ', '').split(',') if x.lstrip('-').isdigit())
    return ids or default


@dataclass(frozen=True)
class VpnPanelConfig:
    """Remnawave: то, что сейчас берётся из API_URL / REMNAWAVE_TOKEN."""
    base_url: str = ''
    token: str = ''
    core_token: str = ''
    base_squad_id: str = ''
    happ_rsa_public_key: str = ''
    connect_base: str = 'https://connect.rsvps.tech/'
    # секрет вебхука панели: сейчас записан прямо в lifeline.py — перевыпустить
    webhook_secret: str = ''


@dataclass(frozen=True)
class StatsConfig:
    """Внешняя аналитика (API_URL_STATS / API_KEY_STATS)."""
    url: str = ''
    key: str = ''


@dataclass(frozen=True)
class PaymentsConfig:
    """Ключи провайдеров. Пустой ключ = провайдер не поднимается вообще."""
    cardlink_token: str = ''
    cardlink_shop_id: str = ''
    wata_token: str = ''
    wata_token_visa: str = ''
    heleket_key: str = ''
    heleket_merchant_id: str = ''
    severpay_key: str = ''
    severpay_web_key: str = ''
    tribute_key: str = ''
    cloudpayments_public_id: str = ''
    cloudpayments_secret: str = ''


@dataclass(frozen=True)
class Config:
    bot_token: str
    mongo_uri: str
    mongo_db: str
    admin_ids: tuple[int, ...]
    vpn: VpnPanelConfig = field(default_factory=VpnPanelConfig)
    payments: PaymentsConfig = field(default_factory=PaymentsConfig)
    stats: StatsConfig = field(default_factory=StatsConfig)
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
        load_env_file()
        return cls(
            # API_TOKEN — имя из текущего config.py, BOT_TOKEN оставлен как синоним
            bot_token=_env('API_TOKEN', 'BOT_TOKEN', required=True),
            # TOKEN_DB — тоже имя из текущего config.py: это строка подключения
            mongo_uri=_env('TOKEN_DB', 'MONGO_URI', default='mongodb://localhost:27017'),
            mongo_db=_env('MONGO_DB', default='RS_2'),
            admin_ids=_env_ids('ADMIN_IDS', default=(802421217, 1107871653)),
            vpn=VpnPanelConfig(
                base_url=_env('API_URL'),
                token=_env('REMNAWAVE_TOKEN'),
                core_token=_env('API_TOKEN_CORE'),
                base_squad_id=_env('VPN_BASE_SQUAD_ID',
                                   default='727b7629-6c08-47dc-8741-33501be0e5b7'),
                happ_rsa_public_key=_env('HAPP_RSA_PUBLIC_KEY'),
                connect_base=_env('VPN_CONNECT_BASE', default='https://connect.rsvps.tech/'),
                webhook_secret=_env('REMNAWAVE_WEBHOOK_SECRET'),
            ),
            payments=PaymentsConfig(
                cardlink_token=_env('CARDLINK_ACCESS_TOKEN'),
                cardlink_shop_id=_env('CARDLINK_SHOP_ID'),
                wata_token=_env('WATA_ACCESS_TOKEN'),
                wata_token_visa=_env('WATA_ACCESS_TOKEN_VISA'),
                heleket_key=_env('HELEKET_API_KEY'),
                heleket_merchant_id=_env('HELEKET_MERCHANT_ID'),
                severpay_key=_env('SEVER_API_KEY'),
                severpay_web_key=_env('SEVER_WEB_API_KEY'),
                # сейчас этот ключ записан прямо в FastApi.py — перенести и перевыпустить
                tribute_key=_env('TRIBUTE_API_KEY'),
                cloudpayments_public_id=_env('CLOUDPAYMENTS_PUBLIC_ID'),
                cloudpayments_secret=_env('CLOUDPAYMENTS_API_SECRET'),
            ),
            stats=StatsConfig(url=_env('API_URL_STATS'), key=_env('API_KEY_STATS')),
            log_level=_env('LOG_LEVEL', default='INFO'),
            timezone=_env('TZ', default='Europe/Moscow'),
            environment=_env('ENVIRONMENT', default='production'),
            api_port=_env_int('API_PORT', 8000),
            media_dir=_env('MEDIA_DIR', default='media'),
        )
