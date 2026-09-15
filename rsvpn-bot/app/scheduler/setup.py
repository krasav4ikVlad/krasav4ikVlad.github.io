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
        sender=Sender(on_blocked=container.users.mark_blocked),
        keyboards=campaign_keyboards(),
        runs_collection=container.db[names.CAMPAIGN_RUNS],
        plans=container.plans,
    )

    scheduler.add_job(jobs.charge_subscriptions, 'interval', minutes=15,
                      args=[container], id='charge_subscriptions')
    # Раз в час: месячная плата за личные серверы. Чаще незачем — списание
    # привязано к дате, а не к моменту.
    scheduler.add_job(jobs.charge_private_servers, 'interval', minutes=60,
                      args=[container], id='charge_private_servers')
    scheduler.add_job(jobs.reconcile_lifeline, 'interval', minutes=15,
                      args=[container], id='reconcile_lifeline')
    scheduler.add_job(jobs.update_segments, 'interval', minutes=60,
                      args=[container], id='update_segments',
                      next_run_time=now() + timedelta(minutes=5))
    # Раз в минуту: рассылка на 190 тысяч писем идёт часами и переживает
    # не всё. Продолжать её руками — значит следить за экраном; сторож
    # делает это сам.
    scheduler.add_job(jobs.watch_broadcasts, 'interval', minutes=1,
                      args=[container, bot], id='watch_broadcasts',
                      next_run_time=now() + timedelta(minutes=1))
    # Раз в шесть часов: даты ByPass и основной подписки должны совпадать.
    # Сами сервисы их и так выравнивают — это подбор за отказавшей панелью
    # и за подписками, приехавшими из старого бота.
    scheduler.add_job(jobs.sync_bypass, 'interval', minutes=360,
                      args=[container], id='sync_bypass',
                      next_run_time=now() + timedelta(minutes=3))
    # Раз в час: перевод подписок на числовые id панели 3.x. Пока панель
    # прежняя, задача стоит одного запроса и выходит — держать её включённой
    # заранее дешевле, чем вспоминать про неё в день обновления панели.
    scheduler.add_job(jobs.migrate_panel_ids, 'interval', minutes=60,
                      args=[container], id='migrate_panel_ids',
                      next_run_time=now() + timedelta(minutes=7))
    scheduler.add_job(jobs.run_campaigns, 'interval', minutes=60,
                      args=[container, bot, engine], id='campaigns',
                      next_run_time=now() + timedelta(minutes=10))
    return scheduler
