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


# ── история в документе пользователя ────────────────────────────────────────
#
# Поле logs существовало с самого начала, но писали в него ровно два места
# (пополнение и реферальное начисление) — у большинства людей оно так и
# оставалось пустым.

async def in_db(container, event, user_id: int = 802421217):
    from app.bot.middlewares.actions import ActionLogMiddleware

    async def ok(event_, data):
        return None

    await ActionLogMiddleware(container)(ok, event, {'event_from_user': USER})
    return ((await container.users.get(user_id)) or {}).get('logs') or []


async def test_every_click_lands_in_the_user_document(container):
    """Формат записи — контракт с платформой операторов: категория в action,
    сырой адрес в details, время строкой."""
    import re

    await container.users.create({'user_data': {'user_id': USER.id}})

    logs = await in_db(container, click('menu:bypass'))

    assert logs[-1]['action'] == 'Действие пользователя'
    assert logs[-1]['details'] == 'menu:bypass'
    assert re.fullmatch(r'\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}',
                        logs[-1]['timestamp']), logs[-1]['timestamp']


async def test_a_failed_action_is_remembered_with_its_error(container):
    from app.bot.middlewares.actions import ActionLogMiddleware

    await container.users.create({'user_data': {'user_id': USER.id}})

    async def boom(event, data):
        raise ValueError('тариф не найден')

    with pytest.raises(ValueError):
        await ActionLogMiddleware(container)(boom, click('plan:buy:1month'),
                                             {'event_from_user': USER})

    logs = (await container.users.get(USER.id))['logs']
    assert logs[-1]['action'] == 'Действие пользователя'
    assert logs[-1]['details'].startswith('plan:buy:1month')
    assert 'ошибка: ValueError' in logs[-1]['details']


async def test_the_setting_turns_the_db_journal_off(container):
    """Каждое нажатие — запись в базу. Если база не справляется, журнал
    должен выключаться без выката новой версии."""
    await container.users.create({'user_data': {'user_id': USER.id}})
    await container.settings.set('log.actions_to_db', False)

    assert await in_db(container, click('menu:profile')) == []


async def test_the_journal_does_not_grow_without_limit(container):
    """Без ограничения активный человек за год раздувает документ до предела
    Mongo в 16 МБ — и тогда перестаёт работать всё, включая списание."""
    await container.users.create({'user_data': {'user_id': USER.id}})

    for i in range(360):
        await container.users.log(USER.id, f'действие {i}')

    logs = (await container.users.get(USER.id))['logs']
    assert len(logs) == 350
    assert logs[-1]['action'] == 'действие 359'


async def test_a_broken_journal_does_not_break_the_action(container):
    """История полезна, но ронять ради неё то действие, которое она
    описывает, нельзя."""
    class Broken:
        async def update_one(self, *a, **kw):
            raise RuntimeError('база недоступна')

    container.users.col = Broken()
    await container.users.log(5, 'кнопка x')      # не бросает


async def test_topup_writes_to_the_journal(container):
    """Пополнение писалось в историю и раньше — проверяем, что не сломали."""
    from app.services.topup import TopupService

    await container.users.create({'user_data': {'user_id': 5}, 'info': {'balance': 0}})
    service = TopupService(container.users, container.payments_repo, container.settings)
    await service.process(provider='wata', txid='t1', amount=100, user_id=5)

    logs = (await container.users.get(5))['logs']
    actions = [item['action'] for item in logs]
    assert 'Начисление' in actions
    assert any('+120₽' in item['details'] for item in logs)


# ── автоматическое отдельно от ручного ──────────────────────────────────────
#
# Внешняя платформа операторов разбирает журнал по action, и «человек нажал
# купить» должно отличаться от «списалось само»: вопросы по ним разные.

async def journal(container, user_id: int = 5) -> list[dict]:
    return ((await container.users.get(user_id)) or {}).get('logs') or []


async def test_manual_and_automatic_charges_differ(container):
    await container.users.create({'user_data': {'user_id': 5},
                                  'info': {'balance': 1000}})

    await container.users.charge(5, 50, 'ByPass: 5 Гб')
    await container.users.charge(5, 150, 'Продление подписки «1 месяц»', auto=True)

    actions = [item['action'] for item in await journal(container)]
    assert actions == ['Списание', 'Автоматическое действие']


async def test_details_carry_the_sum_and_the_reason(container):
    await container.users.create({'user_data': {'user_id': 5},
                                  'info': {'balance': 1000}})

    await container.users.charge(5, 150, 'Покупка подписки «1 месяц»')
    await container.users.credit(5, 157, 'Пополнение (tribute), бонус 7₽')

    details = [item['details'] for item in await journal(container)]
    assert details == ['−150₽ Покупка подписки «1 месяц»',
                       '+157₽ Пополнение (tribute), бонус 7₽']


async def test_automatic_renewal_is_marked_as_automatic(container):
    """Продление списывает деньги, пока человек спит: для него это не его
    действие, и в журнале должно стоять именно так."""
    from datetime import timedelta

    from app.core.time import now
    from app.services.renewal import RenewalService

    class Vpn:
        async def update_subscription(self, uuid, **kw):
            return {}

    await container.startup()
    plan = await container.plans.get('1month')
    await container.users.create({
        'user_data': {'user_id': 5}, 'info': {'balance': 1000},
        'vpn': {'uuid': 'u', 'shortUuid': 's', 'period': plan['days'],
                'expireAt': now() + timedelta(hours=2)}})

    await RenewalService(container.users, container.plans, container.settings,
                         Vpn(), container.topup).run()

    entry = (await journal(container))[-1]
    assert entry['action'] == 'Автоматическое действие'
    assert 'Продление подписки' in entry['details']


async def test_referral_income_is_automatic(container):
    """Пригласивший ничего не нажимал — деньги пришли сами."""
    from app.services.topup import TopupService

    await container.users.create({'user_data': {'user_id': 5}, 'info': {'balance': 0}})
    await container.users.create({'user_data': {'user_id': 6, 'referrer': 5},
                                  'info': {'balance': 0}})

    service = TopupService(container.users, container.payments_repo, container.settings)
    await service.process(provider='wata', txid='t1', amount=100, user_id=6)

    entry = (await journal(container, 5))[-1]
    assert entry['action'] == 'Автоматическое действие'
    assert 'Реферальное начисление' in entry['details']


async def test_a_topup_leaves_exactly_one_entry(container):
    """Раньше пополнение писалось и из credit(), и отдельной строкой —
    на платформе операторов это выглядело как два разных события."""
    from app.services.topup import TopupService

    await container.users.create({'user_data': {'user_id': 5}, 'info': {'balance': 0}})
    service = TopupService(container.users, container.payments_repo, container.settings)
    await service.process(provider='wata', txid='t1', amount=100, user_id=5)

    assert [item['action'] for item in await journal(container)] == ['Начисление']
