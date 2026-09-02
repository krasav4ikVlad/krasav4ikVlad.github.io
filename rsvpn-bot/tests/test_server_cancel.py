"""Отмена заявки на личный сервер и компенсация за ожидание.

Возврат денег сам по себе ничего не компенсирует: он возвращает исходное
положение. Неделя ожидания сервера, которого так и не будет, остаётся не
оплаченной ничем — поэтому к деньгам добавляется прибавка к следующему
пополнению.
"""

from datetime import timedelta

import pytest

from app.admin.private_servers import _days, cancel_letter, cancel_note
from app.core.time import now
from app.domain import private_servers as ps

from tests.test_private_servers import owner_with_money, service  # noqa: F401


async def waiting_request(service_tuple, days_ago: int, owner: int = 1):
    """Заявка, поданная столько-то дней назад и всё ещё не выданная."""
    srv, _, users = service_tuple
    await owner_with_money(users, owner)
    result = await srv.request(owner, 'mini', location='nl', profile='reality')
    await srv.servers.set(result.server['_id'],
                          created_at=now() - timedelta(days=days_ago))
    return result.server['_id']


async def test_cancelling_returns_the_money(service):
    srv, _, users = service
    server_id = await waiting_request(service, days_ago=0)

    result = await srv.reject(server_id)

    assert result.ok and result.amount == 990
    assert (await users.get(1))['info']['balance'] == 3000
    assert (await srv.servers.get(server_id))['status'] == ps.CANCELLED


async def test_a_long_wait_earns_a_bonus_on_the_next_topup(service):
    """Три дня ожидания — и человек получает прибавку сверх возврата."""
    srv, _, users = service
    server_id = await waiting_request(service, days_ago=5)

    result = await srv.reject(server_id)

    assert result.waited_days == 5
    assert result.bonus_rate == pytest.approx(0.15)
    assert (await users.get(1))['info']['bonus_multiplier'] == pytest.approx(0.15)


async def test_a_quick_cancel_earns_nothing(service):
    """Отменили в тот же день — извиняться не за что."""
    srv, _, users = service
    server_id = await waiting_request(service, days_ago=1)

    result = await srv.reject(server_id)

    assert result.bonus_rate == 0
    assert not (await users.get(1))['info'].get('bonus_multiplier')


async def test_the_threshold_day_itself_counts(service):
    """«Более трёх дней» на границе — три дня уже считаются ожиданием."""
    srv, _, users = service
    server_id = await waiting_request(service, days_ago=3)

    assert (await srv.reject(server_id)).bonus_rate == pytest.approx(0.15)


async def test_the_percent_and_the_term_come_from_settings(service):
    srv, _, users = service
    await srv.settings.set('private.wait_bonus_days', 7)
    await srv.settings.set('private.wait_bonus_rate', 0.3)
    server_id = await waiting_request(service, days_ago=5)

    assert (await srv.reject(server_id)).bonus_rate == 0, 'пять дней из семи'

    second = await waiting_request(service, days_ago=8, owner=2)
    assert (await srv.reject(second)).bonus_rate == pytest.approx(0.3)


async def test_the_bonus_can_be_switched_off(service):
    srv, _, users = service
    await srv.settings.set('private.wait_bonus_rate', 0)
    server_id = await waiting_request(service, days_ago=30)

    assert (await srv.reject(server_id)).bonus_rate == 0


async def test_an_existing_promise_is_never_taken_away(service):
    """Прибавку могли пообещать за другое: у человека одно поле на всё."""
    srv, _, users = service
    server_id = await waiting_request(service, days_ago=5)
    await users.promise_bonus(1, 0.5)

    await srv.reject(server_id)

    assert (await users.get(1))['info']['bonus_multiplier'] == pytest.approx(0.5)


async def test_a_bigger_apology_replaces_a_smaller_one(service):
    srv, _, users = service
    server_id = await waiting_request(service, days_ago=5)
    await users.promise_bonus(1, 0.05)

    await srv.reject(server_id)

    assert (await users.get(1))['info']['bonus_multiplier'] == pytest.approx(0.15)


async def test_the_promised_bonus_is_actually_paid_on_the_next_topup(service, db):
    """Обещание бесполезно, если пополнение о нём не знает."""
    from app.repositories.payments import PaymentsRepository
    from app.services.topup import TopupService

    srv, _, users = service
    server_id = await waiting_request(service, days_ago=5)
    await srv.reject(server_id)

    topup = TopupService(users, PaymentsRepository(db['payments']), srv.settings)
    await topup.process(provider='cardlink', txid='t-1', amount=100, user_id=1)

    # 100₽ платежа, 20₽ обычного бонуса и 15₽ извинения — оно идёт сверх
    # обычного, а не вместо него.
    balance = (await users.get(1))['info']['balance']
    assert balance == 3000 + 100 + 20 + 15
    assert not (await users.get(1))['info']['bonus_multiplier'], 'прибавка разовая'


async def test_the_second_topup_gets_no_apology(service, db):
    """Прибавка разовая: второе пополнение идёт по обычному бонусу."""
    from app.repositories.payments import PaymentsRepository
    from app.services.topup import TopupService

    srv, _, users = service
    await srv.reject(await waiting_request(service, days_ago=5))

    topup = TopupService(users, PaymentsRepository(db['payments']), srv.settings)
    await topup.process(provider='cardlink', txid='t-1', amount=100, user_id=1)
    before = (await users.get(1))['info']['balance']
    await topup.process(provider='cardlink', txid='t-2', amount=100, user_id=1)

    assert (await users.get(1))['info']['balance'] == before + 120


async def test_a_server_already_handed_out_is_not_cancelled(service):
    """У работающего сервера свой путь: там надо снимать доступ, а не закрывать заявку."""
    srv, _, users = service
    server_id = await waiting_request(service, days_ago=5)
    await srv.activate(server_id, 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    result = await srv.reject(server_id)

    assert not result.ok and result.reason == 'wrong_status'
    assert not (await users.get(1))['info'].get('bonus_multiplier')


# ── как это читается человеком ──────────────────────────────────────────────
def test_days_are_declined_like_a_human_wrote_them():
    assert _days(1) == '1 день' and _days(3) == '3 дня' and _days(5) == '5 дней'
    assert _days(11) == '11 дней' and _days(21) == '21 день'


class Result:
    ok, server, reason, note = True, {'_id': 'srv_1', 'owner_id': 1}, '', ''

    def __init__(self, amount=990, waited_days=0, bonus_rate=0.0):
        self.amount, self.waited_days, self.bonus_rate = amount, waited_days, bonus_rate


def test_the_letter_leads_with_the_money():
    """Первое, о чём человек подумает, — где его деньги."""
    letter = cancel_letter(Result(waited_days=5, bonus_rate=0.15))

    assert letter.index('990₽') < letter.index('15%')
    assert '5 дней' in letter


def test_the_letter_passes_the_reason_through():
    letter = cancel_letter(Result(), reason='закончились машины в Токио')

    assert 'закончились машины в Токио' in letter


def test_a_letter_without_a_bonus_does_not_mention_one():
    letter = cancel_letter(Result(waited_days=1))

    assert '990₽' in letter and '%' not in letter


def test_the_card_says_what_the_person_got():
    note = cancel_note(Result(waited_days=5, bonus_rate=0.15))

    assert '990₽' in note and '5 дней' in note and '+15%' in note
