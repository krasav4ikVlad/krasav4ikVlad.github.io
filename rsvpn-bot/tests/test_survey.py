"""Опрос: последовательность вопросов и однократный бонус."""

import pytest

from app.repositories.users import UsersRepository
from app.services.survey import SurveyService
from app.settings.service import SettingsService


@pytest.fixture
async def survey(db):
    service = SurveyService(UsersRepository(db['users']), db['survey_bonus'],
                            SettingsService(db['bot_settings']))
    await service.ensure_indexes()
    return service


async def test_full_pass_gives_bonus_once(db, user_factory, survey):
    await user_factory()
    await survey.start(1, ['stability', 'price'])

    first = await survey.answer(1, 'stability', 'good')
    assert first.next_question == 'price' and first.bonus == 0

    second = await survey.answer(1, 'price', 'price_ok')
    assert second.bonus == 25

    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['balance'] == 25

    assert await survey.give_bonus(1) == 0          # повторно не начисляем
    user = await db['users'].find_one({'user_data.user_id': 1})
    assert user['info']['balance'] == 25


async def test_unhappy_answer_asks_for_comment(db, user_factory, survey):
    await user_factory()
    await survey.start(1, ['stability', 'price'])

    step = await survey.answer(1, 'stability', 'bad')

    assert step.next_question == 'comment'
    assert await survey.is_awaiting_comment(1) is True


async def test_comment_continues_the_survey(db, user_factory, survey):
    await user_factory()
    await survey.start(1, ['stability', 'price'])
    await survey.answer(1, 'stability', 'awful')

    step = await survey.comment(1, 'Вечером падает скорость')

    assert step.next_question == 'price'
    assert await survey.is_awaiting_comment(1) is False
    doc = await db['survey_bonus'].find_one({'user_id': 1})
    assert doc['answers']['comment'] == 'Вечером падает скорость'


async def test_bonus_amount_comes_from_settings(db, user_factory, survey):
    await user_factory()
    await survey.settings.set('survey.bonus', 50)
    await survey.start(1, ['stability'])

    step = await survey.answer(1, 'stability', 'good')
    assert step.bonus == 50


async def test_missing_user_does_not_lose_the_bonus(db, survey):
    """Пользователя нет — флаг снимаем, чтобы бонус можно было выдать позже."""
    await survey.start(999, ['stability'])
    assert await survey.give_bonus(999) == 0

    doc = await db['survey_bonus'].find_one({'user_id': 999})
    assert doc['bonus_given'] is False


async def test_stats_counts_answers(db, user_factory, survey):
    for uid in (1, 2, 3):
        await user_factory()
        await survey.start(uid, ['stability'])
    await survey.answer(1, 'stability', 'good')
    await survey.answer(2, 'stability', 'good')
    await survey.answer(3, 'stability', 'bad')

    stats = await survey.stats()
    assert stats['stability'] == {'good': 2, 'bad': 1}


async def test_survey_can_be_disabled(db, user_factory, survey):
    await user_factory()
    await survey.settings.set('survey.enabled', False)
    assert (await survey.answer(1, 'stability', 'good')).saved is False
