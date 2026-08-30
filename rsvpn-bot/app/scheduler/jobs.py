"""Фоновые задачи. Каждая — тонкая обёртка над сервисом.

Ни одна задача не содержит бизнес-логики: планировщик решает «когда»,
сервис — «что». Тумблеры в админке проверяются здесь, чтобы выключенная
задача не тратила запросы к БД.
"""

from __future__ import annotations

import logging

from app.admin import health
from app.campaigns.definitions import EXPIRED_STEPS, NEW_TRIAL_STEPS, TRIAL_STEPS

log = logging.getLogger(__name__)


async def run_campaigns(container, bot, engine) -> None:
    report = await engine.run(NEW_TRIAL_STEPS + EXPIRED_STEPS + TRIAL_STEPS)
    await container.health.mark(health.CAMPAIGNS, sent=report.sent,
                                credited=getattr(report, 'credited', 0))
    if report.sent and container.notifier:
        await container.notifier.campaign_report(report)


async def watch_broadcasts(container, bot) -> None:
    """Поднять рассылки, которые оборвались и сами не продолжатся."""
    from app.admin.broadcast import resume_stalled

    revived = await resume_stalled(container, bot)
    if revived:
        log.warning('сторож поднял рассылок: %s', revived)


async def charge_subscriptions(container) -> None:
    """Автопродление и плата за доп. устройства.

    Напоминания об истечении сюда НЕ переносятся: их присылает панель
    вебхуками (app/services/expiry.py). Здесь только деньги — и эта же задача
    страхует, если вебхук не дошёл.
    """
    # Пропуск логируем громко: раньше незаполненный сервис означал, что
    # автопродление и плата за устройства молча не работают вообще, и по
    # логам это выглядело как «задача отработала».
    if container.renewal:
        report = await container.renewal.run()
        # Отметка для /diag: по логам видно то же самое, но за ними надо
        # идти на сервер, а вопрос «работает ли автопродление» возникает
        # обычно с телефона.
        await container.health.mark(
            health.RENEWAL, checked=report.checked, renewed=report.renewed,
            no_funds=report.no_funds, failed=report.failed)
    else:
        log.warning('автопродление пропущено: сервис renewal не собран')

    if container.device_billing:
        report = await container.device_billing.run()
        await container.health.mark(
            health.DEVICES, charged=report.charged, amount=report.amount,
            deactivated=report.deactivated)
        if report.charged or report.deactivated:
            log.info('устройства: списано %s пакетов на %s₽, отключено %s',
                     report.charged, report.amount, report.deactivated)
    else:
        log.warning('плата за устройства пропущена: сервис не собран')


async def charge_private_servers(container) -> None:
    """Ежемесячная плата за личные серверы владельцам."""
    if not container.private:
        return
    report = await container.private.charge_due()
    if report['checked']:
        log.info('личные серверы: списано %s на %s₽, приостановлено %s, закрыто %s',
                 report['charged'], report['amount'], report['suspended'],
                 report['closed'])
        await container.health.mark(health.PRIVATE_SERVERS, **report)


async def reconcile_lifeline(container) -> None:
    """Вернуть тех, кто продлился, но остался на запасном сервере."""
    if not container.lifeline:
        log.warning('lifeline не собран — возврат подписок пропущен')
        return
    restored = await container.lifeline.reconcile()
    if restored:
        log.info('lifeline: возвращено %s подписок', restored)


async def sync_bypass(container) -> None:
    """Свести даты ByPass с основными подписками.

    Все места, которые двигают срок, синхронизируют ByPass сами. Задача —
    страховка: панель могла не ответить в тот момент, а расхождение видно
    человеку как «VPN отключился раньше времени».
    """
    from app.services import bypass

    if container.vpn is None:
        log.warning('сверка ByPass пропущена: клиент панели не собран')
        return

    report = await bypass.repair(container.users, container.vpn, apply=True)
    if report['checked']:
        log.warning('ByPass: расхождений %s, выровнено %s, не вышло %s',
                    report['checked'], report['fixed'], report['failed'])
    # Отметку ставим всегда, даже когда всё сошлось: иначе «не запускалась»
    # и «запускалась, расхождений нет» в /diag неразличимы.
    await container.health.mark(health.BYPASS_SYNC, **{
        key: report[key] for key in ('checked', 'fixed', 'failed')})


async def migrate_panel_ids(container) -> None:
    """Перевести подписки на числовые id, когда панель обновят до 3.x.

    Пока панель до 3.0, задача выходит после одного запроса: ей нечего
    делать. После обновления она сама переведёт всех, и день обновления не
    превращается в день, когда надо вспомнить про команду.
    """
    from app.services import panel_ids

    if container.vpn is None:
        return

    report = await panel_ids.migrate(container.users, container.vpn, apply=True)
    if report['panel'] == 'old':
        return                      # панель прежняя — отмечать нечего

    if report['moved'] or report['failed']:
        log.warning('панель 3.x: переведено %s, не вышло %s',
                    report['moved'], report['failed'])
    await container.health.mark(health.PANEL_IDS, **{
        key: report[key] for key in ('stale', 'moved', 'failed')})


async def update_segments(container) -> None:
    """Пересчёт growth.segment — без него кампании никого не найдут."""
    from app.services.segments import SegmentService

    report = await SegmentService(container.users, container.settings).run()
    log.info('сегменты пересчитаны: %s', report.by_segment)
