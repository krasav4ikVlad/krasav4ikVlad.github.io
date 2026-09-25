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
from app.content.emoji import e

log = logging.getLogger(__name__)


def build_providers(config, http=None, bills=None) -> list[PaymentProvider]:
    """Провайдер поднимается, только если для него задан ключ в .env.

    Названия у всех разные: несколько провайдеров закрывают один способ оплаты
    (СБП — wata и два severpay, карта РФ — cardlink и cloudpayments), и с
    одинаковыми подписями в меню появлялись кнопки-близнецы.
    """
    keys = config.payments
    candidates = [
        (keys.cardlink_token, lambda: CardlinkProvider(keys.cardlink_token,
                                                       keys.cardlink_shop_id, http, bills)),
        (keys.wata_token, lambda: WataProvider(keys.wata_token, keys.wata_token_visa, http=http)),
        (keys.heleket_key, lambda: HeleketProvider(keys.heleket_key,
                                                   keys.heleket_merchant_id, http)),
        (keys.severpay_key, lambda: SeverPayProvider(keys.severpay_key, 'severpay', http,
                                                     title=f'{e("sbp")} СБП (резерв)')),
        (keys.severpay_web_key, lambda: SeverPayProvider(keys.severpay_web_key,
                                                         'severpay_web', http,
                                                         title=f'{e("sbp")} СБП (запасной)')),
        # Один и тот же мини-апп Tribute закрывает и российские, и зарубежные
        # карты — в меню это две кнопки, как и было в старом боте. Вебхуки
        # приходят на код tribute, вторая запись нужна только ради кнопки.
        # адрес мини-аппа проставит configure() из настройки link.tribute
        (keys.tribute_key, lambda: TributeProvider(keys.tribute_key, http,
                                                   title=f'{e("card")} Карта РФ')),
        (keys.tribute_key, lambda: TributeProvider(keys.tribute_key, http,
                                                   code='tribute_eu',
                                                   title=f'{e("globe")} Карта иностранная')),
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

    async def configure(self) -> None:
        """Общие настройки провайдеров: куда возвращать и куда слать вебхук."""
        success = str(await self._settings.get('pay.success_url'))
        callback = str(await self._settings.get('pay.callback_base'))
        merchant = await self._settings.int('pay.severpay_mid')

        tribute = str(await self._settings.get('link.tribute'))

        for provider in self._providers.values():
            provider.success_url = success
            provider.callback_base = callback
            if provider.code.startswith('severpay') and merchant:
                provider._merchant_id = merchant
            # ссылку на мини-апп меняют из админки, а не пересборкой контейнера
            if provider.direct_url and tribute:
                provider.direct_url = tribute

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
