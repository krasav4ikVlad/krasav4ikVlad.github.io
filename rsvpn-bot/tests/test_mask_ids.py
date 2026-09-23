"""Идентификаторы под звёздочками — для съёмки экрана.

Проверяется не только сама маска, но и два её края: что она доезжает до
экранов, где id показывают, и что она НЕ доезжает до выгрузок — оттуда id
копируют для начислений, и звёздочки там означали бы испорченный файл.
"""

import pytest

from app.admin import raffle as admin_raffle
from app.bot.callbacks import Admin as Adm
from app.bot.screens.profile import profile_caption
from app.content import ids
from app.services import excel

from tests.test_admin_panel import ADMIN, admin_env, callback, message  # noqa: F401


@pytest.fixture(autouse=True)
def plain_ids():
    """Режим глобальный — между тестами его надо возвращать на место."""
    ids.set_hidden(False)
    yield
    ids.set_hidden(False)


# ── сама маска ──────────────────────────────────────────────────────────────
def test_the_middle_is_starred():
    assert ids.mask(7095687) == '70***87'


def test_the_number_of_stars_matches_the_length():
    """Иначе по длине маски видно, сколько цифр в id, — мелочь, но зачем."""
    assert ids.mask(802421217) == '80*****17'
    assert len(ids.mask(802421217)) == len('802421217')


def test_a_short_id_is_hidden_completely():
    assert ids.mask(1234) == '****'
    assert ids.mask(7) == '*'


def test_nothing_is_invented_from_emptiness():
    assert ids.mask('') == '' and ids.mask(None) == ''


def test_show_depends_on_the_mode():
    assert ids.show(7095687) == '7095687'
    ids.set_hidden(True)
    assert ids.show(7095687) == '70***87'


def test_the_visible_block_wins_over_the_mode():
    ids.set_hidden(True)
    with ids.visible():
        assert ids.show(7095687) == '7095687'
    assert ids.show(7095687) == '70***87'


# ── экраны ──────────────────────────────────────────────────────────────────
def test_the_profile_hides_the_id():
    user = {'user_data': {'user_id': 7095687}, 'info': {'balance': 0}}

    assert '7095687' in profile_caption(user)
    ids.set_hidden(True)
    assert '70***87' in profile_caption(user) and '7095687' not in profile_caption(user)


def test_the_raffle_summary_hides_the_ids():
    data = {'start': None, 'end': None, 'tickets': 3, 'events': [], 'revenue': 0,
            'skipped': {}, 'journal_since': None, 'friend_tickets': 3,
            'self_per_month': 1, 'min_months': 1,
            'participants': [{'user_id': 802421217, 'username': 'vasya',
                              'tickets': 3, 'friends': 1, 'own': 0}]}

    ids.set_hidden(True)
    text = admin_raffle.summary(data)

    assert '80*****17' in text and '802421217' not in text


def test_the_export_keeps_real_ids():
    """Из файла копируют id для /rafflewin — маска сломала бы его."""
    from app.core.time import now

    data = {'participants': [{'user_id': 802421217, 'username': 'vasya',
                              'tickets': 3, 'friends': 1, 'own': 0,
                              'first_at': now()}],
            'rows': [{'ticket': 1, 'at': now(), 'owner': 802421217,
                      'owner_username': 'vasya', 'kind': 'друг',
                      'detail': '', 'friend': 802421301}]}

    ids.set_hidden(True)

    assert admin_raffle.user_rows(data)[0][0] == 802421217
    assert admin_raffle.ticket_rows(data)[0][3] == 802421217


# ── команда ─────────────────────────────────────────────────────────────────
async def test_the_command_switches_the_mode(admin_env):
    dp, bot, session, c = admin_env

    await dp.feed_update(bot, message('/mask'))

    assert await c.settings.flag('privacy.mask_ids') is True
    assert 'спрятаны' in session.last_text.lower()


async def test_the_command_switches_it_back(admin_env):
    dp, bot, session, c = admin_env

    await dp.feed_update(bot, message('/mask'))
    await dp.feed_update(bot, message('/mask'))

    assert await c.settings.flag('privacy.mask_ids') is False


async def test_the_state_can_be_set_explicitly(admin_env):
    """На записи переключают в спешке: «/mask on» не должен зависеть от
    того, что стояло раньше."""
    dp, bot, session, c = admin_env

    await dp.feed_update(bot, message('/mask on'))
    await dp.feed_update(bot, message('/mask on'))

    assert await c.settings.flag('privacy.mask_ids') is True


async def test_the_answer_shows_what_it_will_look_like(admin_env):
    dp, bot, session, c = admin_env

    await dp.feed_update(bot, message('/mask on'))

    assert ids.mask(ADMIN.id) in session.last_text


async def test_the_mode_survives_the_next_update(admin_env):
    """Режим живёт в настройках, а не в памяти процесса: иначе после
    перезапуска бот показал бы всё, что прятал."""
    dp, bot, session, c = admin_env
    await c.settings.set('privacy.mask_ids', True)
    ids.set_hidden(False)          # как будто процесс только поднялся

    await dp.feed_update(bot, message('/admin'))

    assert ids.hidden() is True
