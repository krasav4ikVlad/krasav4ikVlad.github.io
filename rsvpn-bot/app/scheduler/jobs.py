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
    """Автопродление и плата за доп. устройства.

    Напоминания об истечении сюда НЕ переносятся: их присылает панель
    вебхуками (app/services/expiry.py). Здесь только деньги — и эта же задача
    страхует, если вебхук не дошёл.
    """
    if container.renewal:
        await container.renewal.run()
    if container.device_billing:
        report = await container.device_billing.run()
        if report.charged or report.deactivated:
            log.info('устройства: списано %s пакетов на %s₽, отключено %s',
                     report.charged, report.amount, report.deactivated)


async def reconcile_lifeline(container) -> None:
    """Вернуть тех, кто продлился, но остался на запасном сервере."""
    if not container.lifeline:
        return
    restored = await container.lifeline.reconcile()
    if restored:
        log.info('lifeline: возвращено %s подписок', restored)


async def update_segments(container) -> None:
    """Пересчёт growth.segment — без него кампании никого не найдут."""
    from app.services.segments import SegmentService

    report = await SegmentService(container.users, container.settings).run()
    log.info('сегменты пересчитаны: %s', report.by_segment)
