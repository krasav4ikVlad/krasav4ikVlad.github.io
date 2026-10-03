"""Итоги розыгрыша: публичный файл, месяцы подписки и письма победителям.

Здесь всё необратимо по-разному: начисление ещё можно откатить руками, а
отправленное письмо — уже нет. Поэтому главное, что проверяется, — обе
команды сначала показывают, и только по второй делают.
"""

from datetime import timedelta

import pytest

from app.admin import raffle_gifts as gifts
from app.content import ids, texts
from app.core.time import MSK, now

from tests.test_admin_panel import ADMIN, admin_env, message  # noqa: F401
from tests.test_raffle import START  # noqa: F401


def ticket(number: int, owner: int, *, hour: int = 12, day: int = 2,
           username: str = 'vasya') -> dict:
    return {'ticket': number, 'at': START.replace(day=day, hour=hour,
                                                  minute=0, second=0),
            'owner': owner, 'owner_username': username,
            'kind': 'друг', 'detail': '', 'friend': None}


# ── публичный файл ──────────────────────────────────────────────────────────
def test_the_public_file_shows_no_full_id():
    """Файл выкладывают в канал: полный id из него набрать нельзя."""
    rows = gifts.public_rows([ticket(1, 802421217)])

    assert rows[0][3] == '802****17'
    assert '802421217' not in str(rows[0])
    assert gifts.PUBLIC_COLUMNS == ('билет', 'дата', 'время',
                                    'участник_id_скрытый')


def test_the_public_file_keeps_number_date_and_time():
    rows = gifts.public_rows([ticket(17, 1, hour=23, day=2)])

    assert rows[0][0] == 17
    assert rows[0][1] == '02.10.2026' and rows[0][2] == '23:00:00'


def test_a_cutoff_drops_the_tail_only():
    """Билеты пронумерованы по времени: отсекается хвост, номера не едут."""
    rows = [ticket(1, 1, day=2, hour=22), ticket(2, 2, day=2, hour=23),
            ticket(3, 3, day=3, hour=1)]
    moment, bad = gifts.cutoff('02.10.2026 23:00')

    taken = gifts.until(rows, moment)

    assert not bad
    assert [row['ticket'] for row in taken] == [1, 2]


def test_a_cutoff_without_time_means_the_whole_day():
    moment, bad = gifts.cutoff('02.10.2026')

    assert not bad and moment.hour == 23 and moment.minute == 59


def test_the_cutoff_is_moscow_time():
    """На сервере может быть любая таймзона, а в файле — московское время."""
    moment, _ = gifts.cutoff('02.10.2026 23:00')

    assert moment.tzinfo is MSK and moment.hour == 23


def test_a_broken_cutoff_is_named_not_swallowed():
    assert gifts.cutoff('вчера')[1] == 'вчера'
    assert gifts.cutoff('02.10.2026 25-00')[1] == '25-00'


def test_without_a_cutoff_nothing_is_dropped():
    rows = [ticket(1, 1), ticket(2, 2)]

    assert gifts.until(rows, None) == rows


# ── месяц подписки ──────────────────────────────────────────────────────────
def saved_row(**extra) -> dict:
    row = {'_id': 'month-20261001-20261022', 'at': now(), 'kind': 'month',
           'days': 30, 'tickets_total': 10, 'participants': 3,
           'winners': [{'prize': gifts.MONTH_PRIZE, 'ticket': 3,
                        'user_id': 802421217, 'username': 'vasya'},
                       {'prize': gifts.MONTH_PRIZE, 'ticket': 7,
                        'user_id': 802421301, 'username': ''}]}
    row.update(extra)
    return row


def test_the_list_shows_usernames_and_tickets():
    text = gifts.month_text(saved_row())

    assert '@vasya' in text and 'без ника' in text
    assert '№3' in text and '№7' in text


def test_the_list_says_nothing_is_given_yet():
    """Главное, что должно быть видно: деньги и дни ещё не ушли."""
    text = gifts.month_text(saved_row())

    assert 'ничего не начислено' in text
    assert '/rafflemonth выдать' in text


def test_after_the_award_the_list_points_to_the_letters():
    text = gifts.month_text(saved_row(awarded_at=now()), awarded=True)

    assert 'начислены' in text and '/rafflenote месяц' in text


# ── письма ──────────────────────────────────────────────────────────────────
def test_the_preview_shows_the_letter_itself():
    """Не «будет отправлено письмо», а ровно тот текст, что уйдёт."""
    text = gifts.preview(saved_row(awarded_at=now()), month_prize=True,
                         key='month-x')

    assert texts.render('raffle.month', days=30, ticket=3)[:40] in text
    assert '@vasya' in text


def test_the_preview_warns_about_a_second_round():
    text = gifts.preview(saved_row(noted_at=now()), month_prize=True,
                         key='month-x')

    assert 'уже уходили' in text


def test_the_letter_names_the_prize_and_the_ticket():
    letter = gifts.letter({'prize': 'iPhone 18 Pro', 'ticket': 42},
                          month_prize=False)

    assert 'iPhone 18 Pro' in letter and '42' in letter


def test_the_month_letter_speaks_of_days_not_prizes():
    letter = gifts.letter({'prize': gifts.MONTH_PRIZE, 'ticket': 3},
                          month_prize=True)

    assert '30 дней' in letter


# ── команды целиком ─────────────────────────────────────────────────────────
async def fill(c) -> None:
    """Два живых билета: один куплен 2 октября, другой 3-го.

    Через настоящие покупки, а не подложенным снимком: команды сначала
    пересчитывают билеты, и подделка снимка проверяла бы не то.
    """
    from tests.test_raffle import bought, person

    await person(c.users, 802421217)
    await person(c.users, 802421301)
    await bought(c.balance_log, 802421217, day=2)
    await bought(c.balance_log, 802421301, day=3)


@pytest.fixture
async def dates(admin_env):
    """Даты акции — иначе любая команда отвечает «не вижу даты»."""
    dp, bot, session, c = admin_env
    await c.settings.set('raffle.start', '01.10.2026')
    await c.settings.set('raffle.end', '22.10.2026')
    return admin_env


async def test_the_month_draw_changes_nothing_by_itself(dates, monkeypatch):
    """Первый вызов — только список: ни дня никому не начислено."""
    dp, bot, session, c = dates
    await fill(c)
    given = []
    monkeypatch.setattr('app.services.raffle_prizes.award',
                        lambda *a, **kw: given.append(a) or {})

    await dp.feed_update(bot, message('/rafflemonth 2'))
    # второй раз — показывает тот же список, и тоже ничего не начисляет
    await dp.feed_update(bot, message('/rafflemonth'))

    assert not given
    assert 'Месяц подписки' in session.last_text
    assert '/rafflemonth выдать' in session.last_text


async def test_the_award_follows_the_shown_list(dates, monkeypatch):
    """Выдача идёт по сохранённому списку, а не по новому броску."""
    dp, bot, session, c = dates
    await fill(c)
    seen = {}

    async def award(users, vpn, winners, **kwargs):
        seen['winners'] = winners
        seen['mark'] = kwargs.get('mark')
        return {'done': winners, 'failed': [], 'skipped': []}

    monkeypatch.setattr('app.services.raffle_prizes.award', award)

    await dp.feed_update(bot, message('/rafflemonth 2'))
    shown = await c.db['raffle_draws'].find_one({'_id': 'month-20261001-20261022'})
    await dp.feed_update(bot, message('/rafflemonth выдать'))

    assert [w['user_id'] for w in seen['winners']] == \
        [w['user_id'] for w in shown['winners']]
    assert all(w['amount'] == 30 for w in seen['winners'])
    assert 'Месяцы начислены' in session.last_text


async def test_the_award_refuses_without_a_list(dates):
    dp, bot, session, c = dates

    await dp.feed_update(bot, message('/rafflemonth выдать'))

    assert 'Сначала вытащите список' in session.last_text


async def test_the_letters_wait_for_the_days(dates):
    """Письмо говорит «подписка продлена» — значит, она должна быть продлена."""
    dp, bot, session, c = dates
    await c.db['raffle_draws'].insert_one(saved_row())

    await dp.feed_update(bot, message('/rafflenote месяц отправить'))

    assert 'ещё не начислены' in session.last_text


async def test_the_letters_are_shown_before_they_are_sent(dates, monkeypatch):
    dp, bot, session, c = dates
    await c.db['raffle_draws'].insert_one(saved_row(awarded_at=now()))
    sent = []
    monkeypatch.setattr('app.campaigns.sender.Sender.send',
                        lambda self, bot, uid, text, markup=None: sent.append(uid))

    await dp.feed_update(bot, message('/rafflenote месяц'))

    assert not sent
    assert 'Будет отправлено: 2' in session.last_text
    assert '@vasya' in session.last_text


async def test_the_letters_report_who_got_them(dates, monkeypatch):
    dp, bot, session, c = dates
    await c.db['raffle_draws'].insert_one(saved_row(awarded_at=now()))

    async def send(self, bot, user_id, text, markup=None):
        return user_id == 802421217      # второй заблокировал бота

    monkeypatch.setattr('app.campaigns.sender.Sender.send', send)

    await dp.feed_update(bot, message('/rafflenote месяц отправить'))

    assert 'Отправлено: 1' in session.last_text
    assert '@vasya' in session.last_text and 'Не дошло: 1' in session.last_text

    row = await c.db['raffle_draws'].find_one({'_id': 'month-20261001-20261022'})
    assert row['noted'] == 1 and row['noted_at']


async def test_the_public_file_is_sent_with_a_cutoff(dates):
    dp, bot, session, c = dates
    await fill(c)

    await dp.feed_update(bot, message('/rafflepublic 02.10.2026 23:00'))

    assert any(name == 'SendDocument' for name, _ in session.calls), \
        'файл не ушёл'
    caption = session.last_text
    assert 'Билетов в файле: <b>1</b>' in caption
    assert 'отброшено <b>1</b>' in caption
