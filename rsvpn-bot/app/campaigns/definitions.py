"""Все кампании — таблицей.

Каждая строка = одно касание. Тексты берутся из app/content/texts.py, поэтому
маркетолог правит формулировки в одном файле (или прямо в админке), не трогая
логику отправки.
"""

from __future__ import annotations

from app.campaigns.engine import CampaignStep, StepContext
from app.content import texts

# ── новички на триале ───────────────────────────────────────────────────────
NEW_TRIAL_BASE = {'growth.has_topup': False}


def _t(key: str, **extra):
    """Текст по ключу из реестра: подставляет имя, баланс и начисление.

    `extra` — постоянные подстановки шага. Так один шаблон обслуживает
    несколько сегментов, различаясь одним словом («несколько дней назад» /
    «около месяца назад»), и не превращается в три копии.
    """
    def render(user: dict, ctx: StepContext) -> str:
        return texts.render(key, name=ctx.name, balance=ctx.balance,
                            credited=ctx.credited, total=ctx.total_balance,
                            daily=ctx.daily_price, days_left=ctx.days_on_balance,
                            ab_percent=round(ctx.bonus_rate * 100),
                            ab_100=int(100 * (1 + ctx.bonus_rate)),
                            ab_75=int(75 * (1 + ctx.bonus_rate)),
                            multi=ctx.is_multi, **extra)
    return render


NEW_TRIAL_STEPS = [
    CampaignStep(
        code='trial_d0', title='D0 — через 2 часа',
        query={**NEW_TRIAL_BASE, 'growth.segment': 'new_trial_d0'},
        text=_t('campaign.trial.d0'), min_hours=2, max_hours=15,
        settings_key='campaign.new_trial_enabled',
    ),
    CampaignStep(
        code='trial_d1', title='D1 — через сутки',
        query={**NEW_TRIAL_BASE, 'growth.segment': 'new_trial_d1'},
        text=_t('campaign.trial.d1'), min_hours=24, max_hours=37,
        keyboard='none', settings_key='campaign.new_trial_enabled',
    ),
    CampaignStep(
        code='trial_d2', title='D2 — предпоследний день',
        query={**NEW_TRIAL_BASE, 'growth.segment': {'$in': ['new_trial_d2', 'new_trial_d2_hot']}},
        text=_t('campaign.trial.d2'), min_hours=48, max_hours=61,
        settings_key='campaign.new_trial_enabled',
    ),
    CampaignStep(
        code='trial_d2_hot', title='D2 HOT — осталось 6 часов',
        query={**NEW_TRIAL_BASE, 'growth.segment': 'new_trial_d2_hot'},
        text=_t('campaign.trial.d2_hot'), respect_night=False,
        after_step='trial_d2', settings_key='campaign.new_trial_enabled',
    ),
    CampaignStep(
        code='trial_d3', title='D3 — последний день',
        query={**NEW_TRIAL_BASE, 'growth.segment': {'$in': ['new_trial_d3', 'new_trial_d3_hot']}},
        text=_t('campaign.trial.d3'), min_hours=60, max_hours=73,
        settings_key='campaign.new_trial_enabled',
    ),
    CampaignStep(
        code='trial_d3_hot', title='D3 HOT — осталось 2 часа',
        query={**NEW_TRIAL_BASE, 'growth.segment': 'new_trial_d3_hot'},
        text=_t('campaign.trial.d3_hot'), respect_night=False,
        after_step='trial_d3', settings_key='campaign.new_trial_enabled',
    ),
]

# ── платившие, у которых истекла подписка ───────────────────────────────────
# credit_to>0 включает начисление «добить баланс». По вашему же отчёту бонусы
# на дальних сегментах жгли маржу — поэтому там 0, и это видно в одной колонке,
# а не спрятано в аргументе give_bonus=False на 550-й строке.
EXPIRED_BASE = {'growth.has_topup': True}

EXPIRED_STEPS = [
    CampaignStep(code='expired_1d', title='Истекли 1 день',
                 query={**EXPIRED_BASE, 'growth.segment': 'expired_1d'},
                 text=_t('campaign.expired.d1'), keyboard='extend',
                 settings_key='campaign.expired_enabled'),
    CampaignStep(code='expired_3d', title='Истекли 3 дня',
                 query={**EXPIRED_BASE, 'growth.segment': 'expired_3d'},
                 text=_t('campaign.expired.d3'), keyboard='extend', credit_to=15,
                 daily_period=True, settings_key='campaign.expired_enabled'),
    CampaignStep(code='expired_7d', title='Истекла неделя',
                 query={**EXPIRED_BASE, 'growth.segment': 'expired_7d'},
                 text=_t('campaign.expired.d7'), keyboard='extend', credit_to=40,
                 daily_period=True, settings_key='campaign.expired_enabled'),
    CampaignStep(code='expired_14d', title='Истекли 2 недели',
                 query={**EXPIRED_BASE, 'growth.segment': 'expired_14d'},
                 text=_t('campaign.expired.d14'), keyboard='extend',
                 settings_key='campaign.expired_enabled'),
    CampaignStep(code='expired_21d', title='Истекли 3 недели',
                 query={**EXPIRED_BASE, 'growth.segment': 'expired_21d'},
                 text=_t('campaign.expired.d21'), keyboard='extend',
                 settings_key='campaign.expired_enabled'),
    CampaignStep(code='expired_30d', title='Истёк месяц',
                 query={**EXPIRED_BASE, 'growth.segment': 'expired_30d'},
                 text=_t('campaign.expired.d30'), keyboard='extend',
                 settings_key='campaign.expired_enabled'),
    CampaignStep(code='churned_45d', title='Ушли 45 дней',
                 query={**EXPIRED_BASE, 'growth.segment': 'churned_45d'},
                 text=_t('campaign.churned.d45'), keyboard='extend',
                 settings_key='campaign.expired_enabled'),
    CampaignStep(code='churned_60d', title='Ушли 60 дней',
                 query={**EXPIRED_BASE, 'growth.segment': 'churned_60d'},
                 text=_t('campaign.churned.d60'), keyboard='extend',
                 settings_key='campaign.expired_enabled'),
    CampaignStep(code='churned_90d', title='Ушли 90 дней',
                 query={**EXPIRED_BASE, 'growth.segment': 'churned_90d'},
                 text=_t('campaign.churned.d90'), keyboard='extend',
                 settings_key='campaign.expired_enabled'),
    CampaignStep(code='churned_dead', title='Ушли 90+ дней',
                 query={**EXPIRED_BASE, 'growth.segment': 'churned_dead'},
                 text=_t('campaign.churned.dead'), keyboard='extend',
                 settings_key='campaign.expired_enabled'),
]

# ── те, кто не заплатил после триала ────────────────────────────────────────
TRIAL_BASE = {'growth.segment': 'trial', 'growth.has_topup': False}

# Касания идут сериями: S1 сразу, S2 и S3 — через паузу после предыдущего.
# Пауза считается от даты предыдущего касания, а не от истечения триала,
# поэтому человек не получает два письма подряд, если сегменты пересчитались
# с задержкой. Диапазон дней у продолжений шире, чем у S1: иначе пользователь,
# успевший перейти в соседний сегмент за время паузы, выпадал бы из серии.
TRIAL_STEPS = [
    CampaignStep(code='trial_back_7d', title='Триал: неделя',
                 query={**TRIAL_BASE, 'growth.days_since_expired': {'$gte': 4, '$lte': 7}},
                 text=_t('campaign.trial_back.s1', when='несколько дней назад'),
                 settings_key='campaign.trial_enabled'),
    CampaignStep(code='trial_back_7d_s2', title='Триал: неделя, касание 2',
                 query={**TRIAL_BASE, 'growth.days_since_expired': {'$gte': 4, '$lte': 21}},
                 text=_t('campaign.trial_back.s2'), after_step='trial_back_7d',
                 delay_days=5, settings_key='campaign.trial_enabled'),

    CampaignStep(code='trial_back_14d', title='Триал: две недели',
                 query={**TRIAL_BASE, 'growth.days_since_expired': {'$gte': 8, '$lte': 14}},
                 text=_t('campaign.trial_back.s1', when='около двух недель назад'),
                 settings_key='campaign.trial_enabled'),
    CampaignStep(code='trial_back_14d_s2', title='Триал: две недели, касание 2',
                 query={**TRIAL_BASE, 'growth.days_since_expired': {'$gte': 8, '$lte': 30}},
                 text=_t('campaign.trial_back.s2'), after_step='trial_back_14d',
                 delay_days=7, settings_key='campaign.trial_enabled'),

    CampaignStep(code='trial_back_30d', title='Триал: месяц',
                 query={**TRIAL_BASE, 'growth.days_since_expired': {'$gte': 15, '$lte': 30}},
                 text=_t('campaign.trial_back.s1', when='около месяца назад'),
                 settings_key='campaign.trial_enabled'),
    CampaignStep(code='trial_back_30d_s2', title='Триал: месяц, касание 2',
                 query={**TRIAL_BASE, 'growth.days_since_expired': {'$gte': 15, '$lte': 60}},
                 text=_t('campaign.trial_back.s2'), after_step='trial_back_30d',
                 delay_days=10, settings_key='campaign.trial_enabled'),
    CampaignStep(code='trial_back_30d_s3', title='Триал: месяц, касание 3',
                 query={**TRIAL_BASE, 'growth.days_since_expired': {'$gte': 15, '$lte': 90}},
                 text=_t('campaign.trial_back.s3'), after_step='trial_back_30d_s2',
                 delay_days=20, settings_key='campaign.trial_enabled'),
]

ALL_STEPS = NEW_TRIAL_STEPS + EXPIRED_STEPS + TRIAL_STEPS
BY_CODE = {step.code: step for step in ALL_STEPS}
