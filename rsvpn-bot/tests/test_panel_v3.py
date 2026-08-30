"""Панель Remnawave 3.x: пользователя опознаёт число, а не uuid.

С версии 3.0 поле `uuid` из ответов убрано совсем, вместо него числовой
`id`, и тело PATCH тоже переехало на `id`. Пути остались той же формы,
поэтому весь бот продолжает хранить ссылку на пользователя в vpn.uuid, а
клиент по виду значения решает, с какой панелью говорит.

Работать должны обе версии одновременно, и это не запас прочности: в день
обновления панели у старых подписок в базе ещё uuid, у новых уже id, и
перемешанная база — нормальное состояние, а не авария.
"""

from datetime import timedelta

import pytest

from app.core.errors import VpnPanelError
from app.core.time import now
from app.integrations.vpn.remnawave import RemnawaveClient, ref_field, user_ref
from app.services import panel_ids
from app.settings.service import SettingsService

from tests.test_remnawave import FakeHttp, FakeResponse

# Ответ панели 3.x: id вместо uuid, самого uuid нет
V3_USER = {'id': 4271, 'shortUuid': 's-1', 'username': '7',
           'expireAt': (now() + timedelta(days=30)).isoformat()}
V2_USER = {'uuid': 'a1b2c3d4-0000-0000-0000-000000000000', 'shortUuid': 's-1',
           'username': '7'}


def api(db, responses):
    http = FakeHttp(responses)
    return RemnawaveClient('https://panel', 'token', http,
                           SettingsService(db['bot_settings'])), http


def ok(payload):
    return FakeResponse(200, {'response': payload})


# ── опознание ───────────────────────────────────────────────────────────────
def test_the_reference_is_read_from_whatever_the_panel_answered():
    assert user_ref(V3_USER) == 4271
    assert user_ref(V2_USER) == V2_USER['uuid']
    assert user_ref({}) is None


def test_the_body_field_follows_the_kind_of_reference():
    assert ref_field(4271) == 'id' and ref_field('4271') == 'id'
    assert ref_field(V2_USER['uuid']) == 'uuid'


# ── запросы ─────────────────────────────────────────────────────────────────
async def test_a_new_subscription_keeps_the_numeric_id(db):
    """Без подстановки подписка сохранилась бы без ссылки на панель."""
    client, _ = api(db, [FakeResponse(201, {'response': V3_USER})])

    created = await client.create_subscription(7, days=30)

    assert created['uuid'] == 4271, 'сервисы читают uuid и кладут его в базу'


async def test_created_is_answered_with_201(db):
    """Панель 3.x отвечает на создание 201, а не 200."""
    client, _ = api(db, [FakeResponse(201, {'response': V3_USER})])

    assert await client.create_subscription(7, days=30)


async def test_patch_identifies_by_id_on_the_new_panel(db):
    client, http = api(db, [ok(V3_USER)])

    await client.update_subscription(4271, expire_at=now())

    payload = http.last_payload
    assert payload['id'] == 4271
    assert 'uuid' not in payload, 'лишнее поле панель 3.x отвергает проверкой схемы'


async def test_patch_still_identifies_by_uuid_on_the_old_panel(db):
    client, http = api(db, [ok(V2_USER)])

    await client.update_subscription(V2_USER['uuid'], expire_at=now())

    assert http.last_payload['uuid'] == V2_USER['uuid'] and 'id' not in http.last_payload


async def test_deleting_a_user_accepts_an_empty_204(db):
    """Удаление в 3.x отвечает 204 без тела — это успех, а не отказ."""
    client, _ = api(db, [FakeResponse(204, None)])

    assert await client.delete_subscription(4271) is True


async def test_a_background_operation_accepts_an_empty_202(db):
    client, _ = api(db, [FakeResponse(202, None)])

    assert await client.get_subscription(4271) == {}


async def test_unhooking_a_device_names_the_user_by_id(db):
    client, http = api(db, [ok({})])

    await client.delete_device(4271, 'hwid-1')

    assert http.last_payload == {'userId': 4271, 'hwid': 'hwid-1'}


async def test_unhooking_a_device_on_the_old_panel_is_unchanged(db):
    client, http = api(db, [ok({})])

    await client.delete_device(V2_USER['uuid'], 'hwid-1')

    assert http.last_payload == {'userUuid': V2_USER['uuid'], 'hwid': 'hwid-1'}


async def test_squad_usage_is_matched_by_username_too(db):
    """Что означает `id` в статистике, у версий своё; username — всегда наш."""
    client, _ = api(db, [ok({'users': [{'id': 4271, 'username': '802421217',
                                        'totalBytes': 500}]})])

    usage = await client.squad_usage('squad-1', now() - timedelta(days=1), now())

    assert usage[4271] == 500 and usage['802421217'] == 500


async def test_an_interrupted_handover_is_picked_up_on_the_new_panel(db):
    """Повтор выдачи должен работать и там, где uuid больше нет."""
    client, http = api(db, [
        FakeResponse(400, {}, text='User with this short UUID already exists'),
        ok(V3_USER),
        ok(V3_USER),
    ])

    created = await client.create_subscription(7, days=30)

    assert created['uuid'] == 4271
    assert http.calls[-1][2]['id'] == 4271, 'доводим запись по числовому id'


# ── переезд базы ────────────────────────────────────────────────────────────
class Panel:
    def __init__(self, answer):
        self.answer = answer
        self.asked: list[str] = []

    async def find_by_short_uuid(self, short_uuid):
        self.asked.append(short_uuid)
        return dict(self.answer, shortUuid=short_uuid) if self.answer else {}


async def test_nothing_happens_while_the_panel_is_still_old(container, user_factory):
    """Задача включена постоянно, поэтому на старой панели она обязана молчать."""
    await user_factory(**{'vpn.uuid': V2_USER['uuid'], 'vpn.shortUuid': 's-1'})
    panel = Panel(V2_USER)

    report = await panel_ids.migrate(container.users, panel, apply=True)

    assert report['panel'] == 'old' and report['moved'] == 0
    assert len(panel.asked) == 1, 'одна проверка версии, а не запрос на человека'


async def test_old_subscriptions_move_to_numeric_ids(container, db, user_factory):
    await user_factory(**{'user_data.user_id': 7, 'vpn.uuid': V2_USER['uuid'],
                          'vpn.shortUuid': 's-1'})
    panel = Panel(V3_USER)

    report = await panel_ids.migrate(container.users, panel, apply=True)

    assert report['moved'] == 1
    assert (await container.users.get(7))['vpn']['uuid'] == 4271


async def test_bypass_moves_too(container, user_factory):
    """У ByPass своя запись в панели и свой короткий идентификатор."""
    await user_factory(**{'user_data.user_id': 7, 'vpn.uuid': V2_USER['uuid'],
                          'vpn.shortUuid': 's-1',
                          'vpn.bypass_uuid': 'b1b2c3d4-0000-0000-0000-000000000000',
                          'vpn.bypass_shortUuid': 'bs-1'})
    panel = Panel(V3_USER)

    await panel_ids.migrate(container.users, panel, apply=True)

    vpn = (await container.users.get(7))['vpn']
    assert vpn['uuid'] == 4271 and vpn['bypass_uuid'] == 4271
    assert 'bs-1' in panel.asked


async def test_already_migrated_users_are_left_alone(container, user_factory):
    await user_factory(**{'vpn.uuid': 4271, 'vpn.shortUuid': 's-1'})

    report = await panel_ids.migrate(container.users, Panel(V3_USER), apply=True)

    assert report['panel'] == 'nothing' and report['stale'] == 0


async def test_a_silent_panel_is_not_reported_as_nothing_to_do(container, user_factory):
    """«Панель не ответила» и «переезжать некуда» — разные ответы."""
    await user_factory(**{'vpn.uuid': V2_USER['uuid'], 'vpn.shortUuid': 's-1'})

    class Broken(Panel):
        async def find_by_short_uuid(self, short_uuid):
            raise VpnPanelError('HTTP 500')

    report = await panel_ids.migrate(container.users, Broken({}), apply=True)

    assert report['panel'] == 'unknown' and report['moved'] == 0


async def test_a_showing_run_changes_nothing(container, user_factory):
    await user_factory(**{'user_data.user_id': 7, 'vpn.uuid': V2_USER['uuid'],
                          'vpn.shortUuid': 's-1'})

    report = await panel_ids.migrate(container.users, Panel(V3_USER), apply=False)

    assert report['panel'] == 'new' and report['stale'] == 1 and report['moved'] == 0
    assert (await container.users.get(7))['vpn']['uuid'] == V2_USER['uuid']


async def test_a_subscription_the_panel_lost_is_counted_not_hidden(container,
                                                                   user_factory):
    """Панель не нашла подписку — это строка в отчёте, а не тихий пропуск."""
    await user_factory(**{'user_data.user_id': 7, 'vpn.uuid': V2_USER['uuid'],
                          'vpn.shortUuid': 's-1'})

    class Half(Panel):
        async def find_by_short_uuid(self, short_uuid):
            self.asked.append(short_uuid)
            return V3_USER if len(self.asked) == 1 else {}

    report = await panel_ids.migrate(container.users, Half(V3_USER), apply=True)

    assert report['failed'] == 1 and report['moved'] == 0


async def test_a_subscription_without_a_stored_short_id_still_moves(container,
                                                                   user_factory):
    """Записи из старого бота приехали без shortUuid — он считается от id."""
    from app.integrations.vpn.remnawave import subscription_token

    await user_factory(**{'user_data.user_id': 802421217,
                          'vpn.uuid': V2_USER['uuid'], 'vpn.shortUuid': ''})
    panel = Panel(V3_USER)

    report = await panel_ids.migrate(container.users, panel, apply=True)

    assert report['moved'] == 1
    assert panel.asked == [subscription_token(802421217)] * 2
