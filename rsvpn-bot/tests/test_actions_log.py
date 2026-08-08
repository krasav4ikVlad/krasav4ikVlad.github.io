"""Лог действий и денег.

Без него поддержка бессильна: человек пишет «нажал продлить, списалось
дважды», а в логах — только результаты работы сервисов, без следа самих
нажатий. Восстановить, что он делал, нечем.
"""

import logging
from datetime import datetime

import pytest
from aiogram import types

from app.bot.middlewares.actions import ActionLogMiddleware, what

USER = types.User(id=802421217, is_bot=False, first_name='Иван', username='ivan')
CHAT = types.Chat(id=1, type='private')


def message(text: str) -> types.Message:
    return types.Message(message_id=1, date=datetime.now(), chat=CHAT,
                         from_user=USER, text=text)


def click(data: str) -> types.CallbackQuery:
    return types.CallbackQuery(id='q', from_user=USER, chat_instance='ci', data=data)


async def run(event, handler=None, caplog=None):
    async def ok(event_, data):
        return 'готово'

    with caplog.at_level(logging.INFO, logger='actions'):
        return await ActionLogMiddleware()(handler or ok, event,
                                           {'event_from_user': USER})


# ── что именно попадает в строку ────────────────────────────────────────────
async def test_click_is_logged_with_user_and_button(caplog):
    await run(click('menu:my_subscription'), caplog=caplog)

    line = caplog.messages[-1]
    assert '802421217' in line and '@ivan' in line
    assert 'кнопка menu:my_subscription' in line


async def test_command_is_logged_in_full(caplog):
    """В командах личного нет, и по ним ищут начало сценария."""
    await run(message('/start ref_555'), caplog=caplog)

    assert 'команда /start ref_555' in caplog.messages[-1]


async def test_user_text_is_not_written_down(caplog):
    """В переписке с поддержкой люди пишут почту и номера карт. В логах,
    которые читают операторы и хранит pm2, таким данным не место."""
    await run(message('моя почта ivan@example.com, карта 4276 1234'), caplog=caplog)

    line = caplog.messages[-1]
    assert 'ivan@example.com' not in line and '4276' not in line
    assert 'текст (' in line, 'длина всё же нужна: видно, что человек писал'


async def test_inline_query_is_logged(caplog):
    query = types.InlineQuery(id='1', from_user=USER, query='1month', offset='',
                              chat_type='sender')
    await run(query, caplog=caplog)

    assert 'инлайн «1month»' in caplog.messages[-1]


async def test_duration_is_measured(caplog):
    await run(click('menu:profile'), caplog=caplog)

    assert 'мс)' in caplog.messages[-1]


# ── ошибки ──────────────────────────────────────────────────────────────────
async def test_a_failing_handler_is_logged_and_reraised(caplog):
    async def boom(event, data):
        raise ValueError('тариф не найден')

    with pytest.raises(ValueError):
        await run(click('plan:buy:1month'), boom, caplog)

    line = caplog.messages[-1]
    assert 'ОШИБКА ValueError' in line and 'тариф не найден' in line
    assert 'plan:buy:1month' in line, 'без действия ошибка бесполезна'


async def test_the_handler_result_passes_through(caplog):
    assert await run(click('x'), caplog=caplog) == 'готово'


# ── деньги ──────────────────────────────────────────────────────────────────
async def test_charge_and_credit_are_logged(container, caplog):
    await container.users.create({'user_data': {'user_id': 5},
                                  'info': {'balance': 300}})

    with caplog.at_level(logging.INFO, logger='money'):
        await container.users.charge(5, 150, 'Покупка подписки «1 месяц»')
        await container.users.credit(5, 240, 'Пополнение (cardlink)')

    assert '5 −150₽  Покупка подписки «1 месяц»' in caplog.messages
    assert '5 +240₽  Пополнение (cardlink)' in caplog.messages


async def test_a_refused_charge_is_logged_too(container, caplog):
    """«Не хватило» — самый частый вопрос в поддержку, и по логу должно быть
    видно, что попытка была."""
    await container.users.create({'user_data': {'user_id': 5},
                                  'info': {'balance': 10}})

    with caplog.at_level(logging.INFO, logger='money'):
        assert await container.users.charge(5, 150, 'Покупка подписки') is False

    assert any('не хватило 150₽' in line for line in caplog.messages)


def test_unknown_event_does_not_break_the_line():
    """Новый тип обновления не должен ронять логирование."""
    assert what(object()) == 'object'
