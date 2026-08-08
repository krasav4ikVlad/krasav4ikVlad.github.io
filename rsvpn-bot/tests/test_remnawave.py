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
    assert result == ['b', 'custom']


async def test_base_squad_is_added_to_everyone_when_set(db):
    """Базовый сквад — дополнение к ротационному, а не замена ему."""
    settings = SettingsService(db['bot_settings'])
    await settings.set('squads.base', 'база')
    await settings.set('squads.extra', 'a,b,c')
    service = SquadService(settings, db['settings_collection'])

    assert await service.for_new_user() == ['база', 'a']
    assert await service.for_existing_user(['b', 'custom']) == ['база', 'b', 'custom']


async def test_without_a_base_squad_the_user_gets_only_the_rotating_one(db):
    """Пустой «Базовый сквад» — по одному скваду на человека, из ротации."""
    settings = SettingsService(db['bot_settings'])
    service = SquadService(settings, db['settings_collection'])

    assert await settings.get('squads.base') == ''
    assert await service.for_new_user() == [SQUADS[0]]


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


# ── режим только чтения ─────────────────────────────────────────────────────
async def test_dry_run_does_not_send_changes(db):
    """Тестовый контур на копии боевых данных не должен править настоящую панель."""
    settings = SettingsService(db['bot_settings'])
    http = FakeHttp()
    api = RemnawaveClient('https://panel', 'token', http, settings, dry_run=True)

    await api.update_subscription('u-1', expire_at=now())
    await api.create_subscription(1, days=30)

    assert http.calls == []


async def test_dry_run_still_reads(db):
    settings = SettingsService(db['bot_settings'])
    http = FakeHttp([FakeResponse(200, {'response': {'devices': [{'hwid': 'a'}]}})])
    api = RemnawaveClient('https://panel', 'token', http, settings, dry_run=True)

    assert await api.devices('u-1') == [{'hwid': 'a'}]
    assert len(http.calls) == 1


# ── сквады из настроек по умолчанию ─────────────────────────────────────────
SQUADS = [
    'c55bee89-5fda-4a9e-8e8b-b5bbc12fc589',
    'e9b8d3d5-5409-4235-a5ba-403581986b2',
    'b174cf00-fe59-4463-9d9a-543ba07ccac3',
    'c4ba52b0-2d16-44e1-80db-a072d6ee053d',
    '528581b4-08ee-42e7-a461-f1757f183ff2',
]


def test_default_rotation_list_is_the_configured_one():
    from app.settings.schema import INDEX

    assert INDEX['squads.extra'].default.split(',') == SQUADS
    assert INDEX['squads.base'].default == ''


async def test_new_subscriptions_walk_the_list_in_order(db):
    """По кругу и по порядку: шестой человек получает первый сквад."""
    settings = SettingsService(db['bot_settings'])
    service = SquadService(settings, db['settings_collection'])

    issued = [(await service.for_new_user())[0] for _ in range(6)]

    assert issued == SQUADS + SQUADS[:1]


async def test_the_counter_is_shared_between_processes(db):
    """Бот и API — разные процессы, счётчик один: он лежит в Mongo."""
    settings = SettingsService(db['bot_settings'])
    bot = SquadService(settings, db['settings_collection'])
    api = SquadService(settings, db['settings_collection'])

    assert await bot.next_extra_squad() == SQUADS[0]
    assert await api.next_extra_squad() == SQUADS[1]


def test_bypass_squads_are_the_configured_ones():
    from app.settings.schema import INDEX

    assert INDEX['bypass.squad_uuid'].default == 'ac03f8c3-0de7-4380-9774-00079d0385ce'
    assert (INDEX['bypass.external_squad_uuid'].default
            == 'dd4fd59f-415a-4fab-8af7-afde9db099bc')


async def test_bypass_payload_carries_both_squads(db):
    """Внутренний сквад — списком, внешний — отдельным полем."""
    from datetime import timedelta

    from app.core.time import now

    settings = SettingsService(db['bot_settings'])
    http = FakeHttp()
    panel = RemnawaveClient('https://panel', 'token', http, settings)
    await panel.create_bypass_subscription(5, now() + timedelta(days=30))

    sent = http.last_payload
    assert sent['activeInternalSquads'] == ['ac03f8c3-0de7-4380-9774-00079d0385ce']
    assert sent['externalSquadUuid'] == 'dd4fd59f-415a-4fab-8af7-afde9db099bc'


# ── проверка настроек ───────────────────────────────────────────────────────
def test_uuid_check_catches_a_missing_character():
    """Ровно тот случай: в UUID 11 знаков в последней группе вместо 12."""
    from app.services.squads import looks_like_uuid

    assert looks_like_uuid('c55bee89-5fda-4a9e-8e8b-b5bbc12fc589')
    assert not looks_like_uuid('e9b8d3d5-5409-4235-a5ba-403581986b2')
    assert not looks_like_uuid('просто текст')
    assert not looks_like_uuid('')


async def test_broken_squad_is_reported_by_name(db):
    """Панель на такой сквад отвечает отказом, а человек к этому моменту
    уже заплатил. Дешевле сказать об этом при старте."""
    settings = SettingsService(db['bot_settings'])
    await settings.set('squads.extra', 'c55bee89-5fda-4a9e-8e8b-b5bbc12fc589,короткий')
    service = SquadService(settings, db['settings_collection'])

    problems = await service.problems()

    assert len(problems) == 1
    assert 'squads.extra' in problems[0] and 'короткий' in problems[0]


async def test_valid_settings_have_no_complaints(db):
    settings = SettingsService(db['bot_settings'])
    await settings.set('squads.extra',
                       'c55bee89-5fda-4a9e-8e8b-b5bbc12fc589,'
                       'b174cf00-fe59-4463-9d9a-543ba07ccac3')

    assert await SquadService(settings, db['settings_collection']).problems() == []
