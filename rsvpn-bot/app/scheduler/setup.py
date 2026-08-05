"""Регистрация задач в APScheduler."""

from __future__ import annotations

from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.campaigns.engine import CampaignEngine
from app.campaigns.sender import Sender
from app.bot.keyboards.common import campaign_keyboards
from app.core import db as names
from app.core.time import now
from app.scheduler import jobs


def create_scheduler(container, bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=container.config.timezone)

    engine = CampaignEngine(
        bot=bot, users=container.users, settings=container.settings,
        sender=Sender(), keyboards=campaign_keyboards(),
        runs_collection=container.db[names.CAMPAIGN_RUNS],
    )

    scheduler.add_job(jobs.charge_subscriptions, 'interval', minutes=15,
                      args=[container], id='charge_subscriptions')
    scheduler.add_job(jobs.reconcile_lifeline, 'interval', minutes=15,
                      args=[container], id='reconcile_lifeline')
    scheduler.add_job(jobs.update_segments, 'interval', minutes=60,
                      args=[container], id='update_segments',
                      next_run_time=now() + timedelta(minutes=5))
    scheduler.add_job(jobs.run_campaigns, 'interval', minutes=60,
                      args=[container, bot, engine], id='campaigns',
                      next_run_time=now() + timedelta(minutes=10))
    return scheduler
