"""Способы вывода: описание (domain) и хранение (service)."""

import pytest

from app.domain import payout_methods as pm
from app.services.payouts import MAX_METHODS, PayoutService


# ── чистая логика ───────────────────────────────────────────────────────────
def test_missing_fields_lists_what_is_not_filled():
    draft = {'type': 'sbp', 'data': {'fio': 'Иванов Иван', 'phone': '', 'bank': ''}}
    assert [f.code for f in pm.missing_fields(draft)] == ['phone', 'bank']
    assert not pm.is_ready(draft)


def test_ready_draft_has_no_missing_fields():
    draft = {'type': 'mir', 'data': {'fio': 'Иванов Иван', 'card': '2200123412341234'}}
    assert pm.is_ready(draft)


def test_whitespace_does_not_count_as_filled():
    assert not pm.is_ready({'type': 'crypto', 'data': {'wallet': '   '}})


def test_changing_type_gives_the_fields_of_that_type():
    assert [f.code for f in pm.fields_of('crypto')] == ['wallet']
    assert pm.empty_data('mir') == {'fio': '', 'card': ''}


@pytest.mark.parametrize('card, expected', [
    ('2200123412341234', '220012******1234'),
    ('2200 1234 1234 1234', '220012******1234'),
    ('1234', '1234'),                       # слишком коротко — маскировать нечего
])
def test_card_is_masked(card, expected):
    assert pm.mask_card(card) == expected


def test_user_sees_masked_card_admin_sees_full():
    """Скриншот экрана не должен раскрывать номер карты, а платить админу надо."""
    method = {'type': 'mir', 'data': {'fio': 'Иванов Иван', 'card': '2200123412341234'}}

    assert '220012******1234' in pm.format_details(method)
    assert '2200123412341234' not in pm.format_details(method)
    assert '2200123412341234' in pm.format_details(method, mask=False)


def test_unknown_type_does_not_crash_the_screen():
    assert pm.format_details({'type': 'пришло из будущего'}) == 'Неизвестный тип способа.'
    assert pm.method_title(None) == 'Способ'


# ── сервис ──────────────────────────────────────────────────────────────────
@pytest.fixture
def payouts(container):
    return PayoutService(container.users, container.settings)


async def filled_draft(payouts, user_id, type_code='sbp', **values):
    await payouts.set_draft_type(user_id, type_code)
    for field, value in values.items():
        await payouts.set_draft_field(user_id, field, value)


async def test_method_is_saved_and_selected(payouts, user_factory):
    user = await user_factory()
    uid = user['user_data']['user_id']

    await filled_draft(payouts, uid, 'sbp', fio='Иванов Иван', phone='+79001234567',
                       bank='Сбербанк')
    ok, problem = await payouts.add_method(uid)

    assert ok, problem
    saved = await payouts.methods(uid)
    assert len(saved) == 1
    assert saved[0]['data']['bank'] == 'Сбербанк'

    # только что добавленный способ сразу выбран — иначе заявка уйдёт «на баланс»
    fresh = await payouts.users.get(uid)
    assert fresh['info']['ref_stats']['payout_selected'] == saved[0]['id']


async def test_incomplete_method_is_refused_with_the_missing_fields(payouts, user_factory):
    user = await user_factory()
    uid = user['user_data']['user_id']

    await filled_draft(payouts, uid, 'sbp', fio='Иванов Иван')
    ok, problem = await payouts.add_method(uid)

    assert not ok
    assert 'Телефон' in problem and 'Банк' in problem
    assert await payouts.methods(uid) == []


async def test_changing_type_clears_fields_of_the_previous_one(payouts, user_factory):
    """Иначе в СБП уехал бы номер карты, введённый для МИР."""
    user = await user_factory()
    uid = user['user_data']['user_id']

    await filled_draft(payouts, uid, 'mir', fio='Иванов Иван', card='2200123412341234')
    draft = await payouts.set_draft_type(uid, 'sbp')

    assert draft['data'] == {'fio': '', 'phone': '', 'bank': ''}


async def test_field_from_another_type_is_ignored(payouts, user_factory):
    user = await user_factory()
    uid = user['user_data']['user_id']

    await payouts.set_draft_type(uid, 'crypto')
    draft = await payouts.set_draft_field(uid, 'card', '2200123412341234')

    assert 'card' not in draft['data']


async def test_number_of_methods_is_capped(payouts, user_factory):
    user = await user_factory()
    uid = user['user_data']['user_id']

    for i in range(MAX_METHODS):
        await filled_draft(payouts, uid, 'crypto', wallet=f'wallet-{i}')
        assert (await payouts.add_method(uid))[0]

    await filled_draft(payouts, uid, 'crypto', wallet='ещё один')
    ok, problem = await payouts.add_method(uid)
    assert not ok
    assert str(MAX_METHODS) in problem


async def test_deleting_the_selected_method_falls_back_to_bot_balance(payouts, user_factory):
    """Иначе заявка уедет с ссылкой на удалённые реквизиты."""
    user = await user_factory()
    uid = user['user_data']['user_id']

    await filled_draft(payouts, uid, 'crypto', wallet='TRC-20-адрес')
    await payouts.add_method(uid)
    method_id = (await payouts.methods(uid))[0]['id']

    await payouts.delete_method(uid, method_id)

    fresh = await payouts.users.get(uid)
    assert await payouts.methods(uid) == []
    assert fresh['info']['ref_stats']['payout_selected'] == pm.BOT_BALANCE


async def test_selecting_an_unknown_method_falls_back_to_bot_balance(payouts, user_factory):
    user = await user_factory()
    uid = user['user_data']['user_id']

    assert await payouts.select_method(uid, 'нет такого') == pm.BOT_BALANCE


async def test_request_carries_the_requisites_for_the_admin(payouts, user_factory):
    """Без реквизитов в карточке админ не знает, куда платить."""
    user = await user_factory(**{'info.ref_stats.withdrawable': 900,
                                 'info.ref_stats.referrals': []})
    uid = user['user_data']['user_id']

    await filled_draft(payouts, uid, 'mir', fio='Иванов Иван', card='2200123412341234')
    await payouts.add_method(uid)

    result = await payouts.request(uid)

    assert result.ok
    assert result.amount == 900
    assert '2200123412341234' in result.method_details      # админу — полный номер
    assert 'Иванов Иван' in result.method_details


async def test_request_without_methods_goes_to_bot_balance(payouts, user_factory):
    user = await user_factory(**{'info.ref_stats.withdrawable': 900})

    result = await payouts.request(user['user_data']['user_id'])

    assert result.ok
    assert result.method == pm.BOT_BALANCE
    assert result.method_details == pm.BOT_BALANCE_TITLE
