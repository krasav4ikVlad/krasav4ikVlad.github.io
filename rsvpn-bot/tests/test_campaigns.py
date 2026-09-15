"""Кампании: окна отправки, однократность, начисления."""

from app.campaigns.engine import CampaignEngine, CampaignStep
from app.campaigns.sender import Sender
from app.core.time import now
from app.repositories.users import UsersRepository
from app.settings.service import SettingsService
from datetime import timedelta

from tests.conftest import FakeBot


def build_engine(db, bot):
    return CampaignEngine(
        bot=bot,
        users=UsersRepository(db['users']),
        settings=SettingsService(db['bot_settings']),
        sender=Sender(),
        keyboards={},
        runs_collection=db['campaign_runs'],
        pause_between=0,
    )


STEP = CampaignStep(
    code='trial_d0', title='D0',
    query={'growth.segment': 'new_trial_d0'},
    text=lambda user, ctx: f'привет {ctx.name}',
    min_hours=2, max_hours=15, respect_night=False,
)


async def test_sends_only_inside_window(db, user_factory):
    await user_factory(**{'growth.segment': 'new_trial_d0',
                          'growth.joined_at': now() - timedelta(hours=5)})
    await user_factory(**{'growth.segment': 'new_trial_d0',
                          'growth.joined_at': now() - timedelta(minutes=30)})
    await user_factory(**{'growth.segment': 'new_trial_d0',
                          'growth.joined_at': now() - timedelta(hours=40)})

    bot = FakeBot()
    report = await build_engine(db, bot).run_step(STEP)

    assert report.sent == 1
    assert report.skipped_window == 2
    assert bot.sent[0][1] == 'привет User1'


async def test_second_run_does_not_resend(db, user_factory):
    await user_factory(**{'growth.segment': 'new_trial_d0',
                          'growth.joined_at': now() - timedelta(hours=5)})
    bot = FakeBot()
    engine = build_engine(db, bot)

    assert (await engine.run_step(STEP)).sent == 1
    assert (await engine.run_step(STEP)).sent == 0
    assert len(bot.sent) == 1


async def test_blocked_user_does_not_get_bonus_twice(db, user_factory):
    """Пользователь заблокировал бота: деньги начисляются ровно один раз."""
    await user_factory(**{'growth.segment': 'expired_3d', 'growth.has_topup': True,
                          'growth.balance': 0, 'info.balance': 0})
    step = CampaignStep(code='expired_3d', title='3 дня',
                        query={'growth.segment': 'expired_3d'},
                        text=lambda u, ctx: 'вернитесь', credit_to=15,
                        respect_night=False)

    bot = FakeBot(fail_for={1})
    engine = build_engine(db, bot)

    first = await engine.run_step(step)
    second = await engine.run_step(step)

    assert first.credited == 15 and first.failed == 1 and first.sent == 0
    assert second.credited == 0
    assert (await db['users'].find_one({'user_data.user_id': 1}))['info']['balance'] == 15


async def test_disabled_step_is_skipped(db, user_factory):
    await user_factory(**{'growth.segment': 'new_trial_d0',
                          'growth.joined_at': now() - timedelta(hours=5)})
    settings = SettingsService(db['bot_settings'])
    await settings.set('campaign.new_trial_enabled', False)

    bot = FakeBot()
    engine = build_engine(db, bot)
    engine.settings = settings

    step = CampaignStep(code='trial_d0', title='D0', query={'growth.segment': 'new_trial_d0'},
                        text=lambda u, ctx: 'x', settings_key='campaign.new_trial_enabled')
    report = await engine.run([step])

    assert report.steps == [] and bot.sent == []


async def test_bonus_switches_the_user_to_the_daily_plan(db, user_factory):
    """Начисление без суточного тарифа — деньги на ветер.

    Автопродление считает цену по vpn.period. У вернувшегося там остаётся
    месяц или полгода: 15₽ бонуса не покрывают такой тариф, продления нет,
    бонус лежит мёртвым грузом. Старый бот ставил period=1 тем же запросом.
    """
    await user_factory(**{'growth.segment': 'expired_3d', 'growth.has_topup': True,
                          'growth.balance': 0, 'info.balance': 0, 'vpn.period': 30})
    step = CampaignStep(code='expired_3d', title='3 дня',
                        query={'growth.segment': 'expired_3d'},
                        text=lambda u, ctx: 'вернитесь', credit_to=15,
                        daily_period=True, respect_night=False)

    await build_engine(db, FakeBot()).run_step(step)

    doc = await db['users'].find_one({'user_data.user_id': 1})
    assert doc['vpn']['period'] == 1
    assert doc['info']['balance'] == 15


async def test_step_without_daily_period_does_not_touch_the_plan(db, user_factory):
    await user_factory(**{'growth.segment': 'expired_14d', 'growth.has_topup': True,
                          'vpn.period': 30})
    step = CampaignStep(code='expired_14d', title='2 недели',
                        query={'growth.segment': 'expired_14d'},
                        text=lambda u, ctx: 'вернитесь', respect_night=False)

    await build_engine(db, FakeBot()).run_step(step)

    doc = await db['users'].find_one({'user_data.user_id': 1})
    assert doc['vpn']['period'] == 30


async def test_every_step_has_its_own_flag_and_text():
    """Два шага с одним кодом молча съели бы друг друга: флаг общий."""
    from app.campaigns.definitions import ALL_STEPS

    codes = [s.code for s in ALL_STEPS]
    assert len(codes) == len(set(codes))


async def test_texts_of_all_steps_render(db, user_factory):
    """Опечатка в ключе текста видна здесь, а не в проде пустым сообщением."""
    from app.campaigns.definitions import ALL_STEPS
    from app.campaigns.engine import StepContext

    ctx = StepContext(name='Иван', balance=10, credited=15, topups_count=1,
                      daily_price=6, bonus_rate=0.5)
    for step in ALL_STEPS:
        rendered = step.text({}, ctx)
        assert rendered and rendered != step.code, step.code
        assert '{' not in rendered, step.code
