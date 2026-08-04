"""Фоновые задачи. Каждая — тонкая обёртка над сервисом.

Ни одна задача не содержит бизнес-логики: планировщик решает «когда»,
сервис — «что». Тумблеры в админке проверяются здесь, чтобы выключенная
задача не тратила запросы к БД.
"""

from __future__ import annotations

import logging

from app.campaigns.definitions import EXPIRED_STEPS, NEW_TRIAL_STEPS, TRIAL_STEPS

log = logging.getLogger(__name__)


async def run_campaigns(container, bot, engine) -> None:
    report = await engine.run(NEW_TRIAL_STEPS + EXPIRED_STEPS + TRIAL_STEPS)
    if report.sent and container.notifier:
        await container.notifier.campaign_report(report)


async def charge_subscriptions(container) -> None:
    """Ежедневное списание за подписку и устройства.

    Переносится из utils.process_subscriptions (сейчас ~290 строк в одной
    функции). Разбейте на: выборка истекающих → расчёт цены (domain/pricing)
    → списание (users.charge) → продление в панели → уведомление.
    """
    if not await container.settings.flag('features.autorenew_enabled'):
        log.info('автопродление выключено в админке')
        return
    raise NotImplementedError


async def update_segments(container) -> None:
    """Пересчёт growth.segment по правилам из domain/segments.py."""
    raise NotImplementedError
