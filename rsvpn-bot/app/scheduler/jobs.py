"""Фоновые задачи. Каждая — тонкая обёртка над сервисом.

Ни одна задача не содержит бизнес-логики: планировщик решает «когда»,
сервис — «что». Тумблеры в админке проверяются здесь, чтобы выключенная
задача не тратила запросы к БД.
"""

from __future__ import annotations

import asyncio
import functools
import logging

from app.admin import health
from app.core.time import fmt, parse_dt
from app.campaigns.definitions import EXPIRED_STEPS, NEW_TRIAL_STEPS, TRIAL_STEPS

log = logging.getLogger(__name__)


def quiet_on_stop(job):
    """Остановка бота — не авария задачи.

    При `pm2 restart` планировщик отменяет всё, что выполняется, и каждая
    такая задача печатает в лог CancelledError со стектрейсом. В логе это
    выглядит как падение — и в первый раз отнимает полчаса на поиск
    несуществующей ошибки. Здесь отмена превращается в одну понятную
    строку.

    Дальше отмену не пропускаем сознательно: задача — лист, после неё
    ничего не ждёт, а свою уборку сервисы делают сами.
    """
    @functools.wraps(job)
    async def wrapper(*args, **kwargs):
        try:
            return await job(*args, **kwargs)
        except asyncio.CancelledError:
            log.warning('задача %s прервана остановкой бота', job.__name__)
            return None

    return wrapper


@quiet_on_stop
async def run_campaigns(container, bot, engine) -> None:
    report = await engine.run(NEW_TRIAL_STEPS + EXPIRED_STEPS + TRIAL_STEPS)
    await container.health.mark(health.CAMPAIGNS, sent=report.sent,
                                credited=getattr(report, 'credited', 0))
    if report.sent and container.notifier:
        await container.notifier.campaign_report(report)


@quiet_on_stop
async def watch_database(container, bot) -> None:
    """Достаёт ли бот до базы. Пишет админам лично, если нет.

    Живёт здесь, а не среди уведомлений: всё остальное в боте при лежащей
    базе молчит, и этот сторож — единственное, что должно продолжать
    работать. Поэтому он не читает ни настроек, ни коллекций.
    """
    watchdog = getattr(container, 'watchdog', None)
    if watchdog is None:
        return
    await watchdog.check()


@quiet_on_stop
async def thaw_torrents(container) -> None:
    """Вернуть доступ тем, у кого заморозка за торренты кончилась.

    Заморозку ставит вебхук панели, а снимать её некому: панель второй раз
    не позвонит. Раз в минуту — потому что «на полчаса» должно означать
    полчаса, а не «полчаса и сколько-то сверху».
    """
    guard = getattr(container, 'torrents', None)
    if guard is None:
        return
    await guard.thaw()


@quiet_on_stop
async def backup_database(container, bot) -> None:
    """Снимок базы по расписанию — с разговором о любой неудаче.

    Обёртка нужна ради последней строчки: что бы ни сломалось, человек
    должен это увидеть. Молча упавшая в лог задача копий не делает, а
    выглядит точно так же, как работающая.
    """
    try:
        await _backup_once(container)
    except asyncio.CancelledError:
        raise
    except Exception as exc:      # noqa: BLE001 — причину показываем человеку
        log.exception('копия базы: непредвиденная ошибка')
        guard = getattr(container, 'backup', None)
        notifier = getattr(container, 'notifier', None)
        if guard is None or notifier is None:
            return
        from app.content.emoji import e

        text = (f'{e("cross")} <b>Копия базы: ошибка</b>\n'
                f'<code>{str(exc)[:500]}</code>\n\n'
                f'Подробности — в логах: '
                f'<code>pm2 logs rsvpn-bot --lines 200</code>')
        await notifier.edit_all(await guard.pending_cards(), text)
        await guard.drop_cards()
        await notifier.dm(text)


async def _backup_once(container) -> None:
    """Снимок базы по расписанию.

    Задача сама решает, пора ли: по назначенному часу и по тому, есть ли
    снимок после него. Так снимок привязан к полуночи, не теряется из-за
    перезапуска бота и не делается дважды подряд после него.

    Всё, что происходит, видно в одном личном сообщении: оно приходит с
    «начинаю», потом обрастает полоской, а в конце становится отчётом.
    Отдельные сообщения на каждый шаг превратили бы ночную копию в
    ночную рассылку.
    """
    from pathlib import Path

    from app.content import progress
    from app.content.emoji import e
    from app.services.backup import human_size

    guard = getattr(container, 'backup', None)
    if guard is None or not await container.settings.flag('backup.enabled'):
        return

    # Первым делом дописываем судьбу прошлой полоски. Её оборвали вместе
    # с процессом, сама она этого сказать не могла — и осталась на экране
    # навсегда, выглядя работой.
    await _finish_frozen(container)

    if not await guard.due():
        # Три обрыва подряд — это не невезение, это что-то снаружи убивает
        # процесс. Сказать об этом надо один раз и внятно, иначе человек
        # будет неделю смотреть на недоделанные полоски.
        await _warn_about_breaks(container)
        return
    if guard.running():
        # Ещё одна дверь на тот же замок: due() смотрит на файл, этот —
        # на сам процесс. Сообщение «начинаю» не должно уйти вообще.
        log.info('копия уже делается — задача пропущена')
        return

    notifier = container.notifier
    personally = bool(notifier) and await container.settings.flag('backup.to_telegram')
    title = f'{e("document")} Копия базы'
    cards = []
    if personally:
        cards = await notifier.dm_progress(progress.screen(
            title, 'Начинаю. Считаю, сколько всего документов…'))

    await guard.note_progress(0, 0, 'Начинаю', cards=cards)
    ticker = progress.Ticker()

    async def show(step: str, done: int, total: int) -> None:
        if not ticker.should(progress.percent(done, total)):
            return
        # Сначала в файл, потом на экран: если процесс убьют прямо сейчас,
        # следующий запуск должен знать, на чём копия остановилась.
        await guard.note_progress(done, total, step)
        if cards:
            await notifier.edit_all(cards,
                                    progress.screen(title, step, done, total))

    report = await guard.run(on_progress=show)
    if report.busy:
        # Успели начать вдвоём — убираем своё «начинаю», чтобы не осталось
        # висеть недоделанной полоски.
        await notifier.edit_all(cards, progress.screen(
            title, 'Копия уже делается другим запуском — отменил свой.', 1, 1))
        return

    await container.health.mark(health.BACKUP, ok=report.ok, docs=report.docs,
                                size=report.size, error=report.error)

    if not notifier:
        return

    if not report.ok:
        # Провал говорим громко и во все стороны: молчащий бекап
        # неотличим от работающего ровно до того дня, когда он понадобится.
        await notifier.edit_all(cards, progress.screen(
            title, f'{e("cross")} Не получилось: {report.error}', 0, 1))
        await notifier.backup_failed(report.error)
        return

    name = Path(report.path).name
    done = ((f'{e("warning")} <i>Прошлая копия оборвалась — нашёл '
             f'недописанный файл и убрал его.</i>\n\n'
             if report.after_break else '')
            + f'{e("ok")} <b>Копия базы готова</b>\n'
            f'<code>{name}</code>\n'
            f'<b>Размер:</b> {human_size(report.size)}\n'
            f'<b>Документов:</b> <code>{report.docs}</code>\n'
            f'<b>Заняло:</b> {report.seconds:.0f} с'
            + (f'\n<i>Старых удалено: {report.removed}</i>'
               if report.removed else ''))

    if cards:
        await notifier.edit_all(cards, done)
    else:
        # Личка выключена — тогда хотя бы строчка в админ-чат.
        await notifier.backup_done(name=name, size=report.size,
                                   docs=report.docs, seconds=report.seconds,
                                   removed=report.removed)

    if not personally:
        return
    # В личку админам из .env, а не в общий чат: в снимке вся база, и
    # адресатов у неё должно быть ровно столько, сколько людей имеет право
    # её видеть. Заодно личку не потерять при смене админ-чата.
    sent = await notifier.backup_file(
        report.path,
        limit_mb=await container.settings.int('backup.max_mb'),
        chat_ids=container.config.admin_ids,
        backup=guard)
    if not sent:
        log.warning('копия базы никому не ушла: %s', report.path)


@quiet_on_stop
async def _finish_frozen(container) -> None:
    """Дописать в застывшую полоску, чем всё кончилось."""
    from app.content import progress
    from app.content.emoji import e

    guard = container.backup
    cards = await guard.pending_cards()
    if not cards or not container.notifier:
        return

    state = await guard.state()
    done = int(state.get('done') or 0)
    total = int(state.get('total') or 0)
    wait_until = await guard.retry_after()

    await container.notifier.edit_all(cards, progress.screen(
        f'{e("attention")} Копия базы прервана',
        f'{state.get("note") or "Копия не доделана"}\n\n'
        f'Процесс бота остановился посреди работы. Обрывов подряд: '
        f'<b>{await guard.breaks()}</b>.\n'
        f'Следующая попытка: '
        + (fmt(wait_until, '%d.%m %H:%M') if wait_until else 'на ближайшей '
                                                            'проверке')
        + f'\nРаньше — <code>/backup force</code>',
        done, total, at=parse_dt(state.get('broken_at') or state.get('at'))))
    await guard.drop_cards()


async def _warn_about_breaks(container) -> None:
    from app.services import backup as service

    guard = container.backup
    breaks = await guard.breaks()
    if breaks < service.GIVE_UP_AFTER:
        return

    state = await guard.state()
    if state.get('told'):
        return
    state['told'] = True
    await guard._save_state(state)

    from app.content.emoji import e

    text = (f'{e("attention")} <b>Копия базы не доходит до конца</b>\n\n'
            f'Подряд прервано попыток: <b>{breaks}</b>. Каждый раз процесс '
            f'бота останавливается посреди копии — сама копия так не '
            f'падает.\n\n'
            f'Что посмотреть на сервере:\n'
            f'<code>pm2 describe rsvpn-bot</code> — счётчик restarts и '
            f'память\n'
            f'<code>pm2 logs rsvpn-bot --lines 200</code> — что было перед '
            f'стартом\n'
            f'<code>df -h</code> — место на диске\n\n'
            f'Частая причина — лимит памяти в ecosystem.config.js '
            f'(<code>max_memory_restart</code>): снимок большой базы в него '
            f'не помещается, и pm2 перезапускает бота.\n\n'
            f'Следующая попытка — по расписанию. Раньше — '
            f'<code>/backup force</code>.')
    if container.notifier:
        await container.notifier.dm(text)
        await container.notifier.send('backup', text)


@quiet_on_stop
async def watch_broadcasts(container, bot) -> None:
    """Поднять рассылки, которые оборвались и сами не продолжатся."""
    from app.admin.broadcast import resume_stalled

    revived = await resume_stalled(container, bot)
    if revived:
        log.warning('сторож поднял рассылок: %s', revived)


@quiet_on_stop
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


@quiet_on_stop
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


@quiet_on_stop
async def reconcile_lifeline(container) -> None:
    """Вернуть тех, кто продлился, но остался на запасном сервере."""
    if not container.lifeline:
        log.warning('lifeline не собран — возврат подписок пропущен')
        return
    restored = await container.lifeline.reconcile()
    if restored:
        log.info('lifeline: возвращено %s подписок', restored)


@quiet_on_stop
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


@quiet_on_stop
async def migrate_panel_ids(container) -> None:
    """Перевести подписки на числовые id, когда панель обновят до 3.x.

    Пока панель до 3.0, задача выходит после одного запроса: ей нечего
    делать. После обновления она сама переведёт всех, и день обновления не
    превращается в день, когда надо вспомнить про команду.

    Откат с 3.x обратно на 2.x она не разгребает — для этого есть команда
    /panelids fix и скрипт. Искать следы отката сама она не может дёшево:
    это перебор всей базы, и делать его каждый час ради события, которого
    не было, — плата ни за что.
    """
    from app.services import panel_ids

    if container.vpn is None:
        return

    # scan_old=False: пока панель прежняя, задача и правда стоит одного
    # запроса. Без этого она каждый час перебирала всю базу в поисках следов
    # отката с 3.x — отката, которого не было.
    report = await panel_ids.migrate(container.users, container.vpn, apply=True,
                                     scan_old=False)
    if report['panel'] == 'old':
        return                      # панель прежняя — отмечать нечего

    if report['moved'] or report['failed']:
        log.warning('панель 3.x: переведено %s, не вышло %s',
                    report['moved'], report['failed'])
    await container.health.mark(health.PANEL_IDS, **{
        key: report[key] for key in ('stale', 'moved', 'failed')})


@quiet_on_stop
async def update_segments(container) -> None:
    """Пересчёт growth.segment — без него кампании никого не найдут."""
    from app.services.segments import SegmentService

    report = await SegmentService(container.users, container.settings).run()
    log.info('сегменты пересчитаны: %s', report.by_segment)
