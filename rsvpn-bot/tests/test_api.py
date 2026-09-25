"""Вебхуки по HTTP — так же, как их шлют платёжки и панель.

Раньше проверялись только сервисы под ними (topup, expiry), а сами адреса —
нет. Между тем именно адрес и формат тела ломаются при переезде: платёжка
стучится по старому URL, попадает в 404, деньги не зачисляются, и узнаём мы
об этом от пользователя.
"""

import json

import pytest

fastapi = pytest.importorskip('fastapi')
pytest.importorskip('httpx')

import httpx
from fastapi import FastAPI

from app.api.remnawave import router as remnawave_router
from app.api.webhooks import router as webhooks_router


class RecordingBot:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, user_id, text, reply_markup=None, **kwargs):
        self.sent.append({'user_id': user_id, 'text': text})
        return True


class FakeVpn:
    async def update_subscription(self, uuid, **kwargs):
        return {}


@pytest.fixture
async def api(container):
    """Приложение с теми же роутерами, что в проде, но без lifespan."""
    from app.services.notifier import Notifier
    from app.services.topup import TopupService

    import dataclasses
    # секрет задан: с пустым проверка подписи намеренно пропускается,
    # и тест проверял бы не то поведение, что в бою
    container.config = dataclasses.replace(
        container.config,
        vpn=dataclasses.replace(container.config.vpn, webhook_secret='секрет'))

    container.vpn = FakeVpn()
    container.topup = TopupService(container.users, container.payments_repo,
                                   container.settings)
    # как в main_api: вебхук зачисляет деньги И сообщает об этом человеку
    container.topup.bot = RecordingBot()

    await make_all_providers(container)

    app = FastAPI()
    app.include_router(webhooks_router)
    app.include_router(remnawave_router)
    app.state.container = container

    # httpx поверх ASGI, а не TestClient: весь набор тестов асинхронный, и
    # синхронный клиент внутри него дерётся за event loop
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url='http://api') as client:
        yield client, container


async def make_all_providers(container):
    """Реестр со всеми кодами, на которые ведут старые адреса."""
    from app.integrations.payments.registry import PaymentRegistry

    codes = ('cardlink', 'wata', 'heleket', 'severpay', 'severpay_web',
             'tribute', 'cards_ru')
    container.payments = PaymentRegistry([_stub(code) for code in codes],
                                         container.settings)


# ── адреса ──────────────────────────────────────────────────────────────────
async def test_old_payment_urls_still_work(api):
    """В кабинетах платёжек прописаны старые адреса — менять их при переезде
    не нужно, иначе оплаты потеряются в момент переключения."""
    client, container = api

    for path in ('/payment/webhook_cardlink', '/payment/webhook_wata',
                 '/payment/webhook_heleket', '/payment/webhook_sever',
                 '/payment/webhook_sever_web', '/payment/webhook_tribute',
                 '/payment/webhook'):
        response = await client.post(path, json={})
        assert response.status_code != 404, f'{path} потерялся'


async def test_unknown_payment_route_is_404(api):
    client, _ = api
    assert (await client.post('/payment/что-то-чужое', json={})).status_code == 404


async def test_remnawave_path_is_unchanged(api):
    """Тот же путь, что был у lifeline — в панели ничего менять не нужно."""
    client, _ = api
    assert (await client.post('/remnawave/webhook', json={})).status_code != 404


# ── зачисление ──────────────────────────────────────────────────────────────
def _stub(code: str):
    from app.integrations.payments.base import (PaymentProvider, SignatureError,
                                                WebhookEvent)

    class Stub(PaymentProvider):
        def __init__(self):
            self.code = code
            self.title = 'тест'
            self.verified = True

        def verify(self, body, headers, payload):
            if payload.get('bad_sign'):
                raise SignatureError('подпись не сошлась')

        def parse(self, payload):
            if not payload.get('amount'):
                return WebhookEvent.ignore('not_a_payment')
            return WebhookEvent(handled=True, txid=payload['txid'],
                                user_id=payload['user_id'],
                                amount=payload['amount'], raw=payload)

        async def resolve_user(self, event, container):
            return event.user_id

    return Stub()


async def test_payment_is_credited_through_http(api, user_factory):
    client, container = api
    user = await user_factory()
    user_id = user['user_data']['user_id']

    response = await client.post('/payment/webhook/heleket', json={
        'txid': 'tx-1', 'user_id': user_id, 'amount': 100})

    assert response.status_code == 200
    fresh = await container.users.get(user_id)
    assert fresh['info']['balance'] > 0


async def test_repeated_webhook_does_not_credit_twice(api, user_factory):
    """Платёжки ретраят доставку — второй раз зачислять нельзя."""
    client, container = api
    user = await user_factory()
    user_id = user['user_data']['user_id']

    body = {'txid': 'tx-повтор', 'user_id': user_id, 'amount': 100}
    await client.post('/payment/webhook/heleket', json=body)
    after_first = (await container.users.get(user_id))['info']['balance']

    await client.post('/payment/webhook/heleket', json=body)
    after_second = (await container.users.get(user_id))['info']['balance']

    assert after_first == after_second


async def test_bad_signature_is_rejected(api, user_factory):
    client, container = api
    response = await client.post('/payment/webhook/heleket',
                           json={'txid': 'tx-2', 'user_id': 1, 'amount': 100,
                                 'bad_sign': True})

    assert response.status_code == 403


async def test_non_payment_event_is_accepted_and_ignored(api):
    """Ответить не-2xx значит получить поток ретраев на событие, которое
    нас не касается."""
    client, container = api

    response = await client.post('/payment/webhook/heleket', json={'txid': 'tx-3'})

    assert response.status_code == 200
    assert response.json()['ok'] is True


def test_form_encoded_body_is_understood():
    """Часть платёжек шлёт form-urlencoded, а не JSON."""
    from app.api.webhooks import parse_body

    parsed = parse_body(b'amount=100&order_id=42',
                        {'content-type': 'application/x-www-form-urlencoded'})
    assert parsed == {'amount': '100', 'order_id': '42'}


# ── панель ──────────────────────────────────────────────────────────────────
async def test_remnawave_rejects_a_wrong_signature_without_retries(api):
    """Отвечаем 200 даже на плохую подпись: не-2xx панель ретраит."""
    client, container = api

    response = await client.post('/remnawave/webhook',
                           content=json.dumps({'event': 'user.expired'}),
                           headers={'x-remnawave-signature': 'wrong-signature'})

    assert response.status_code == 200
    assert response.json().get('note') == 'bad_signature'


async def test_the_payer_hears_about_it_over_http(api, user_factory):
    """Сквозь настоящий роутер: вебхук пришёл — человеку ушло сообщение.

    Именно этого не хватало: деньги зачислялись, админам уходило
    уведомление, а плательщику — ничего.
    """
    client, container = api
    await user_factory()

    await client.post('/payment/webhook_wata',
                      json={'txid': 'http-1', 'amount': 100, 'user_id': 1})

    sent = container.topup.bot.sent
    assert sent and sent[0]['user_id'] == 1
    assert 'Баланс пополнен' in sent[0]['text']
