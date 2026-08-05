"""Реестр провайдеров: сборка из конфига, включение тумблерами админки."""

from __future__ import annotations

import logging

from app.integrations.payments.base import PaymentProvider
from app.integrations.payments.cardlink import CardlinkProvider
from app.integrations.payments.cloudpayments import CloudPaymentsProvider
from app.integrations.payments.heleket import HeleketProvider
from app.integrations.payments.severpay import SeverPayProvider
from app.integrations.payments.tribute import TributeProvider
from app.integrations.payments.wata import WataProvider

log = logging.getLogger(__name__)


def build_providers(config, http=None) -> list[PaymentProvider]:
    """Провайдер поднимается, только если для него задан ключ в .env."""
    keys = config.payments
    candidates = [
        (keys.cardlink_token, lambda: CardlinkProvider(keys.cardlink_token,
                                                       keys.cardlink_shop_id, http)),
        (keys.wata_token, lambda: WataProvider(keys.wata_token, keys.wata_token_visa, http=http)),
        (keys.heleket_key, lambda: HeleketProvider(keys.heleket_key,
                                                   keys.heleket_merchant_id, http)),
        (keys.severpay_key, lambda: SeverPayProvider(keys.severpay_key, 'severpay', http)),
        (keys.severpay_web_key, lambda: SeverPayProvider(keys.severpay_web_key,
                                                         'severpay_web', http)),
        (keys.tribute_key, lambda: TributeProvider(keys.tribute_key, http)),
        (keys.cloudpayments_secret, lambda: CloudPaymentsProvider(
            keys.cloudpayments_public_id, keys.cloudpayments_secret, http)),
    ]

    providers = [factory() for key, factory in candidates if key]
    log.info('платёжные провайдеры: %s', [p.code for p in providers] or 'нет ключей в .env')
    return providers


class PaymentRegistry:
    def __init__(self, providers: list[PaymentProvider], settings):
        self._providers = {p.code: p for p in providers}
        self._settings = settings

    def get(self, code: str) -> PaymentProvider | None:
        return self._providers.get(code)

    def all(self) -> list[PaymentProvider]:
        return list(self._providers.values())

    async def available(self) -> list[PaymentProvider]:
        """Провайдеры, включённые тумблером pay.<code>_enabled в админке."""
        result = []
        for code, provider in self._providers.items():
            key = f'pay.{code}_enabled'
            if key not in self._settings.index or await self._settings.flag(key):
                result.append(provider)
        return result
