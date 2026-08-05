"""ByPass: отдельный конфиг для обхода белых списков."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.filters.feature import Feature
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.core.errors import VpnPanelError
from app.core.time import parse_dt

GB = 1024 ** 3
TRAFFIC_PACKAGES = (10, 25, 50, 100)


async def bypass(call: types.CallbackQuery, c, user: dict, settings):
    vpn = user.get('vpn') or {}
    connect_base = await settings.get('link.connect_base')
    price_gb = await settings.int('bypass.price_per_gb')

    kb = InlineKeyboardBuilder()

    if not vpn.get('bypass_uuid'):
        text = ('<b>🚧 Белые списки</b>\n\n'
                'ByPass — отдельный конфиг для сетей, где работают только разрешённые '
                'сайты. Он тестовый: полной гарантии работы нет.\n\n'
                'Подключение бесплатное, платить нужно только за трафик.')
        kb.row(types.InlineKeyboardButton(
            text='➕ Подключить ByPass', callback_data=Menu(screen='bypass_create').pack()))
    else:
        left_gb = round(int(vpn.get('bypass_trafficLimitBytes') or 0) / GB, 1)
        text = ('<b>🚧 Белые списки</b>\n\n'
                f'<b>Трафик:</b> <code>{left_gb} Гб</code>\n'
                f'<b>Действует до:</b> <code>{parse_dt(vpn.get("bypass_expireAt")) or "—"}</code>\n\n'
                f'<b>🔗 Ссылка:</b> {connect_base}{vpn.get("bypass_shortUuid", "")}')
        for amount in TRAFFIC_PACKAGES:
            kb.add(types.InlineKeyboardButton(
                text=f'{amount} Гб — {amount * price_gb}₽',
                callback_data=Menu(screen='bypass_buy', arg=str(amount)).pack()))
        kb.adjust(2)

    await footer(kb, settings, back='my_subscription')
    await render(call, Screen(text=text, markup=kb.as_markup(), image=c.media('bypass')))
    await call.answer()


async def create(call: types.CallbackQuery, c, user: dict, settings):
    vpn = user.get('vpn') or {}
    expire = parse_dt(vpn.get('expireAt'))
    if not expire:
        await call.answer('Сначала подключите основную подписку', show_alert=True)
        return

    try:
        created = await c.vpn.create_bypass_subscription(call.from_user.id, expire)
    except VpnPanelError:
        await call.answer('Панель не ответила, попробуйте позже.', show_alert=True)
        return

    await c.users.set_vpn(call.from_user.id, {
        'bypass_uuid': created.get('uuid', ''),
        'bypass_shortUuid': created.get('shortUuid', ''),
        'bypass_expireAt': expire,
        'bypass_trafficLimitBytes': created.get('trafficLimitBytes', 0),
    })
    await call.answer('ByPass подключён ✅')
    await bypass(call, c, await c.users.get(call.from_user.id), settings)


async def buy_traffic(call: types.CallbackQuery, callback_data: Menu, c, user: dict, settings):
    try:
        amount = int(callback_data.arg)
    except (TypeError, ValueError):
        await call.answer('Некорректный объём', show_alert=True)
        return

    price = amount * await settings.int('bypass.price_per_gb')
    uuid = c.users.pick(user, 'vpn.bypass_uuid')
    if not uuid:
        await call.answer('Сначала подключите ByPass', show_alert=True)
        return

    if not await c.users.charge(call.from_user.id, price, f'ByPass: {amount} Гб'):
        await call.answer(f'Не хватает средств: нужно {price}₽', show_alert=True)
        return

    current = int(c.users.pick(user, 'vpn.bypass_trafficLimitBytes', 0) or 0)
    try:
        await c.vpn.update_subscription(uuid, traffic_bytes=current + amount * GB)
    except VpnPanelError:
        await c.users.credit(call.from_user.id, price, 'Возврат за трафик ByPass')
        await call.answer('Панель не ответила, деньги возвращены.', show_alert=True)
        return

    await c.users.col.update_one(
        {'user_data.user_id': call.from_user.id},
        {'$inc': {'vpn.bypass_trafficLimitBytes': amount * GB},
         '$push': {'info.bypass_stats.purchases': {'amount_gb': amount, 'price': price}}})

    await call.answer(f'Начислено {amount} Гб ✅')
    await bypass(call, c, await c.users.get(call.from_user.id), settings)


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='bypass')
    router.callback_query.register(bypass, Menu.filter(F.screen == 'bypass'), Feature('features.bypass_enabled'))
    router.callback_query.register(create, Menu.filter(F.screen == 'bypass_create'), Feature('features.bypass_enabled'))
    router.callback_query.register(buy_traffic, Menu.filter(F.screen == 'bypass_buy'), Feature('features.bypass_enabled'))
    return router
