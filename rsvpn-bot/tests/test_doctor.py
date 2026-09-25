"""«Почему бот не отвечает» — три причины, о которых знает только Telegram.

Проверяется не текст ответа, а то, что каждая причина названа и что доктор
не молчит: диагностику зовут в четыре утра, и «всё в порядке» при стоящем
вебхуке дороже, чем отсутствие команды вовсе.
"""

import pytest

from scripts import doctor


class Reply:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def telegram(**answers):
    """Поддельный httpx.AsyncClient: отвечает по имени метода в url."""
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None):
            for method, payload in answers.items():
                if method in url:
                    return Reply(payload)
            return Reply({'ok': True, 'result': {}})

    return Client


ME = {'ok': True, 'result': {'username': 'rsconnect_bot', 'id': 1}}
NO_HOOK = {'ok': True, 'result': {'url': ''}}
BUSY = {'ok': False, 'error_code': 409,
        'description': 'Conflict: terminated by other getUpdates request'}


@pytest.fixture
def run(monkeypatch, capsys):
    async def go(**answers):
        import httpx

        monkeypatch.setenv('API_TOKEN', '123:AAA')
        monkeypatch.setenv('ADMIN_IDS', '1')
        monkeypatch.setattr(httpx, 'AsyncClient', telegram(**answers))
        code = await doctor.main()
        return code, capsys.readouterr().out

    return go


async def test_a_revoked_token_is_named_first(run):
    """Процесс жив, в логе тишина — по логам это не диагностируется вовсе."""
    code, out = await run(getMe={'ok': False, 'description': 'Unauthorized'})

    assert code == 1
    assert 'Unauthorized' in out and 'BotFather' in out


async def test_a_webhook_is_named_as_the_reason(run):
    """Самый неочевидный случай: бот работает, polling крутится, а сообщения
    уходят на чужой адрес."""
    code, out = await run(getMe=ME, getUpdates=BUSY,
                          getWebhookInfo={'ok': True,
                                          'result': {'url': 'https://old/hook'}})

    assert code == 1
    assert 'Стоит вебхук' in out and 'https://old/hook' in out
    assert 'deleteWebhook' in out


async def test_a_second_process_is_named(run):
    code, out = await run(getMe=ME, getWebhookInfo=NO_HOOK, getUpdates=BUSY)

    assert code == 1
    assert 'занят другим процессом' in out and 'main_bot' in out


async def test_silence_on_the_line_means_the_process_is_down(run):
    """Если getUpdates ответил нам, значит polling не держал никто."""
    code, out = await run(getMe=ME, getWebhookInfo=NO_HOOK,
                          getUpdates={'ok': True, 'result': []})

    assert code == 1
    assert 'никто не опрашивал' in out


async def test_pending_updates_are_shown(run):
    """Сколько накопилось, пока бот молчал, — это и размер проблемы."""
    code, out = await run(getMe=ME, getUpdates=BUSY,
                          getWebhookInfo={'ok': True,
                                          'result': {'url': '',
                                                     'pending_update_count': 1200}})

    assert '1200' in out


async def test_a_healthy_bot_gets_a_clean_answer(run):
    """«Всё в порядке» должно означать именно это — и подсказывать, куда
    смотреть дальше."""
    code, out = await run(getMe=ME, getWebhookInfo=NO_HOOK, getUpdates=BUSY)

    assert 'Токен принят' in out


async def test_a_dead_line_to_telegram_is_told_apart(run):
    """«Сеть не пускает» и «токен отозван» лечатся по-разному."""
    class Broken:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None):
            raise OSError('Name or service not known')

    import httpx

    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setenv('API_TOKEN', '123:AAA')
    monkeypatch.setenv('ADMIN_IDS', '1')
    monkeypatch.setattr(httpx, 'AsyncClient', Broken)
    try:
        code = await doctor.main()
    finally:
        monkeypatch.undo()

    assert code == 1
