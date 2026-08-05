"""Платёжные провайдеры: подписи и разбор вебхуков."""

import base64
import hashlib
import hmac
import json

import pytest

from app.integrations.payments.base import SignatureError
from app.integrations.payments.cardlink import CardlinkProvider
from app.integrations.payments.cloudpayments import CloudPaymentsProvider
from app.integrations.payments.heleket import HeleketProvider
from app.integrations.payments.severpay import SeverPayProvider
from app.integrations.payments.tribute import TributeProvider
from app.integrations.payments.wata import WataProvider

HELEKET_KEY = 'heleket-secret'
SEVER_KEY = 'sever-secret'
TRIBUTE_KEY = 'tribute-secret'


def heleket_payload(**over):
    data = {'status': 'paid', 'is_final': True, 'order_id': '802421217_abc',
            'amount': '150.00', 'uuid': 'h-1', **over}
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('/', '\\/')
    encoded = base64.b64encode(raw.encode()).decode()
    data['sign'] = hashlib.md5((encoded + HELEKET_KEY).encode()).hexdigest()
    return data


def sever_payload(**over):
    data = {'type': 'payin',
            'data': {'status': 'paid', 'order_id': '802421217_uuid', 'id': 's-1',
                     'amount': '300', **over}}
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()
    data['sign'] = hmac.new(SEVER_KEY.encode(), raw, hashlib.sha256).hexdigest()
    return data


# ── подписи ─────────────────────────────────────────────────────────────────
def test_heleket_signature_accepted_and_rejected():
    provider = HeleketProvider(HELEKET_KEY)
    payload = heleket_payload()

    provider.verify(b'', {}, payload)

    with pytest.raises(SignatureError):
        provider.verify(b'', {}, {**payload, 'sign': 'deadbeef'})
    with pytest.raises(SignatureError):
        provider.verify(b'', {}, {k: v for k, v in payload.items() if k != 'sign'})


def test_severpay_signature():
    provider = SeverPayProvider(SEVER_KEY)
    payload = sever_payload()

    provider.verify(b'', {}, payload)
    with pytest.raises(SignatureError):
        provider.verify(b'', {}, {**payload, 'sign': 'x' * 64})


def test_severpay_web_uses_its_own_key():
    """Два вебхука отличались только ключом — теперь это один класс."""
    web = SeverPayProvider('another-key', code='severpay_web')
    with pytest.raises(SignatureError):
        web.verify(b'', {}, sever_payload())
    assert web.code == 'severpay_web'


def test_tribute_accepts_hex_and_base64_signature():
    provider = TributeProvider(TRIBUTE_KEY)
    body = b'{"name":"new_donation"}'
    digest = hmac.new(TRIBUTE_KEY.encode(), body, hashlib.sha256)

    provider.verify(body, {'trbt-signature': digest.hexdigest()}, {})
    provider.verify(body, {'trbt-signature': base64.b64encode(digest.digest()).decode()}, {})

    with pytest.raises(SignatureError):
        provider.verify(body, {'trbt-signature': 'nope'}, {})
    with pytest.raises(SignatureError):
        provider.verify(body, {}, {})


# ── разбор ──────────────────────────────────────────────────────────────────
def test_heleket_parses_user_and_amount():
    event = HeleketProvider(HELEKET_KEY).parse(heleket_payload())
    assert event.handled and event.user_id == 802421217 and event.amount == 150


def test_heleket_ignores_unfinished_payment():
    assert HeleketProvider(HELEKET_KEY).parse(heleket_payload(is_final=False)).handled is False


def test_wata_subtracts_gateway_fee():
    """Оплатил 105 с комиссией 5% — на баланс идут 100."""
    event = WataProvider('t').parse({
        'transactionStatus': 'Paid', 'amount': '105',
        'orderDescription': 'Пополнение баланса 802421217', 'transactionId': 'w-1'})
    assert event.amount == 100 and event.user_id == 802421217


def test_wata_ignores_unpaid():
    assert WataProvider('t').parse({'transactionStatus': 'Pending'}).handled is False


def test_tribute_converts_minor_units():
    event = TributeProvider(TRIBUTE_KEY).parse({
        'name': 'new_donation', 'created_at': '2026-08-05T10:00:00Z',
        'payload': {'telegram_user_id': 5, 'amount': 15000, 'currency': 'rub',
                    'donation_request_id': 77}})
    assert event.amount == 150 and event.user_id == 5
    assert event.txid == 'tribute_new_donation_77_2026-08-05T10:00:00Z_5_15000'


def test_tribute_txid_is_stable_between_retries():
    provider = TributeProvider(TRIBUTE_KEY)
    payload = {'name': 'new_donation', 'created_at': '2026-08-05T10:00:00Z',
               'payload': {'telegram_user_id': 5, 'amount': 15000, 'currency': 'rub',
                           'donation_request_id': 77}}
    first = provider.parse(payload)
    retry = provider.parse({**payload, 'sent_at': '2026-08-05T10:00:09Z'})
    assert first.txid == retry.txid


def test_tribute_foreign_currency_goes_to_manual():
    event = TributeProvider(TRIBUTE_KEY).parse({
        'name': 'new_donation', 'payload': {'telegram_user_id': 5, 'amount': 1000,
                                            'currency': 'usd'}})
    assert event.handled is False and 'usd' in event.note


def test_tribute_finds_user_id_in_anonymous_message():
    event = TributeProvider(TRIBUTE_KEY).parse({
        'name': 'new_donation', 'created_at': 'x',
        'payload': {'message': 'оплата для 802421217', 'amount': 10000, 'currency': 'rub'}})
    assert event.user_id == 802421217


def test_cloudpayments_parses_form():
    event = CloudPaymentsProvider().parse({
        'Status': 'Completed', 'AccountId': '802421217', 'Amount': '100.00',
        'InvoiceId': 'cp-1'})
    assert event.user_id == 802421217 and event.amount == 100


async def test_cardlink_resolves_user_from_bill(container):
    await container.db['cardlink_bills'].insert_one({
        'provider': 'cardlink', 'payment_id': 'trs-1', 'user_id': 802421217, 'status': 'new'})

    provider = CardlinkProvider('token')
    event = provider.parse({'Status': 'SUCCESS', 'TrsId': 'trs-1', 'OutSum': '250'})
    assert event.amount == 250 and event.user_id is None

    assert await provider.resolve_user(event, container) == 802421217
    bill = await container.db['cardlink_bills'].find_one({'payment_id': 'trs-1'})
    assert bill['status'] == 'success'


async def test_cardlink_unknown_bill_gives_no_user(container):
    provider = CardlinkProvider('token')
    event = provider.parse({'Status': 'SUCCESS', 'TrsId': 'ghost', 'Amount': '100'})
    assert await provider.resolve_user(event, container) is None


def test_providers_without_signature_are_marked():
    """Явный флаг вместо тихого отсутствия проверки."""
    assert WataProvider('t').verified is False
    assert CardlinkProvider('t').verified is False
    assert HeleketProvider('k').verified is True
    assert TributeProvider('k').verified is True
