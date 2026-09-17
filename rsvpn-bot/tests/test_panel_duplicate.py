"""Прерванная выдача: в панели подписка есть, в боте её нет.

Самый неприятный из известных случаев. Бот отправляет POST /api/users,
панель пользователя создаёт, а ответ до бота не доходит — оборванное
соединение, таймаут, перезапуск. Деньги бот возвращает, у себя ничего не
сохраняет, человек видит «Сервис подписок не отвечает».

Дальше начиналось непоправимое: shortUuid считается от user_id, поэтому
каждая следующая попытка упиралась в «short UUID already exists». Купить
подписку человек не мог уже никогда — ни в этот день, ни через месяц.
"""

from datetime import timedelta

import pytest

from app.core.errors import VpnPanelError
from app.core.time import now, parse_dt
from app.integrations.vpn.remnawave import RemnawaveClient
from app.settings.service import SettingsService

from tests.test_remnawave import FakeHttp, FakeResponse

EXISTS = FakeResponse(400, {}, text='User with this short UUID already exists')


def found(expire_at, **extra):
    """Ответ панели про уже существующего пользователя."""
    return FakeResponse(200, {'response': dict(
        {'uuid': 'u-1', 'shortUuid': 's-1', 'username': '7',
         'expireAt': expire_at.isoformat(), 'createdAt': now().isoformat(),
         'activeInternalSquads': [{'uuid': 'squad-1'}]}, **extra)})


def patched():
    return FakeResponse(200, {'response': {'uuid': 'u-1', 'shortUuid': 's-1'}})


def api_with(db, responses):
    http = FakeHttp(responses)
    return RemnawaveClient('https://panel', 'token', http,
                           SettingsService(db['bot_settings'])), http


async def test_a_second_attempt_picks_up_the_existing_record(db):
    """Ради этого всё и делалось: повтор проходит, а не отказывает."""
    api, http = api_with(db, [EXISTS, found(now() + timedelta(days=30)), patched()])

    result = await api.create_subscription(7, days=30)

    assert result['uuid'] == 'u-1' and result['shortUuid'] == 's-1'
    assert [call[0] for call in http.calls] == ['POST', 'GET', 'PATCH']


async def test_the_paid_days_are_not_added_twice(db):
    """Запись уже с оплаченным сроком — второй раз те же дни не начисляем."""
    already = now() + timedelta(days=30)
    api, http = api_with(db, [EXISTS, found(already), patched()])

    await api.create_subscription(7, days=30)

    sent = parse_dt(http.calls[-1][2]['expireAt'])
    assert abs((sent - already).total_seconds()) < 60


async def test_a_stale_record_gets_the_purchased_term(db):
    """Старая протухшая запись — это не «уже оплачено»: срок ставим новый."""
    api, http = api_with(db, [EXISTS, found(now() - timedelta(days=100)), patched()])

    await api.create_subscription(7, days=30)

    sent = parse_dt(http.calls[-1][2]['expireAt'])
    assert abs((sent - (now() + timedelta(days=30))).total_seconds()) < 60


async def test_a_longer_subscription_is_never_shortened(db):
    api, http = api_with(db, [EXISTS, found(now() + timedelta(days=200)), patched()])

    await api.create_subscription(7, days=30)

    assert parse_dt(http.calls[-1][2]['expireAt']) > now() + timedelta(days=190)


async def test_the_record_is_brought_to_its_post_purchase_shape(db):
    """Найденная запись могла быть отключена или без сквадов — доводим её."""
    api, http = api_with(db, [EXISTS, found(now() + timedelta(days=30)), patched()])
    await SettingsService(db['bot_settings']).set('price.default_device_limit', 5)

    await api.create_subscription(7, days=30)

    payload = http.calls[-1][2]
    assert payload['status'] == 'ACTIVE'
    assert payload['hwidDeviceLimit'] == 5
    assert 'trafficLimitBytes' not in payload, 'у основной подписки лимита трафика нет'


async def test_squads_of_the_found_record_are_left_alone(db):
    """Пустой набор сквадов не должен стирать тот, что уже стоит в панели."""
    api, http = api_with(db, [EXISTS, found(now() + timedelta(days=30)), patched()])

    await api.create_subscription(7, days=30)

    assert 'activeInternalSquads' not in http.calls[-1][2]


async def test_the_search_falls_back_to_the_short_uuid(db):
    """Ручка поиска по имени есть не во всех версиях панели."""
    api, http = api_with(db, [EXISTS, FakeResponse(404, {}, text='not found'),
                              found(now() + timedelta(days=30)), patched()])

    await api.create_subscription(7, days=30)

    assert 'by-short-uuid' in http.calls[2][1]


async def test_a_real_refusal_is_still_an_error(db):
    """Подхватывать нужно только повтор: остальные отказы прятать нельзя."""
    api, _ = api_with(db, [FakeResponse(500, {}, text='internal error')])

    with pytest.raises(VpnPanelError) as exc:
        await api.create_subscription(7, days=30)
    assert 'HTTP 500' in str(exc.value)


async def test_a_duplicate_we_cannot_find_stays_an_error(db):
    """Молча отдать пустой результат нельзя: наверху решат, что всё хорошо."""
    api, _ = api_with(db, [EXISTS, FakeResponse(404, {}, text='not found'),
                           FakeResponse(404, {}, text='not found')])

    with pytest.raises(VpnPanelError):
        await api.create_subscription(7, days=30)


async def test_bypass_is_picked_up_the_same_way(db):
    """У ByPass shortUuid тоже считается от id — беда та же."""
    wanted = now() + timedelta(days=30)
    api, http = api_with(db, [EXISTS, found(now() + timedelta(days=200)), patched()])

    await api.create_bypass_subscription(7, expire_at=wanted)

    payload = http.calls[-1][2]
    assert abs((parse_dt(payload['expireAt']) - wanted).total_seconds()) < 60, \
        'срок ByPass равен сроку основной подписки, длинный чужой не бережём'
    assert payload['trafficLimitBytes'] > 0


# ── сквозь покупку ──────────────────────────────────────────────────────────
async def test_the_person_can_buy_again_after_an_interrupted_purchase(container):
    """Весь путь целиком: сорвалась выдача, деньги вернулись, повтор прошёл."""
    from app.services.billing import BillingService

    await container.startup()
    await container.users.create({'user_data': {'user_id': 7},
                                  'info': {'balance': 1000}})

    http = FakeHttp([
        FakeResponse(500, {}, text='connection reset'),   # ответ до бота не дошёл
        EXISTS,                                           # а пользователь в панели создан
        found(now() + timedelta(days=30)),
        patched(),
    ])
    api = RemnawaveClient('https://panel', 'token', http, container.settings)
    billing = BillingService(container.users, container.plans, container.settings,
                             api, container.topup, discounts=container.discounts)

    with pytest.raises(VpnPanelError):
        await billing.buy(7, '1month')
    assert (await container.users.get(7))['info']['balance'] == 1000, \
        'панель не ответила — деньги должны вернуться'

    result = await billing.buy(7, '1month')

    assert (await container.users.get(7))['vpn']['uuid'] == 'u-1'
    assert (await container.users.get(7))['info']['balance'] == 1000 - result['price']
