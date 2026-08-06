"""Менеджер устройств: лимит, покупка пакетов, отвязка."""

from __future__ import annotations

import hashlib

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Devices, Menu
from app.bot.filters.feature import Feature
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.bot.screens.pricing import price_line_for
from app.bot.screens.profile import devices_block, profile_caption
from app.content import texts
from app.core.errors import NotEnoughBalance, VpnPanelError

PACKAGES = (1, 2, 3, 5)


async def manager(event, c, user: dict, settings):
    free_limit = await settings.int('price.devices_free_limit')
    price = await settings.int('price.device_extra')
    connect_base = await settings.get('link.connect_base')

    kb = InlineKeyboardBuilder()
    for amount in PACKAGES:
        kb.add(types.InlineKeyboardButton(
            text=f'Увеличить на {amount} за {amount * price}₽',
            callback_data=Devices(action='add', value=str(amount)).pack()))
    kb.adjust(2)

    if int(c.users.pick(user, 'vpn.hwidDeviceLimit', 0)) > free_limit:
        kb.row(types.InlineKeyboardButton(
            text='➖ Уменьшить лимит на 1',
            callback_data=Devices(action='remove', value='1').pack()))
    kb.row(types.InlineKeyboardButton(
        text='📲 Мои устройства', callback_data=Devices(action='list').pack()))
    await footer(kb, settings, back='my_subscription')

    text = (profile_caption(user, '📲 Менеджер устройств')
            + devices_block(user, await price_line_for(c, user), connect_base)
            + f'<blockquote>📲 {texts.render("screen.devices.hint", free_devices=free_limit, device_price=price)}</blockquote>')

    await render(event, Screen(text=text, markup=kb.as_markup(), image=c.media('devices')))
    if isinstance(event, types.CallbackQuery):
        await event.answer()


async def add_devices(call: types.CallbackQuery, callback_data: Devices, c, user: dict,
                      settings):
    try:
        amount = int(callback_data.value)
        await c.devices.add(call.from_user.id, amount)
    except ValueError:
        await call.answer('Некорректное количество', show_alert=True)
        return
    except NotEnoughBalance as exc:
        await call.answer(exc.user_message, show_alert=True)
        return
    except VpnPanelError:
        await call.answer('Панель не ответила, попробуйте позже.', show_alert=True)
        return

    await call.answer(f'Добавлено устройств: {amount} ✅')
    await manager(call, c, await c.users.get(call.from_user.id), settings)


async def remove_devices(call: types.CallbackQuery, callback_data: Devices, c, user: dict,
                         settings):
    try:
        new_limit = await c.devices.remove(call.from_user.id, int(callback_data.value or 1))
    except VpnPanelError:
        await call.answer('Панель не ответила, попробуйте позже.', show_alert=True)
        return

    await call.answer(f'Лимит устройств: {new_limit}')
    await manager(call, c, await c.users.get(call.from_user.id), settings)


def device_token(hwid: str) -> str:
    """Короткий стабильный идентификатор устройства для кнопки.

    Сам hwid в callback_data не помещается: у Telegram на всё поле 64 байта,
    а hwid бывает длиннее. Раньше в кнопку ехал номер в списке, а сами hwid
    лежали в состоянии диалога — и отвязка ломалась, стоило человеку зайти в
    любой другой раздел: состояние очищалось, кнопка отвечала «список устарел».
    Хэш ни от чего не зависит, поэтому кнопка работает всегда.
    """
    return hashlib.sha1(hwid.encode()).hexdigest()[:16]


async def list_devices(call: types.CallbackQuery, c, user: dict, settings):
    uuid = c.users.pick(user, 'vpn.uuid')
    try:
        devices = await c.vpn.devices(uuid)
    except VpnPanelError:
        await call.answer('Не удалось загрузить устройства', show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for device in devices[:30]:
        hwid = device.get('hwid', '')
        title = device.get('deviceModel') or device.get('platform') or hwid[:12]
        kb.row(types.InlineKeyboardButton(
            text=f'🗑 {title}',
            callback_data=Devices(action='unbind', value=device_token(hwid)).pack()))
    await footer(kb, settings, back='devices')

    text = ('<b>📲 Ваши устройства</b>\n\nНажмите, чтобы отвязать. Отвязка освобождает '
            'слот, лимит при этом не меняется.') if devices else \
           '<b>📲 Устройства</b>\n\nПодключённых устройств пока нет.'
    await render(call, Screen(text=text, markup=kb.as_markup(),
                              image=c.media('devices_list')))
    await call.answer()


async def unbind(call: types.CallbackQuery, callback_data: Devices, c, user: dict, settings):
    uuid = c.users.pick(user, 'vpn.uuid')
    try:
        devices = await c.vpn.devices(uuid)
    except VpnPanelError:
        await call.answer('Панель не ответила, попробуйте позже.', show_alert=True)
        return

    hwid = next((d.get('hwid', '') for d in devices
                 if device_token(d.get('hwid', '')) == callback_data.value), None)
    if hwid is None:
        await call.answer('Это устройство уже отвязано', show_alert=True)
        await list_devices(call, c, user, settings)
        return

    removed = await c.devices.unbind(call.from_user.id, hwid)
    await call.answer('Устройство отвязано ✅' if removed else 'Не удалось отвязать',
                      show_alert=not removed)
    await list_devices(call, c, await c.users.get(call.from_user.id), settings)


def create_router() -> Router:
    """Собирает роутер раздела."""
    router = Router(name='devices')
    feature = Feature('features.devices_enabled')

    router.callback_query.register(manager, Menu.filter(F.screen == 'devices'), feature)
    router.callback_query.register(add_devices, Devices.filter(F.action == 'add'), feature)
    router.callback_query.register(remove_devices, Devices.filter(F.action == 'remove'), feature)
    router.callback_query.register(list_devices, Devices.filter(F.action == 'list'))
    router.callback_query.register(unbind, Devices.filter(F.action == 'unbind'))
    return router
