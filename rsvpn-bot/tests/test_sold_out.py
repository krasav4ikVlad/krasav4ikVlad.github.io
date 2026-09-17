"""«Серверы закончились»: закрытая продажа личных серверов.

Выключенный приём заявок раньше был виден только на списании: тарифы
показывались как обычно, локация и протокол выбирались, и лишь по кнопке
«Оплатить» всплывало «временно недоступны». Человек проходил всю покупку,
чтобы получить отказ за свой же выбор.

Проверяется поэтому не то, что появился текст, а то, что мимо закрытой
продажи не проходит ни одна кнопка — включая ту, что осталась на экране с
той минуты, когда серверы ещё были.
"""

import pytest

from app.admin.private_servers import queue_text
from app.bot.callbacks import Server
from app.bot.handlers import private_servers as h

from tests.test_admin_panel import ADMIN, admin_env, callback  # noqa: F401
from tests.test_private_servers import owner_with_money


class FakeEvent:
    """Ловит то, что ушло бы в Telegram: экран собирается через render()."""

    def __init__(self, user_id: int = 1):
        self.texts: list[str] = []
        self.markups: list = []
        self.alerts: list[str] = []
        self.from_user = type('U', (), {'id': user_id})()

    async def answer(self, text: str = '', reply_markup=None,
                     show_alert: bool = False, **kwargs):
        if show_alert:
            self.alerts.append(text)
        else:
            self.texts.append(text)
            self.markups.append(reply_markup)
        return True

    @property
    def text(self) -> str:
        return self.texts[-1] if self.texts else ''

    def buttons(self) -> list[str]:
        markup = self.markups[-1] if self.markups else None
        return [button.text
                for row in (markup.inline_keyboard if markup else [])
                for button in row]


@pytest.fixture
async def env(container):
    await container.startup()
    await owner_with_money(container.users, 1)
    return container, container.settings, await container.users.get(1)


async def closed(settings):
    await settings.set('private.enabled', False)


# ── витрина ─────────────────────────────────────────────────────────────────
async def test_the_shop_shows_the_tariffs_while_we_sell(env):
    c, settings, user = env
    event = FakeEvent()

    await h.shop(event, c, user, settings)

    assert any('₽' in title for title in event.buttons())
    assert 'закончились' not in event.text


async def test_a_closed_shop_says_the_servers_ran_out(env):
    """Главное здесь — что кнопок тарифов на экране не осталось: нажимать
    на то, чего не продают, человек не должен."""
    c, settings, user = env
    await closed(settings)
    event = FakeEvent()

    await h.shop(event, c, user, settings)

    assert 'закончились' in event.text
    assert not [title for title in event.buttons() if '₽' in title]


async def test_the_closed_shop_text_is_yours_to_write(env):
    c, settings, user = env
    await closed(settings)
    await settings.set('private.sold_out_note', 'Машин нет, ждём поставку.')
    event = FakeEvent()

    await h.shop(event, c, user, settings)

    assert 'Машин нет, ждём поставку.' in event.text


async def test_an_empty_text_still_says_something(env):
    """Стёрли настройку — экран обязан остаться понятным."""
    c, settings, user = env
    await closed(settings)
    await settings.set('private.sold_out_note', '   ')
    event = FakeEvent()

    await h.shop(event, c, user, settings)

    assert 'закончились' in event.text


async def test_someone_who_already_has_a_server_still_sees_it(env):
    """Закрытая продажа — про новых покупателей. У владельца ничего не
    меняется: он платит дальше и должен видеть свой сервер."""
    c, settings, user = env
    await c.private.request(1, 'mini', location='nl', profile='reality')
    await closed(settings)
    event = FakeEvent()

    await h.entry(event, c, user, settings)

    assert 'закончились' not in event.text
    assert 'Сервер готовится' in event.text


# ── кнопки, оставшиеся на старом экране ─────────────────────────────────────
async def test_a_stale_tariff_button_does_not_open_the_locations(env):
    c, settings, user = env
    await closed(settings)
    event = FakeEvent()

    await h.choose_location(event, Server(action='buy', value='mini'), c, user, settings)

    assert 'закончились' in event.text


async def test_a_stale_location_button_does_not_open_the_protocols(env):
    c, settings, user = env
    await closed(settings)
    event = FakeEvent()

    await h.choose_profile(event, Server(action='loc', value='mini-nl'), c, user, settings)

    assert 'закончились' in event.text


async def test_a_stale_protocol_button_does_not_reach_the_payment(env):
    c, settings, user = env
    await closed(settings)
    event = FakeEvent()

    await h.buy_confirm(event, Server(action='prof', value='mini-nl-reality'),
                        c, user, settings)

    assert 'закончились' in event.text
    assert not [title for title in event.buttons() if 'Оплатить' in title]


async def test_closing_the_sale_between_the_screen_and_the_tap_takes_no_money(env):
    """Последняя защита — сам сервис: экран мог открыться, пока продавали."""
    c, settings, user = env
    event = FakeEvent()

    await settings.set('private.enabled', False)
    await h.order(event, Server(action='order', value='mini-nl-reality'),
                  c, user, settings)

    assert (await c.users.get(1))['info']['balance'] == 3000
    assert await c.private.servers.of_owner(1) is None
    assert 'закончились' in event.text


async def test_the_refusal_is_a_screen_and_not_a_popup(env):
    """Всплывающее «серверы закончились» поверх экрана «Оплатить 990₽»
    читается как сбой бота, а не как ответ."""
    c, settings, user = env
    await closed(settings)
    event = FakeEvent()

    await h.order(event, Server(action='order', value='mini-nl-reality'),
                  c, user, settings)

    assert event.alerts == []


# ── админка ─────────────────────────────────────────────────────────────────
async def test_the_queue_says_when_the_sale_is_closed(env):
    """Иначе пустая очередь читается как «спрос кончился», а не как
    «мы не продаём»."""
    c, settings, _ = env
    await closed(settings)

    assert 'Продажа закрыта' in await queue_text(c, settings)


async def test_the_queue_says_nothing_extra_while_we_sell(env):
    c, settings, _ = env

    assert 'Продажа закрыта' not in await queue_text(c, settings)


async def test_the_button_closes_the_sale(admin_env):
    from app.bot.callbacks import Admin as Adm

    dp, bot, session, container = admin_env
    await container.users.create({'user_data': {'user_id': ADMIN.id}})

    await dp.feed_update(bot, callback(Adm(act='srvsale').pack()))

    assert await container.settings.flag('private.enabled') is False
    assert 'Продажа закрыта' in session.last_text


async def test_the_same_button_opens_it_back(admin_env):
    """Кнопка одна и та же: закрывать продажу навсегда никто не собирался."""
    from app.bot.callbacks import Admin as Adm

    dp, bot, session, container = admin_env
    await container.users.create({'user_data': {'user_id': ADMIN.id}})
    await container.settings.set('private.enabled', False)

    await dp.feed_update(bot, callback(Adm(act='srvsale').pack()))

    assert await container.settings.flag('private.enabled') is True
    assert 'Продажа закрыта' not in session.last_text


async def test_closing_the_sale_is_written_down_with_who_did_it(admin_env):
    """Продажа закрыта, а никто не закрывал — обычный конец такой истории."""
    from app.bot.callbacks import Admin as Adm

    dp, bot, session, container = admin_env
    await container.users.create({'user_data': {'user_id': ADMIN.id}})

    await dp.feed_update(bot, callback(Adm(act='srvsale').pack()))

    records = await container.db['bot_settings_audit'].find(
        {'key': 'private.enabled'}).to_list(length=10)
    assert [(row['after'], row['admin_id']) for row in records] == [(False, ADMIN.id)]


async def test_the_closed_sale_is_visible_on_the_queue_button(admin_env):
    """На закрытой продаже кнопка обязана предлагать открыть, а не закрыть:
    иначе второе нажатие включает продажу в уверенности, что выключает."""
    from app.bot.callbacks import Admin as Adm

    dp, bot, session, container = admin_env
    await container.users.create({'user_data': {'user_id': ADMIN.id}})
    await container.settings.set('private.enabled', False)

    session.all_markups.clear()
    await dp.feed_update(bot, callback(Adm(act='srvq').pack()))

    titles = [button.text for markup in session.all_markups
              for row in markup.inline_keyboard for button in row]
    assert any('Открыть продажу' in title for title in titles)
    assert not any('Закрыть продажу' in title for title in titles)
