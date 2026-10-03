"""Экраны и клавиатуры — один раз, а не 40 раз копипастой.

В start.py один и тот же блок «Профиль / Баланс / Друзей / Почта» повторяется
больше 20 раз, и рядом с ним каждый раз один и тот же try/except с
edit_message_media. Здесь это две функции: profile_header() и show_screen().
"""

from __future__ import annotations

from datetime import datetime

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.settings import S

EMOJI = {
    'user': '5316934955694072975',
    'id': '5316813090292015176',
    'money': '5317017827088049911',
    'friends': '5316564321491260174',
    'mail': '5316596297522781199',
    'calendar': '5321162912910319482',
    'devices': '5318881086980265568',
    'pay': '5319108887750679115',
    'link': '5318920660808932171',
    'shield': '5319082443637037842',
    'back': '5321133913291135005',
    'support': '5318943836452462111',
    'channel': '5321530076779551906',
    'warn': '5321083387295869258',
    'renew': '5321024322905613389',
}


def e(name: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{EMOJI[name]}">{fallback}</tg-emoji>'


def profile_header(user: dict, title: str = 'Профиль', icon: str = 'user') -> str:
    """Тот самый повторяющийся блок. Меняется в одном месте — меняется везде."""
    info = user.get('info') or {}
    ref_stats = info.get('ref_stats') or {}
    return (
        f'<b>{e(icon, "👤")} {title}</b>\n\n'
        f'<b>{e("id", "🆔")} Идентификатор:</b> <code>{user["user_data"]["user_id"]}</code>\n'
        f'<b>{e("money", "💰")} Баланс:</b> <code>{info.get("balance", 0)}₽</code>\n'
        f'<b>{e("friends", "👥")} Друзей:</b> <code>{len(ref_stats.get("referrals") or [])}</code>\n'
        f'<b>{e("mail", "✉️")} Почта:</b> <code>{info.get("email", "Не привязана")}</code>\n\n'
    )


async def subscription_lines(user: dict) -> str:
    """Блок с датой окончания / лимитом устройств / платой — тоже был везде."""
    from core.plans import subscription_monthly_cost

    vpn = user.get('vpn') or {}
    expire = vpn.get('expireAt')
    expire_text = expire.strftime('%d.%m.%Y %H:%M') if isinstance(expire, datetime) else str(expire or '—')
    cost = await subscription_monthly_cost(user)
    base = await S.get('link.connect_base')

    return (
        f'<b>{e("calendar", "📅")} Дата окончания:</b> <code>{expire_text}</code>\n'
        f'<b>{e("devices", "📲")} Лимит устройств:</b> <code>{vpn.get("hwidDeviceLimit", 0)}</code>\n'
        f'<b>{e("pay", "💸")} Плата за подписку:</b> <code>{cost}₽</code>\n\n'
        f'<b>{e("link", "🔗")} Ссылка на подключение:</b> {base}{vpn.get("shortUuid", "")}\n\n'
    )


async def add_footer(kb: InlineKeyboardBuilder, back: str | None = 'menu:main') -> InlineKeyboardBuilder:
    """Кнопки «Назад / Поддержка / Канал». Ссылки редактируются в админке."""
    if back:
        kb.row(types.InlineKeyboardButton(
            text='Назад', callback_data=back, icon_custom_emoji_id=EMOJI['back']))
    kb.row(types.InlineKeyboardButton(
        text='Поддержка', url=await S.get('link.support'), icon_custom_emoji_id=EMOJI['support']))
    kb.add(types.InlineKeyboardButton(
        text='RS VPN', url=await S.get('link.channel'), icon_custom_emoji_id=EMOJI['channel']))
    return kb


async def show_screen(
    event: types.Message | types.CallbackQuery,
    text: str,
    kb: InlineKeyboardBuilder | types.InlineKeyboardMarkup | None = None,
    image: str | None = None,
):
    """Единая отрисовка экрана.

    Для callback — правит текущее сообщение (медиа/подпись/текст),
    для message — отправляет новое. Все try/except внутри.
    """
    markup = kb.as_markup() if isinstance(kb, InlineKeyboardBuilder) else kb

    if isinstance(event, types.CallbackQuery):
        message = event.message
        if image:
            try:
                await message.bot.edit_message_media(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    media=types.InputMediaPhoto(media=types.FSInputFile(image), caption=text),
                    reply_markup=markup,
                )
                return message
            except Exception:
                pass
        try:
            if message.photo:
                return await message.edit_caption(caption=text, reply_markup=markup)
            return await message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            return await message.answer(text, reply_markup=markup, disable_web_page_preview=True)

    if image:
        try:
            return await event.answer_photo(
                photo=types.FSInputFile(image), caption=text, reply_markup=markup)
        except Exception:
            pass
    return await event.answer(text, reply_markup=markup, disable_web_page_preview=True)


async def notify_admins_chat(bot, text: str, thread_id: int | None = None) -> None:
    """Одна функция вместо шести одинаковых try/except с send_message в лог-чат."""
    try:
        await bot.send_message(
            chat_id=await S.int('admin.log_chat_id'),
            message_thread_id=thread_id,
            text=text,
        )
    except Exception as err:  # noqa: BLE001 - уведомление не должно ломать основной поток
        print(f'Ошибка отправки админ-уведомления: {err}')
