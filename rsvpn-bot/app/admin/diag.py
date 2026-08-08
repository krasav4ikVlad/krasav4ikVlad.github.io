"""/diag — что из фонового работает прямо сейчас.

Вопрос «идут ли напоминания и автопродление» неудобен тем, что ответы на
него живут в разных местах: продление — в планировщике бота, напоминания —
в вебхуках панели, которые принимает вообще другой процесс. По логам это
видно, но за логами надо идти на сервер.

Экран собирает всё в одно место и, главное, честно показывает пустоту:
«вебхуков от панели не было» — самый частый и самый полезный ответ, потому
что означает конкретное: панель не настроена или не достучалась.

Здесь только чтение и никаких действий: команду можно жать смело.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.admin import health
from app.bot.callbacks import Admin as Adm
from app.content.emoji import e
from app.core.time import fmt, now
from app.services.expiry import ALL_KEYS
from app.version import version_line


def ago(moment) -> str:
    """«12 минут назад» — по абсолютному времени непонятно, свежее это или нет."""
    if not moment:
        return 'не было'
    seconds = (now() - moment).total_seconds()
    if seconds < 90:
        return 'только что'
    if seconds < 3600:
        return f'{int(seconds // 60)} мин назад'
    if seconds < 86400:
        return f'{int(seconds // 3600)} ч назад'
    return fmt(moment)


def numbers(info: dict) -> str:
    return ', '.join(f'{key}={value}' for key, value in (info or {}).items()
                     if value not in (None, ''))


async def text(c, settings) -> str:
    marks = await c.health.read()
    scheduler = c.config.scheduler_enabled

    lines = [f'<b>{e("tools")} Диагностика</b>', f'Сборка: <code>{version_line()}</code>', '']

    # ── планировщик: автопродление и списания ───────────────────────────────
    lines.append(f'<b>{e("renew")} Автопродление</b>')
    if not scheduler:
        lines.append(f'{e("cross")} Планировщик выключен в этом процессе '
                     '(SCHEDULER_ENABLED=0) — списаний не будет')
    elif not await settings.flag('features.autorenew_enabled'):
        lines.append(f'{e("cross")} Выключено в настройках '
                     '(Функции → Автопродление)')
    else:
        lines.append(f'{e("ok")} Включено, проверка каждые 15 минут')

    renewal = marks.get(health.RENEWAL) or {}
    lines.append(f'Последний проход: <b>{ago(renewal.get("at"))}</b>')
    if renewal.get('info'):
        lines.append(f'<code>{numbers(renewal["info"])}</code>')

    devices = marks.get(health.DEVICES) or {}
    if devices.get('at'):
        lines.append(f'Плата за устройства: {ago(devices.get("at"))} '
                     f'<code>{numbers(devices.get("info"))}</code>')

    # ── напоминания: приходят вебхуками панели ──────────────────────────────
    lines.append(f'\n<b>{e("clock")} Напоминания об истечении</b>')
    if not await settings.flag('expiry.notify_enabled'):
        lines.append(f'{e("cross")} Выключены в настройках')
    else:
        on = [key for key in ALL_KEYS if await settings.flag(f'expiry.send_{key}')]
        lines.append(f'{e("ok")} Включены: <code>{", ".join(on) or "ни одного порога"}</code>')

    webhook = marks.get(health.PANEL_WEBHOOK) or {}
    lines.append(f'Событий от панели: <b>{ago(webhook.get("at"))}</b>'
                 + (f' (всего {webhook.get("runs", 0)})' if webhook.get('at') else ''))
    if webhook.get('info'):
        lines.append(f'<code>{numbers(webhook["info"])}</code>')

    sent = marks.get(health.EXPIRY_SENT) or {}
    lines.append(f'Последнее отправленное: <b>{ago(sent.get("at"))}</b>'
                 + (f' <code>{numbers(sent.get("info"))}</code>' if sent.get('at') else ''))

    if not webhook.get('at'):
        lines.append(
            f'\n<blockquote>{e("attention")} Панель ни разу не позвала бота. '
            'Напоминания приходят вебхуками, сам бот их не рассылает. '
            'Проверьте в <code>/opt/remnawave/.env</code>: '
            '<code>WEBHOOK_ENABLED=true</code>, '
            '<code>WEBHOOK_URL</code> на <code>/remnawave/webhook</code>, '
            '<code>EXPIRATION_NOTIFICATIONS_ENABLED=true</code> и совпадение '
            '<code>WEBHOOK_SECRET_HEADER</code> с REMNAWAVE_WEBHOOK_SECRET. '
            'И что процесс API запущен.</blockquote>')
    elif webhook.get('info', {}).get('note') == 'bad_signature':
        lines.append(f'\n<blockquote>{e("attention")} Панель зовёт, но подпись не '
                     'сходится: секрет в панели и в .env бота разные.</blockquote>')

    # ── кампании ────────────────────────────────────────────────────────────
    campaigns = marks.get(health.CAMPAIGNS) or {}
    lines.append(f'\n<b>{e("megaphone")} Кампании</b>: {ago(campaigns.get("at"))}'
                 + (f' <code>{numbers(campaigns.get("info"))}</code>'
                    if campaigns.get('at') else ''))
    return '\n'.join(lines)


async def command(message: types.Message, c, settings) -> None:
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text=f'{e("refresh")} Обновить', callback_data=Adm(act='diag').pack()))
    await message.answer(await text(c, settings), reply_markup=kb.as_markup())


async def refresh(call: types.CallbackQuery, c, settings) -> None:
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text=f'{e("refresh")} Обновить', callback_data=Adm(act='diag').pack()))
    try:
        await call.message.edit_text(await text(c, settings), reply_markup=kb.as_markup())
    except Exception:      # «message is not modified» — ничего не изменилось
        pass
    await call.answer('Обновлено')


def register(router: Router) -> None:
    router.message.register(command, Command('diag'))
    router.callback_query.register(refresh, Adm.filter(F.act == 'diag'))
