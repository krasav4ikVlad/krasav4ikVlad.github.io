"""Бесплатный период: выдача один раз и сброс из админки.

Сброс — это не только «снять галочку». У всех, кто когда-либо покупал,
в документе навсегда остаётся vpn.shortUuid, и старое правило «есть
подписка — триала не будет» съедало бы сброс целиком. А снимать это
правило совсем нельзя: тогда бесплатные дни разом достались бы всей
истёкшей базе. Поэтому здесь проверяется и то, что сброс работает, и то,
что он не работает шире, чем просили.
"""

from datetime import timedelta

import pytest

from app.core.time import now
from app.domain.segments import audience_query
from app.services.trial import TrialService


class Panel:
    """Панель, которая помнит, что у неё просили."""

    def __init__(self):
        self.created: list[int] = []
        self.patched: list[dict] = []

    async def create_subscription(self, user_id, days):
        self.created.append(days)
        return {'uuid': 'u-new', 'shortUuid': 's-new',
                'expireAt': now() + timedelta(days=days), 'createdAt': now()}

    async def update_subscription(self, uuid, **kw):
        self.patched.append({'uuid': uuid, **kw})
        return {}


@pytest.fixture
async def trial(container):
    await container.startup()
    await container.settings.set('trial.require_subscription', False)
    container.trial.vpn = Panel()
    return container.trial


async def make(container, user_id: int, **growth_and_vpn) -> dict:
    doc = {'user_data': {'user_id': user_id}, 'info': {'balance': 0},
           'growth': growth_and_vpn.get('growth', {}),
           'vpn': growth_and_vpn.get('vpn', {})}
    await container.users.create(doc)
    return doc


# ── выдача ──────────────────────────────────────────────────────────────────
async def test_new_user_gets_a_fresh_subscription(container, trial):
    await make(container, 5)

    result = await trial.claim(5)

    assert result.ok
    assert trial.vpn.created == [3]
    assert (await container.users.get(5))['growth']['trial_claimed_at']


async def test_second_time_is_refused(container, trial):
    await make(container, 5)
    await trial.claim(5)

    assert (await trial.claim(5)).reason == 'claimed'


async def test_active_subscription_means_no_trial(container, trial):
    await make(container, 5, vpn={'shortUuid': 's', 'uuid': 'u',
                                  'expireAt': now() + timedelta(days=10)})

    assert (await trial.claim(5)).reason == 'has_sub'


async def test_expired_subscription_alone_does_not_open_the_trial(container, trial):
    """Без явного сброса истёкшая база бесплатных дней не получает."""
    await make(container, 5, vpn={'shortUuid': 's', 'uuid': 'u',
                                  'expireAt': now() - timedelta(days=30)})

    assert (await trial.claim(5)).reason == 'has_sub'
    assert trial.vpn.created == []


# ── сброс ───────────────────────────────────────────────────────────────────
async def test_reset_clears_the_mark_and_counts(container, trial):
    await make(container, 5, growth={'trial_claimed_at': now()})
    await make(container, 6, growth={'trial_claimed_at': now()})
    await make(container, 7)

    assert await trial.claimed_count() == 2
    assert await trial.reset() == 2

    user = await container.users.get(5)
    assert 'trial_claimed_at' not in user['growth']
    assert user['growth']['trial_reset_at']
    assert await trial.claimed_count() == 0


async def test_reset_touches_only_the_chosen_audience(container, trial):
    await make(container, 5, growth={'trial_claimed_at': now(), 'segment': 'expired_3d'})
    await make(container, 6, growth={'trial_claimed_at': now(), 'segment': 'active_paid'})

    assert await trial.reset(audience_query('expired')) == 1

    assert 'trial_claimed_at' not in (await container.users.get(5))['growth']
    assert (await container.users.get(6))['growth']['trial_claimed_at']


async def test_after_reset_an_expired_user_can_claim_again(container, trial):
    """И подписка при этом продлевается, а не создаётся заново.

    POST /api/users ответил бы «User short UUID already exists»: shortUuid
    считается от user_id и у второй подписки совпал бы с первой.
    """
    await make(container, 5,
               growth={'trial_claimed_at': now()},
               vpn={'shortUuid': 's', 'uuid': 'u', 'period': 30,
                    'expireAt': now() - timedelta(days=5)})
    await trial.reset()

    result = await trial.claim(5)

    assert result.ok
    assert trial.vpn.created == [], 'вторая подписка создаваться не должна'
    assert trial.vpn.patched[0]['uuid'] == 'u'
    assert trial.vpn.patched[0]['status'] == 'ACTIVE'

    user = await container.users.get(5)
    assert user['vpn']['expireAt'] > now()
    assert user['vpn']['period'] == 30, 'выбранную длительность сброс не трогает'


async def test_reset_does_not_revive_an_active_subscription(container, trial):
    """Пока подписка работает, бесплатный период не нужен — и не выдаётся."""
    await make(container, 5,
               growth={'trial_claimed_at': now()},
               vpn={'shortUuid': 's', 'uuid': 'u',
                    'expireAt': now() + timedelta(days=10)})
    await trial.reset()

    assert (await trial.claim(5)).reason == 'has_sub'


async def test_bypass_is_extended_together_with_the_main_subscription(container, trial):
    await make(container, 5,
               growth={'trial_claimed_at': now()},
               vpn={'shortUuid': 's', 'uuid': 'u', 'bypass_uuid': 'b',
                    'expireAt': now() - timedelta(days=1)})
    await trial.reset()
    await trial.claim(5)

    assert {p['uuid'] for p in trial.vpn.patched} == {'u', 'b'}


async def test_button_comes_back_after_a_reset(container, trial):
    """available() — то же правило, что и claim(): кнопка не должна обещать
    того, чего сервис не сделает."""
    await make(container, 5,
               growth={'trial_claimed_at': now()},
               vpn={'shortUuid': 's', 'uuid': 'u',
                    'expireAt': now() - timedelta(days=5)})

    assert await trial.available(await container.users.get(5)) is False
    await trial.reset()
    assert await trial.available(await container.users.get(5)) is True


async def test_panel_failure_gives_the_mark_back(container, trial):
    """Не выдали — не помечаем: иначе человек теряет период из-за нашей аварии."""
    async def broken(uuid, **kw):
        raise RuntimeError('панель недоступна')

    await make(container, 5,
               growth={'trial_claimed_at': now()},
               vpn={'shortUuid': 's', 'uuid': 'u',
                    'expireAt': now() - timedelta(days=5)})
    await trial.reset()
    trial.vpn.update_subscription = broken

    assert (await trial.claim(5)).reason == 'panel'
    assert 'trial_claimed_at' not in (await container.users.get(5))['growth']
