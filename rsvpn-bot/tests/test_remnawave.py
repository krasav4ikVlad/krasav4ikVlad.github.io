"""Клиент панели и раскладка сквадов — на поддельном HTTP."""

import pytest

from app.core.errors import VpnPanelError
from app.integrations.vpn.remnawave import RemnawaveClient, subscription_token
from app.services.squads import SquadService, build_active_squads, unrank_combination
from app.settings.service import SettingsService
from app.core.time import now


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


class FakeHttp:
    """Записывает запросы и отдаёт заранее заданные ответы."""

    def __init__(self, responses=None):
        self.calls: list[tuple[str, str, dict]] = []
        self.responses = list(responses or [])

    async def request(self, method, url, headers=None, **kwargs):
        self.calls.append((method, url, kwargs.get('json') or {}))
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse(200, {'response': {'uuid': 'u-1', 'shortUuid': 's-1'}})

    @property
    def last_payload(self) -> dict:
        return self.calls[-1][2]


@pytest.fixture
def client(db):
    settings = SettingsService(db['bot_settings'])
    squads = SquadService(settings, db['settings_collection'], db['fingerprints'])
    http = FakeHttp()
    return RemnawaveClient('https://panel.example.com/', 'token', http, settings, squads), http


# ── токены ──────────────────────────────────────────────────────────────────
def test_token_is_stable_and_differs_for_bypass():
    assert subscription_token(802421217) == subscription_token(802421217)
    assert subscription_token(802421217) != subscription_token(802421217, bypass=True)
    assert subscription_token(1).islower()


# ── запросы ─────────────────────────────────────────────────────────────────
async def test_create_subscription_sends_expected_payload(client):
    api, http = client
    await api.create_subscription(802421217, days=30)

    method, url, payload = http.calls[0]
    assert (method, url) == ('POST', 'https://panel.example.com/api/users')
    assert payload['telegramId'] == 802421217
    assert payload['username'] == '802421217'
    assert payload['hwidDeviceLimit'] == 2          # из настроек, не из кода
    assert payload['status'] == 'ACTIVE'


async def test_device_limit_comes_from_settings(client, db):
    api, http = client
    await SettingsService(db['bot_settings']).set('price.default_device_limit', 5)
    await api.create_subscription(1, days=1)
    assert http.last_payload['hwidDeviceLimit'] == 5


async def test_update_is_partial(client):
    """Продление не должно обнулять лимиты — передаются только заданные поля."""
    api, http = client
    await api.update_subscription('u-1', expire_at=now())

    payload = http.last_payload
    assert set(payload) == {'uuid', 'expireAt'}
    assert 'trafficLimitBytes' not in payload and 'hwidDeviceLimit' not in payload


async def test_panel_error_raises(db):
    settings = SettingsService(db['bot_settings'])
    http = FakeHttp([FakeResponse(500, {}, text='boom')])
    api = RemnawaveClient('https://panel', 'token', http, settings)

    with pytest.raises(VpnPanelError) as exc:
        await api.create_subscription(1, days=1)
    assert 'HTTP 500' in str(exc.value)


async def test_network_error_becomes_domain_error(db):
    class Broken:
        async def request(self, *a, **kw):
            raise ConnectionError('нет сети')

    api = RemnawaveClient('https://panel', 'token', Broken(), SettingsService(db['bot_settings']))
    with pytest.raises(VpnPanelError):
        await api.devices('u-1')


async def test_devices_unwraps_response(db):
    http = FakeHttp([FakeResponse(200, {'response': {'devices': [{'hwid': 'a'}]}})])
    api = RemnawaveClient('https://panel', 'token', http, SettingsService(db['bot_settings']))
    assert await api.devices('u-1') == [{'hwid': 'a'}]


async def test_delete_device_returns_false_instead_of_raising(db):
    http = FakeHttp([FakeResponse(404, {}, text='not found')])
    api = RemnawaveClient('https://panel', 'token', http, SettingsService(db['bot_settings']))
    assert await api.delete_device('u-1', 'hwid-1') is False


# ── сквады ──────────────────────────────────────────────────────────────────
def test_build_squads_keeps_order_without_duplicates():
    assert build_active_squads('base', 'extra', ['extra', 'old']) == ['base', 'extra', 'old']
    assert build_active_squads('base', None, None) == ['base']


def test_unrank_combination_is_unique_per_index():
    combos = {tuple(unrank_combination(6, 3, i)) for i in range(20)}
    assert len(combos) == 20


async def test_extra_squads_rotate_round_robin(db):
    settings = SettingsService(db['bot_settings'])
    await settings.set('squads.extra', 'a, b, c')
    service = SquadService(settings, db['settings_collection'])

    picked = [await service.next_extra_squad() for _ in range(4)]
    assert picked == ['a', 'b', 'c', 'a']


async def test_existing_user_keeps_his_extra_squad(db):
    settings = SettingsService(db['bot_settings'])
    await settings.set('squads.extra', 'a,b,c')
    service = SquadService(settings, db['settings_collection'])

    result = await service.for_existing_user(['b', 'custom'])
    assert result == [await settings.get('squads.base'), 'b', 'custom']


async def test_legacy_fingerprints_are_not_restored(db):
    settings = SettingsService(db['bot_settings'])
    await settings.set('squads.extra', 'a')
    await settings.set('squads.fingerprint', 'f1,f2')
    service = SquadService(settings, db['settings_collection'])

    result = await service.for_existing_user(['f1', 'a', 'keep'])
    assert 'f1' not in result and 'keep' in result


async def test_fingerprint_assignment_is_stable_per_user(db):
    settings = SettingsService(db['bot_settings'])
    await settings.set('squads.fingerprint', ','.join(f's{i}' for i in range(8)))
    await settings.set('squads.fingerprint_pick', 3)
    service = SquadService(settings, db['settings_collection'], db['fingerprints'])

    first = await service.assign_fingerprint(1)
    again = await service.assign_fingerprint(1)
    other = await service.assign_fingerprint(2)

    assert len(first) == 3 and first == again
    assert other != first


async def test_running_out_of_combinations_does_not_break_purchase(db):
    """Раньше здесь вылетал RuntimeError — деньги списаны, подписки нет."""
    settings = SettingsService(db['bot_settings'])
    await settings.set('squads.fingerprint', 'a,b')
    await settings.set('squads.fingerprint_pick', 1)
    service = SquadService(settings, db['settings_collection'], db['fingerprints'])

    results = [await service.assign_fingerprint(i) for i in range(1, 6)]
    assert all(len(r) == 1 for r in results)
