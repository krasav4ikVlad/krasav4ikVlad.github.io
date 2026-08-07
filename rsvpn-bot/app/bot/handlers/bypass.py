"""ByPass: отдельный конфиг для обхода белых списков.

Экран собран как в старом боте: шапка профиля, дата окончания и остаток
гигабайт, покупка трафика пакетами и выбор приложения (Happ / INCY) —
ссылка приходит отдельным сообщением, чтобы её было удобно скопировать.

Шифрование ссылки живёт в integrations/vpn/links.py: здесь только «показать».
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.filters.feature import Feature
from app.bot.handlers.common import close_button
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.bot.screens.profile import profile_caption
from app.core.errors import VpnPanelError
from app.core.time import fmt, parse_dt
from app.integrations.vpn.links import LinkEncryptionError
from app.content.emoji import e

GB = 1024 ** 3


async def traffic_packages(settings) -> tuple[tuple[int, int], ...]:
    """Пакеты «объём: цена» из настройки bypass.packages.

    Цена не линейна (5 Гб — 50₽, 100 Гб — 500₽), поэтому пакеты задаются
    списком, а не умножением на цену гигабайта. Настройка пустая — считаем
    по bypass.price_per_gb, чтобы экран не остался без кнопок.
    """
    raw = (await settings.get('bypass.packages') or '').strip()
    packages: list[tuple[int, int]] = []
    for chunk in raw.split(','):
        volume, _, price = chunk.strip().partition(':')
        if volume.strip().isdigit() and price.strip().isdigit():
            packages.append((int(volume), int(price)))

    if packages:
        return tuple(packages)

    per_gb = await settings.int('bypass.price_per_gb')
    return tuple((volume, volume * per_gb) for volume in (5, 15, 30, 100))


def _bypass_block(user: dict) -> str:
    vpn = user.get('vpn') or {}
    left = round(int(vpn.get('bypass_trafficLimitBytes') or 0) / GB, 1)
    return (f'<b>{e("calendar")} Дата окончания:</b> <code>{fmt(vpn.get("bypass_expireAt"))}</code>\n'
            f'<b>{e("traffic")} Лимит гигабайт:</b> <code>{left}</code>\n\n')


async def bypass(event, c, user: dict, settings):
    vpn = user.get('vpn') or {}
    kb = InlineKeyboardBuilder()

    if not vpn.get('bypass_uuid'):
        text = (profile_caption(user, f'{e("shield")} ByPass подписка')
                + f'<blockquote>{e("shield")} ByPass — дополнительная подписка поверх обычной для '
                  'обхода белых списков. Подключение бесплатное, оплачивается только '
                  'трафик.\n\n'
                  f'{e("warning")} ByPass является тестовым конфигом и не гарантирует полную работу '
                  'из-за внешних факторов.</blockquote>')
        kb.row(types.InlineKeyboardButton(
            text=f'{e("plus")} Подключить ByPass', callback_data=Menu(screen='bypass_create').pack()))
    else:
        text = (profile_caption(user, f'{e("shield")} Ваша ByPass подписка')
                + _bypass_block(user)
                + f'<blockquote>{e("link")} Выберите приложение — пришлём ссылку для подключения.\n\n'
                  f'{e("warning")} ByPass это дополнительная подписка поверх обычной для обхода белых '
                  'списков. Оплачивается за гигабайты.\n'
                  'ByPass является тестовым конфигом, и не гарантирует полную работу '
                  'из-за внешних факторов.</blockquote>')
        kb.row(
            types.InlineKeyboardButton(
                text=f'{e("devices")} Happ', callback_data=Menu(screen='bypass_app', arg='happ').pack()),
            types.InlineKeyboardButton(
                text=f'{e("devices")} INCY', callback_data=Menu(screen='bypass_app', arg='incy').pack()),
        )
        kb.row(types.InlineKeyboardButton(
            text=f'{e("traffic")} Купить гигабайты', callback_data=Menu(screen='bypass_traffic').pack()))

    await footer(kb, settings, back='my_subscription')
    await render(event, Screen(text=text, markup=kb.as_markup(), image=c.media('bypass')))
    if isinstance(event, types.CallbackQuery):
        await event.answer()


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
    await call.answer(f'ByPass подключён {e("ok")}')
    await bypass(call, c, await c.users.get(call.from_user.id), settings)


# ── ссылка под выбранное приложение ─────────────────────────────────────────
async def send_link(call: types.CallbackQuery, callback_data: Menu, c, user: dict, settings):
    short = c.users.pick(user, 'vpn.bypass_shortUuid')
    if not short:
        await call.answer('Сначала подключите ByPass', show_alert=True)
        return

    app = callback_data.arg if callback_data.arg in ('happ', 'incy') else 'happ'
    app_name = 'INCY' if app == 'incy' else 'Happ'
    cache_field = 'bypass_connectUrl_incy' if app == 'incy' else 'bypass_connectUrl'

    link = c.users.pick(user, f'vpn.{cache_field}')
    if not link:
        raw_url = f'{await settings.get("link.connect_base")}{short}'
        try:
            link = await c.links.for_app(app, raw_url)
        except LinkEncryptionError as exc:
            await call.answer(exc.user_message, show_alert=True)
            return
        # ссылка детерминированная и не меняется — считаем один раз
        await c.users.set_vpn(call.from_user.id,
                              {cache_field: link, 'preferred_client': app})
    else:
        await c.users.set_vpn(call.from_user.id, {'preferred_client': app})

    kb = InlineKeyboardBuilder()
    kb.row(close_button())
    await call.message.answer(
        f'{e("link")} <b>Ваша ссылка для подключения ByPass ({app_name}):</b>\n\n'
        f'<code>{link}</code>\n\n'
        f'<blockquote>{e("clipboard")} Скопируйте ссылку выше и добавьте её в <b>{app_name}</b>:\n\n'
        f'1️⃣ Откройте приложение <b>{app_name}</b>\n'
        f'2️⃣ Нажмите <b>+</b> или <b>Добавить подписку</b>\n'
        f'3️⃣ Вставьте скопированную ссылку\n'
        f'4️⃣ Нажмите <b>Подключить</b></blockquote>',
        reply_markup=kb.as_markup())
    await call.answer()


# ── трафик ──────────────────────────────────────────────────────────────────
async def traffic_menu(call: types.CallbackQuery, c, user: dict, settings):
    if not c.users.pick(user, 'vpn.bypass_uuid'):
        await call.answer('Сначала подключите ByPass', show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for volume, price in await traffic_packages(settings):
        kb.row(types.InlineKeyboardButton(
            text=f'{volume} Гб — {price}₽',
            callback_data=Menu(screen='bypass_buy', arg=str(volume)).pack()))
    await footer(kb, settings, back='bypass')

    text = (profile_caption(user, f'{e("traffic")} Покупка гигабайт')
            + _bypass_block(user)
            + f'<blockquote>{e("traffic")} Выберите нужное количество гигабайт.\n'
              'Гигабайты не восстанавливаются и не сгорают.</blockquote>')
    await render(call, Screen(text=text, markup=kb.as_markup(),
                              image=c.media('bypass_buying')))
    await call.answer()


async def buy_traffic(call: types.CallbackQuery, callback_data: Menu, c, user: dict, settings):
    prices = dict(await traffic_packages(settings))
    try:
        amount = int(callback_data.arg)
        price = prices[amount]
    except (TypeError, ValueError, KeyError):
        await call.answer('Этот пакет больше недоступен', show_alert=True)
        return

    uuid = c.users.pick(user, 'vpn.bypass_uuid')
    if not uuid:
        await call.answer('Сначала подключите ByPass', show_alert=True)
        return

    # деньги списываются до обращения к панели: если панель не ответит, есть
    # что возвращать. Обратный порядок оставил бы выданный трафик без оплаты
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

    await call.answer(f'Начислено {amount} Гб {e("ok")}')
    await bypass(call, c, await c.users.get(call.from_user.id), settings)


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='bypass')
    feature = Feature('features.bypass_enabled')

    router.callback_query.register(bypass, Menu.filter(F.screen == 'bypass'), feature)
    router.callback_query.register(create, Menu.filter(F.screen == 'bypass_create'), feature)
    router.callback_query.register(send_link, Menu.filter(F.screen == 'bypass_app'), feature)
    router.callback_query.register(traffic_menu, Menu.filter(F.screen == 'bypass_traffic'), feature)
    router.callback_query.register(buy_traffic, Menu.filter(F.screen == 'bypass_buy'), feature)
    return router
