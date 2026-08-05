"""Менеджер устройств: лимит, покупка пакетов, отвязка."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Devices, Menu
from app.bot.filters.feature import Feature
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.bot.screens.profile import devices_block, profile_caption
from app.core.errors import NotEnoughBalance, VpnPanelError

PACKAGES = (1, 2, 3, 5)


async def manager(call: types.CallbackQuery, c, user: dict, settings):
    free_limit = await settings.int('price.devices_free_limit')
    price = await settings.int('price.device_extra')

    kb = InlineKeyboardBuilder()
    for amount in PACKAGES:
        kb.add(types.InlineKeyboardButton(
            text=f'+{amount} за {amount * price}₽',
            callback_data=Devices(action='add', value=str(amount)).pack()))
    kb.adjust(2)

    if int(c.users.pick(user, 'vpn.hwidDeviceLimit', 0)) > free_limit:
        kb.row(types.InlineKeyboardButton(
            text='➖ Уменьшить лимит', callback_data=Devices(action='remove', value='1').pack()))
    kb.row(types.InlineKeyboardButton(
        text='📲 Мои устройства', callback_data=Devices(action='list').pack()))
    await footer(kb, settings, back='my_subscription')

    await render(call, Screen(
        text=profile_caption(user) + devices_block(user, free_limit, price),
        markup=kb.as_markup(), image=c.media('devices')))
    await call.answer()


async def add_devices(call: types.CallbackQuery, callback_data: Devices, c, user: dict, settings):
    try:
        amount = int(callback_data.value)
    except ValueError:
        await call.answer('Некорректное количество', show_alert=True)
        return

    try:
        await c.devices.add(call.from_user.id, amount)
    except NotEnoughBalance as exc:
        await call.answer(exc.user_message, show_alert=True)
        return
    except VpnPanelError:
        await call.answer('Панель не ответила, попробуйте позже.', show_alert=True)
        return

    await call.answer(f'Добавлено устройств: {amount} ✅')
    await manager(call, c, await c.users.get(call.from_user.id), settings)


async def list_devices(call: types.CallbackQuery, c, user: dict, settings):
    uuid = c.users.pick(user, 'vpn.uuid')
    try:
        devices = await c.vpn.devices(uuid)
    except VpnPanelError:
        await call.answer('Не удалось загрузить устройства', show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for device in devices[:30]:
        title = device.get('deviceModel') or device.get('platform') or device.get('hwid', '')[:12]
        kb.row(types.InlineKeyboardButton(
            text=f'🗑 {title}',
            callback_data=Devices(action='unbind', value=device.get('hwid', '')[:40]).pack()))
    await footer(kb, settings, back='devices')

    text = ('<b>📲 Ваши устройства</b>\n\nНажмите, чтобы отвязать. Отвязка освобождает '
            'слот, лимит при этом не меняется.') if devices else \
           '<b>📲 Устройства</b>\n\nПодключённых устройств пока нет.'
    await render(call, Screen(text=text, markup=kb.as_markup()))
    await call.answer()


async def unbind(call: types.CallbackQuery, callback_data: Devices, c, user: dict, settings):
    removed = await c.devices.unbind(call.from_user.id, callback_data.value)
    await call.answer('Устройство отвязано ✅' if removed else 'Не удалось отвязать',
                      show_alert=not removed)
    await list_devices(call, c, await c.users.get(call.from_user.id), settings)


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='devices')
    router.callback_query.register(manager, Menu.filter(F.screen == 'devices'), Feature('features.devices_enabled'))
    router.callback_query.register(add_devices, Devices.filter(F.action == 'add'), Feature('features.devices_enabled'))
    router.callback_query.register(list_devices, Devices.filter(F.action == 'list'))
    router.callback_query.register(unbind, Devices.filter(F.action == 'unbind'))
    return router
