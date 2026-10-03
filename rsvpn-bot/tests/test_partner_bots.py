"""Партнёрские боты: витрина, которая ведёт в основного с меткой партнёра.

Партнёр рекламирует своего бота, а не ссылку. Ссылку теряют — её вырезают
при пересылке, её не набирают руками, по ней не приходят те, кто нашёл бота
поиском. Имя бота, наоборот, и есть то, что запоминают.

Проверяется главное: что кнопка ведёт именно на метку этого партнёра, и что
чужой запрос на вебхук ничего не сделает.
"""

import pytest

from app.repositories.partner_bots import PartnerBotsRepository
from app.services.partner_bots import (PartnerBotService, new_secret,
                                       webhook_url)

TOKEN = '123456789:AAHyeahThisLooksLikeAToken'


class Settings:
    def __init__(self, **over):
        self.values = {
            'pay.callback_base': 'https://webhook.rsvps.tech',
            'link.bot_username': 'rsconnect_bot',
            'partner.greeting': 'RS VPN — быстрый VPN.',
            'partner.button': 'Получить VPN',
            **over,
        }

    async def get(self, key, default=''):
        return self.values.get(key, default)


class Http:
    """Поддельный Telegram: помнит вызовы и отвечает заранее заданным."""

    def __init__(self, answers=None):
        self.calls: list[tuple[str, dict]] = []
        self.answers = dict(answers or {})

    async def post(self, url, json=None):
        method = url.rsplit('/', 1)[-1]
        self.calls.append((method, json or {}))
        answer = self.answers.get(method, {'ok': True, 'result': True})

        class Reply:
            @staticmethod
            def json():
                return answer

        return Reply()

    def call(self, method: str) -> dict:
        return next(payload for name, payload in self.calls if name == method)

    def called(self, method: str) -> bool:
        return any(name == method for name, _ in self.calls)


GET_ME = {'ok': True, 'result': {'username': 'vladvpn_bot', 'first_name': 'Vlad VPN'}}


@pytest.fixture
async def service(db):
    repo = PartnerBotsRepository(db['partner_bots'])
    await repo.ensure_indexes()
    http = Http({'getMe': GET_ME})
    return PartnerBotService(repo, http, Settings()), repo, http


# ── подключение ─────────────────────────────────────────────────────────────
async def test_connecting_asks_telegram_whose_token_it_is(db, service):
    """Токен с опечаткой иначе тихо лёг бы в базу, а узнал бы об этом
    партнёр — от своих подписчиков."""
    api, repo, http = service

    result = await api.connect(TOKEN, 'vlad', 802421217)

    assert result['ok'] and result['username'] == 'vladvpn_bot'
    assert http.called('getMe')


async def test_the_webhook_is_set_with_a_secret(db, service):
    api, repo, http = service

    result = await api.connect(TOKEN, 'vlad', 802421217)

    hook = http.call('setWebhook')
    assert hook['url'] == webhook_url('https://webhook.rsvps.tech', result['secret'])
    assert hook['secret_token'] == result['secret']
    assert TOKEN not in hook['url'], 'токен в адресе осел бы в логах прокси'


async def test_a_bad_token_is_refused_before_anything_is_written(db, service):
    api, repo, http = service

    result = await api.connect('не-токен', 'vlad', 1)

    assert not result['ok'] and result['reason'] == 'bad_token'
    assert await repo.all() == []


async def test_a_token_telegram_rejects_is_not_saved(db):
    repo = PartnerBotsRepository(db['partner_bots'])
    await repo.ensure_indexes()
    http = Http({'getMe': {'ok': False, 'description': 'Unauthorized'}})
    api = PartnerBotService(repo, http, Settings())

    result = await api.connect(TOKEN, 'vlad', 1)

    assert not result['ok'] and result['reason'] == 'telegram'
    assert await repo.all() == []


async def test_a_bot_whose_webhook_failed_is_not_left_in_the_list(db):
    """Запись без вебхука — это бот, который молчит."""
    repo = PartnerBotsRepository(db['partner_bots'])
    await repo.ensure_indexes()
    http = Http({'getMe': GET_ME,
                 'setWebhook': {'ok': False, 'description': 'Bad URL'}})
    api = PartnerBotService(repo, http, Settings())

    result = await api.connect(TOKEN, 'vlad', 1)

    assert not result['ok'] and result['reason'] == 'webhook'
    assert await repo.all() == []


async def test_the_same_bot_cannot_be_connected_twice(db, service):
    api, repo, http = service
    await api.connect(TOKEN, 'vlad', 1)

    result = await api.connect(TOKEN, 'other', 2)

    assert not result['ok'] and result['reason'] == 'exists'
    assert len(await repo.all()) == 1


async def test_without_a_webhook_address_nothing_is_connected(db):
    repo = PartnerBotsRepository(db['partner_bots'])
    await repo.ensure_indexes()
    api = PartnerBotService(repo, Http({'getMe': GET_ME}),
                            Settings(**{'pay.callback_base': ''}))

    result = await api.connect(TOKEN, 'vlad', 1)

    assert not result['ok'] and result['reason'] == 'no_base'


# ── ответ человеку ──────────────────────────────────────────────────────────
def update(text='/start', chat_id=555):
    return {'message': {'chat': {'id': chat_id}, 'text': text}}


async def test_the_button_leads_to_the_main_bot_with_the_partner_tag(db, service):
    """Ради этой строки всё и делается."""
    api, repo, http = service
    connected = await api.connect(TOKEN, 'vlad', 802421217)

    assert await api.handle(connected['secret'], update()) is True

    sent = http.call('sendMessage')
    button = sent['reply_markup']['inline_keyboard'][0][0]
    assert button['url'] == 'https://t.me/rsconnect_bot?start=ref_vlad'
    assert button['text'] == 'Получить VPN'
    assert sent['chat_id'] == 555


async def test_any_message_gets_an_answer_not_only_start(db, service):
    """Человек часто пишет «привет», а молчание считает поломкой."""
    api, repo, http = service
    connected = await api.connect(TOKEN, 'vlad', 1)

    await api.handle(connected['secret'], update(text='привет'))

    assert http.called('sendMessage')


async def test_starts_are_counted_but_chatter_is_not(db, service):
    api, repo, http = service
    connected = await api.connect(TOKEN, 'vlad', 1)

    await api.handle(connected['secret'], update())
    await api.handle(connected['secret'], update())
    await api.handle(connected['secret'], update(text='привет'))

    assert (await repo.all())[0]['starts'] == 2


async def test_an_unknown_secret_does_nothing(db, service):
    """Иначе чужой запрос заставлял бы бота писать людям."""
    api, repo, http = service
    await api.connect(TOKEN, 'vlad', 1)
    http.calls.clear()

    assert await api.handle('чужой-секрет', update()) is False
    assert not http.called('sendMessage')


async def test_an_update_without_a_chat_is_not_an_error(db, service):
    api, repo, http = service
    connected = await api.connect(TOKEN, 'vlad', 1)
    http.calls.clear()

    assert await api.handle(connected['secret'], {'edited_message': {}}) is True
    assert not http.called('sendMessage')


async def test_each_partner_gets_his_own_tag(db, service):
    """Две витрины на одну базу — перепутать метки нельзя."""
    api, repo, http = service
    first = await api.connect(TOKEN, 'vlad', 1)
    http.answers['getMe'] = {'ok': True, 'result': {'username': 'anna_bot'}}
    second = await api.connect('987654321:BBanotherToken', 'anna', 2)

    await api.handle(first['secret'], update())
    first_url = http.call('sendMessage')['reply_markup']['inline_keyboard'][0][0]['url']
    http.calls.clear()
    await api.handle(second['secret'], update())
    second_url = http.call('sendMessage')['reply_markup']['inline_keyboard'][0][0]['url']

    assert first_url.endswith('ref_vlad') and second_url.endswith('ref_anna')


# ── отключение ──────────────────────────────────────────────────────────────
async def test_disconnecting_removes_the_webhook_too(db, service):
    """Без снятия вебхука бот продолжал бы отвечать в пустоту."""
    api, repo, http = service
    await api.connect(TOKEN, 'vlad', 1)

    found = await api.disconnect('@vladvpn_bot')

    assert found and http.called('deleteWebhook')
    assert await repo.all() == []


async def test_a_bot_can_be_disconnected_by_its_tag(db, service):
    api, repo, http = service
    await api.connect(TOKEN, 'vlad', 1)

    assert await api.disconnect('vlad')


async def test_disconnecting_a_missing_bot_is_not_a_success(db, service):
    api, repo, http = service

    assert await api.disconnect('@nobody_bot') is None


def test_secrets_are_not_guessable():
    assert new_secret() != new_secret()
    assert len(new_secret()) >= 24
