"""Личный сервер: витрина, покупка, участники, статистика.

Кому виден раздел — настройка `private.visibility`: всем, только админам
(так его обкатывали) или никому. Кнопки в профиле у того, кому раздел не
виден, нет совсем: показывать её и отвечать «недоступно» хуже, чем не
показывать.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu, Server
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.bot.screens.profile import profile_caption
from app.content.emoji import e
from app.core.time import fmt, now
from app.domain import private_servers as ps

INVITES_ENABLED = 'info.server_invites_enabled'

ERRORS = {
    'disabled': 'Личные серверы временно недоступны.',
    'already_has': 'У вас уже есть сервер.',
    'no_funds': 'На балансе не хватает {amount}₽.',
    'unknown_plan': 'Такого тарифа нет.',
    'unknown_location': 'Такой локации нет.',
    'limited_location': 'На долевой машине доступны только безлимитные '
                        'площадки. Выберите другую.',
    'unknown_profile': 'Такого профиля нет.',
    'not_owner': 'Это не ваш сервер.',
    'not_active': 'Сервер ещё не запущен.',
    'no_slots': 'Свободных мест нет.',
    'bad_code': 'Ссылка недействительна или уже использована.',
    'own_server': 'Это ваш собственный сервер.',
    'already_member': 'Вы уже на этом сервере.',
    'panel': 'Не удалось выдать доступ. Мы уже разбираемся.',
    'not_member': 'Участник не найден.',
}


class ServerTitle(StatesGroup):
    value = State()


async def visible_for(user_id: int, c, settings) -> bool:
    """Кому показывать раздел. На время тестов — только админам."""
    mode = str(await settings.get('private.visibility') or 'all')
    if mode == 'off':
        return False
    if mode == 'admins':
        return user_id in (c.config.admin_ids or ())
    return True


def _btn(text: str, action: str, value: str = '') -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=text, callback_data=Server(action=action, value=value).pack())


# ── витрина ─────────────────────────────────────────────────────────────────
async def shop(event, c, user: dict, settings, note: str = '') -> None:
    # Тарифов четыре, и раньше каждый занимал две плотные строки, из которых
    # три четверти повторяли друг друга («сервер целиком ваш» трижды подряд).
    # Читается это как сплошной текст, а выбирают тут по двум числам: цена и
    # сколько человек. Их и выносим, остальное — общей подписью снизу.
    kb = InlineKeyboardBuilder()
    lines = []
    for plan, price in await c.private.plans():
        row = (f'<b>{plan.title}</b> — <code>{price}₽</code> в месяц\n'
               f'   до {plan.slots} человек, по {price // max(1, plan.slots)}₽ '
               f'с каждого')
        if plan.shared:
            shares = await c.private.shares_limit(plan)
            row += (f'\n   <i>машина общая: кроме вас на ней '
                    f'{ps.others_on_machine(shares)}</i>')
        lines.append(row)
        kb.row(_btn(f'{plan.title} — {price}₽', 'buy', plan.code))

    text = (profile_caption(user, f'{e("private")} Свой сервер')
            + '\n\n'.join(lines) + '\n\n'
            + f'<blockquote>{note or await settings.get("private.note")}</blockquote>')

    await footer(kb, settings, back='profile')
    await render(event, Screen(text=text, markup=kb.as_markup(), image=c.media('profile')))
    if isinstance(event, types.CallbackQuery):
        await event.answer()


async def entry(call: types.CallbackQuery, c, user: dict, settings) -> None:
    """Точка входа: свой сервер, чужой или витрина — что уместно."""
    if not await visible_for(call.from_user.id, c, settings):
        await call.answer('Раздел недоступен', show_alert=True)
        return

    own = await c.private.servers.of_owner(call.from_user.id)
    if own:
        await server_screen(call, c, user, settings, own)
        return

    joined = await c.private.servers.of_member(call.from_user.id)
    if joined:
        await server_screen(call, c, user, settings, joined[0])
        return

    await shop(call, c, user, settings)


# ── покупка: тариф → локация → профиль → подтверждение ──────────────────────
#
# Три шага, а не один экран с двумя списками: локаций одиннадцать, профилей
# три, и всё вместе не помещается ни в клавиатуру, ни в голову. Выбранное
# едет в callback_data — состояние в FSM здесь только мешало бы, потому что
# «назад» посреди покупки должен возвращать к предыдущему шагу, а не терять всё.

# Разделитель — дефис, а не двоеточие: двоеточие в aiogram отделяет поля
# callback_data, и pack() на нём падает. Коды тарифов, локаций и протоколов
# дефисов не содержат, id серверов — тоже.
PICK = '-'


def _pick(*parts: str) -> str:
    return PICK.join(parts)


async def choose_location(call: types.CallbackQuery, callback_data: Server, c,
                          user: dict, settings) -> None:
    plan = ps.BY_CODE.get(callback_data.value)
    if not plan:
        await call.answer(ERRORS['unknown_plan'], show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    # Без значка: в остальном боте он означает «выбрано», и здесь читался
    # как уже сделанный выбор. Отличие и так в подписи.
    for location in ps.locations_for(plan):
        kb.row(_btn(f'{location.title} — {location.traffic_title}', 'loc',
                    _pick(plan.code, location.code)))
    kb.row(_btn(f'{e("back")} К тарифам', 'shop'))

    hint = ('Все площадки под этот тариф — без ограничения по трафику: на '
            'машине живут три покупателя со своими людьми, и терабайт на всех '
            'кончился бы за месяц.' if plan.shared else
            'Площадки с пометкой «1 ТБ» ограничены по трафику на весь сервер '
            'в месяц — на несколько человек этого хватает с запасом. '
            'Отмеченные «безлимит» работают без ограничения по трафику.')

    text = (profile_caption(user, f'{e("pin")} Где поднять сервер')
            + f'<b>Тариф:</b> <code>{plan.title}</code>, мест {plan.slots}'
            + (f' (машина общая: на ней '
               f'{ps.others_on_machine(await c.private.shares_limit(plan))})'
               if plan.shared else '') + '\n\n'
            + f'<blockquote>{hint}\n\nБлиже к вам — быстрее отклик: для России '
              f'это европейские точки.</blockquote>')

    await footer(kb, settings, back=None)
    await render(call, Screen(text=text, markup=kb.as_markup(), image=c.media('profile')))
    await call.answer()


async def choose_profile(call: types.CallbackQuery, callback_data: Server, c,
                         user: dict, settings) -> None:
    plan_code, _, location_code = callback_data.value.partition(PICK)
    plan, location = ps.BY_CODE.get(plan_code), ps.BY_LOCATION.get(location_code)
    if not plan or not location:
        await call.answer(ERRORS['unknown_location'], show_alert=True)
        return
    if not ps.allowed_location(plan, location):
        await call.answer(ERRORS['limited_location'], show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    lines = []
    for profile in ps.PROFILES:
        # Про роутер — последней фразой описания, и у Hysteria2 тоже:
        # молчание про него читается как «наверное, тоже можно», а протокол
        # на работающем сервере уже не поменять.
        # Значка нет нарочно: во всём остальном боте галочка означает
        # «выбрано», и здесь она читалась бы как сделанный выбор.
        lines.append(f'<b>{profile.title}</b> — {profile.hint} '
                     f'<u>{ps.router_hint(profile)}</u>')
        kb.row(_btn(profile.title, 'prof',
                    _pick(plan.code, location.code, profile.code)))
    kb.row(_btn(f'{e("back")} К локациям', 'buy', plan.code))

    text = (profile_caption(user, f'{e("tools")} Протокол сервера')
            + '\n\n'.join(lines) + '\n\n'
            + '<blockquote>От протокола зависит скорость и то, как сервер '
              'переживает блокировки. Не знаете, что выбрать, — берите '
              f'{ps.BY_PROFILE[ps.DEFAULT_PROFILE].title}: он подходит '
              'большинству и ставится на роутер.\n\nПоменять протокол на '
              'работающем сервере можно только через поддержку.</blockquote>')

    await footer(kb, settings, back=None)
    await render(call, Screen(text=text, markup=kb.as_markup(), image=c.media('profile')))
    await call.answer()


async def buy_confirm(call: types.CallbackQuery, callback_data: Server, c, user: dict,
                      settings) -> None:
    parts = callback_data.value.split(PICK)
    plan = ps.BY_CODE.get(parts[0] if parts else '')
    location = ps.BY_LOCATION.get(parts[1] if len(parts) > 1 else '')
    profile = ps.BY_PROFILE.get(parts[2] if len(parts) > 2 else '')
    if not plan or not location or not profile:
        await call.answer(ERRORS['unknown_plan'], show_alert=True)
        return
    if not ps.allowed_location(plan, location):
        await call.answer(ERRORS['limited_location'], show_alert=True)
        return

    price = await c.private.price(plan)
    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("ok")} Оплатить {price}₽', 'order', callback_data.value))
    kb.row(_btn(f'{e("back")} Назад', 'loc', _pick(plan.code, location.code)))

    text = (profile_caption(user, f'{e("private")} {plan.title}')
            + f'<b>{e("pin")} Локация:</b> <code>{location.title}</code>\n'
            + f'<b>{e("traffic")} Трафик:</b> <code>{location.traffic_title}</code>\n'
            + f'<b>{e("tools")} Протокол:</b> <code>{profile.title}</code>\n'
            # Последний экран перед списанием: если человек брал сервер ради
            # роутера, здесь он ещё может вернуться и поменять протокол.
            + f'   <u>{ps.router_hint(profile)}</u>\n'
            + f'<b>{e("devices")} Мест:</b> <code>{plan.slots}</code> '
              f'(вы и ещё {plan.guests})\n'
            + f'<b>{e("money")} Списание:</b> <code>{price}₽</code> сейчас '
              'и столько же каждый месяц\n\n'
            + (f'<blockquote>Эту же машину покупаете не только вы: кроме вас '
               f'на ней {ps.others_on_machine(await c.private.shares_limit(plan))}, '
               f'и у каждого свои {plan.slots} мест. Всё остальное раздельно — '
               f'свои приглашения, своя статистика, своё продление. Ни вы их, '
               f'ни они вас не видят. Общая только скорость канала, поэтому и '
               f'цена втрое ниже.</blockquote>\n\n'
               if plan.shared else '')
            + '<blockquote>Оплата помесячная: списываем сейчас за первый месяц '
              'и дальше столько же каждые 30 дней. Отказаться от следующего '
              'списания можно в любой момент — сервер доработает оплаченное.\n\n'
              'Поднимается он вручную и обычно готов в течение нескольких часов. '
              'Если запустить не выйдет, вернём деньги полностью.</blockquote>')

    await footer(kb, settings, back=None)
    await render(call, Screen(text=text, markup=kb.as_markup(), image=c.media('profile')))
    await call.answer()


async def order(call: types.CallbackQuery, callback_data: Server, c, user: dict,
                settings) -> None:
    parts = callback_data.value.split(PICK)
    result = await c.private.request(
        call.from_user.id, parts[0],
        location=parts[1] if len(parts) > 1 else '',
        profile=parts[2] if len(parts) > 2 else ps.DEFAULT_PROFILE)
    if not result.ok:
        await call.answer(ERRORS.get(result.reason, 'Не получилось').format(
            amount=result.amount), show_alert=True)
        return

    if c.notifier:
        from app.admin.private_servers import request_card, request_markup

        await c.notifier.send('servers', await request_card(c, result.server),
                              markup=request_markup(result.server['_id']))

    await call.answer('Заявка принята', show_alert=True)
    await server_screen(call, c, await c.users.get(call.from_user.id), settings,
                        result.server)


# ── экран сервера ───────────────────────────────────────────────────────────
async def server_screen(event, c, user: dict, settings, server: dict,
                        note: str = '', extra=None) -> None:
    owner = server.get('owner_id') == event.from_user.id
    plan = ps.plan_of(server)
    paid_until = server.get('paid_until')

    lines = [f'<b>{e("note")} Название:</b> <code>{server.get("title")}</code>',
             f'<b>{e("stats")} Статус:</b> {ps.STATUS_TITLES.get(server.get("status"), "—")}']
    location = ps.location_of(server)
    if location:
        lines.append(f'<b>{e("pin")} Локация:</b> <code>{location.title}</code>')
        lines.append(f'<b>{e("traffic")} Трафик:</b> <code>{location.traffic_title}</code>')
    if ps.profile_of(server):
        lines.append(f'<b>{e("tools")} Протокол:</b> '
                     f'<code>{ps.profile_title(server)}</code>')
    lines.append(f'<b>{e("devices")} Мест:</b> '
                 f'<code>{ps.occupied(server)} из {server.get("slots")}</code>')
    if paid_until:
        lines.append(f'<b>{e("calendar")} Оплачен до:</b> <code>{fmt(paid_until)}</code>')
    if owner:
        renews = server.get('autorenew', True)
        lines.append(f'<b>{e("money")} Списание:</b> <code>{server.get("price")}₽</code> '
                     + ('в месяц, следующее '
                        f'{fmt(server.get("next_charge_at"))}' if renews else
                        'в месяц — <b>продление выключено</b>'))

    kb = InlineKeyboardBuilder()
    if server.get('status') == ps.ACTIVE:
        kb.row(_btn(f'{e("shield")} Подключиться', 'link', server['_id']))
        if owner:
            if ps.free_slots(server) > 0:
                kb.row(_btn(f'{e("plus")} Пригласить друга', 'invite', server['_id']))
            kb.row(_btn(f'{e("stats")} Статистика', 'stats', server['_id']))
            if server.get('members'):
                kb.row(_btn(f'{e("friends")} Участники', 'members', server['_id']))
            kb.row(_btn(f'{e("renew")} Включить продление' if not server.get('autorenew', True)
                        else f'{e("cross")} Не продлевать', 'renew', server['_id']))
        else:
            kb.row(_btn(f'{e("cross")} Выйти с сервера', 'leave', server['_id']))

        # Роутер — только там, где протокол это позволяет: Hysteria2
        # прошивки почти не умеют, и кнопка вела бы в тупик.
        if ps.router_ready(server):
            kb.row(_btn(f'{e("tools")} Настроить на роутер', 'router', server['_id']))

    hint = ('Сервер готовится. Как только он будет поднят, придёт сообщение.'
            if server.get('status') == ps.REQUESTED else
            'Продление выключено: сервер доработает оплаченный месяц и '
            'закроется. Передумаете — включите обратно.'
            if owner and not server.get('autorenew', True) else
            f'Ваша доля на машине: {server.get("slots")} мест, приглашения и '
            f'статистика только ваши. Эту же машину купили '
            f'{ps.others_on_machine(ps.shares_of(server))} — их и их гостей вы '
            f'не видите, они вас тоже.' if owner and ps.is_shared(server) else
            'Приглашайте друзей — каждый получит доступ к этому серверу '
            'и только к нему.' if owner else
            'Вы пользуетесь сервером друга. Оплачивает его владелец.')

    # Заметка — обычным блоком, а не цитатой: внутри цитаты Telegram не даёт
    # скопировать <code> нажатием, а ссылку-приглашение показывают ровно
    # ради этого.
    text = (profile_caption(user, f'{e("private")} Свой сервер')
            + '\n'.join(lines)
            + (f'\n\n{note}' if note else '')
            + f'\n\n<blockquote>{hint}</blockquote>')

    if extra is not None:
        kb.row(extra)
    await footer(kb, settings, back='profile')
    await render(event, Screen(text=text, markup=kb.as_markup(), image=c.media('profile')))
    if isinstance(event, types.CallbackQuery):
        await event.answer()


async def open_server(call: types.CallbackQuery, callback_data: Server, c, user: dict,
                      settings) -> None:
    server = await c.private.servers.get(callback_data.value)
    if not server or not ps.is_member(server, call.from_user.id):
        await call.answer(ERRORS['not_owner'], show_alert=True)
        return
    await server_screen(call, c, user, settings, server)


# ── приглашения ─────────────────────────────────────────────────────────────
async def invite(call: types.CallbackQuery, callback_data: Server, c, user: dict,
                 settings) -> None:
    result = await c.private.invite(callback_data.value, call.from_user.id)
    if not result.ok:
        await call.answer(ERRORS.get(result.reason, 'Не получилось'), show_alert=True)
        return

    me = await call.bot.get_me()
    invite_link = f'https://t.me/{me.username}?start=srv_{result.reason}'
    server = await c.private.servers.get(callback_data.value)

    # Тем же экраном, а не новым сообщением: иначе на каждое нажатие чат
    # прирастает карточкой, а закреплённый экран уезжает вверх.
    share = types.InlineKeyboardButton(
        text=f'{e("link")} Отправить другу',
        url=f'https://t.me/share/url?url={invite_link}&text='
            f'Заходи на мой сервер RS VPN')
    await server_screen(
        call, c, user, settings, server, extra=share,
        note=(f'{e("link")} <b>Ссылка-приглашение</b> — нажмите, чтобы скопировать:\n'
              f'<code>{invite_link}</code>\n\n'
              f'Одноразовая: сработает у одного человека. Свободных мест '
              f'после него — {max(0, ps.free_slots(server) - 1)}. '
              f'Принять её друг должен сам — добавить без его ведома нельзя.'))


async def accept_screen(message: types.Message, code: str, c, user: dict, settings) -> None:
    """Экран по ссылке-приглашению. Вызывается из /start."""
    server = await c.private.servers.by_invite(code)
    if not server:
        await message.answer(f'{e("cross")} {ERRORS["bad_code"]}')
        return

    owner = await c.users.get(server['owner_id'], {'user_data': 1})
    who = c.users.pick(owner or {}, 'user_data.first_name') or 'Владелец'

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("ok")} Принять приглашение', 'accept', code))
    kb.row(_btn(f'{e("cross")} Отказаться', 'decline', code))

    await message.answer(
        f'{e("private")} <b>Приглашение на личный сервер</b>\n\n'
        f'<b>{who}</b> зовёт вас на сервер «{server.get("title")}».\n'
        f'Свободных мест: <b>{ps.free_slots(server)}</b>.\n\n'
        f'<blockquote>Это отдельный сервер, которым пользуется только его '
        f'владелец и приглашённые. Доступ действует, пока владелец платит '
        f'за сервер, — своя подписка для этого не нужна.\n\n'
        f'Приняв приглашение, вы разрешаете присылать вам такие приглашения. '
        f'Отключить это можно в профиле.</blockquote>',
        reply_markup=kb.as_markup())


async def accept(call: types.CallbackQuery, callback_data: Server, c, user: dict,
                 settings) -> None:
    # Согласие — сам факт нажатия. Отдельный тумблер в профиле нужен, чтобы
    # отказаться от таких приглашений навсегда, а не чтобы включать их
    # заранее: иначе первая же ссылка упирается в «сходите в настройки».
    await c.users.col.update_one({'user_data.user_id': call.from_user.id},
                                 {'$set': {INVITES_ENABLED: True}})

    result = await c.private.join(callback_data.value, call.from_user.id)
    if not result.ok:
        await call.answer(ERRORS.get(result.reason, 'Не получилось'), show_alert=True)
        return

    await call.answer('Вы на сервере', show_alert=True)
    await server_screen(call, c, await c.users.get(call.from_user.id), settings,
                        result.server, note='Доступ выдан. Нажмите «Подключиться».')


async def decline(call: types.CallbackQuery, c, settings) -> None:
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer('Приглашение отклонено')


# ── участники и статистика ──────────────────────────────────────────────────
async def members(call: types.CallbackQuery, callback_data: Server, c, user: dict,
                  settings) -> None:
    server = await c.private.servers.get(callback_data.value)
    if not server or server.get('owner_id') != call.from_user.id:
        await call.answer(ERRORS['not_owner'], show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    lines = []
    for entry_ in server.get('members') or []:
        member = await c.users.get(entry_['user_id'], {'user_data': 1})
        name = c.users.pick(member or {}, 'user_data.first_name') or entry_['user_id']
        lines.append(f'• {name} — с {fmt(entry_.get("joined_at"))}')
        kb.row(_btn(f'{e("minus")} Убрать {name}', 'kick',
                    _pick(server['_id'], str(entry_['user_id']))))

    kb.row(_btn(f'{e("back")} К серверу', 'open', server['_id']))
    text = (profile_caption(user, f'{e("friends")} Участники')
            + ('\n'.join(lines) or 'Пока никого нет.') + '\n\n'
            + '<blockquote>Убрать участника — значит сразу закрыть ему доступ '
              'к серверу. Место освободится.</blockquote>')

    await footer(kb, settings, back=None)
    await render(call, Screen(text=text, markup=kb.as_markup(), image=c.media('profile')))
    await call.answer()


async def kick(call: types.CallbackQuery, callback_data: Server, c, user: dict,
               settings) -> None:
    server_id, _, raw = callback_data.value.partition(PICK)
    result = await c.private.kick(server_id, call.from_user.id, int(raw or 0))
    if not result.ok:
        await call.answer(ERRORS.get(result.reason, 'Не получилось'), show_alert=True)
        return
    await call.answer('Участник убран')
    await members(call, Server(action='members', value=server_id), c, user, settings)


async def leave(call: types.CallbackQuery, callback_data: Server, c, user: dict,
                settings) -> None:
    result = await c.private.leave(callback_data.value, call.from_user.id)
    if not result.ok:
        await call.answer(ERRORS.get(result.reason, 'Не получилось'), show_alert=True)
        return
    await call.answer('Вы вышли с сервера', show_alert=True)
    await shop(call, c, await c.users.get(call.from_user.id), settings,
               note='Вы больше не на сервере друга.')


async def toggle_renew(call: types.CallbackQuery, callback_data: Server, c, user: dict,
                       settings) -> None:
    """Отказ от следующего списания. Оплаченный месяц остаётся за человеком."""
    server = await c.private.servers.get(callback_data.value)
    if not server:
        await call.answer(ERRORS['not_owner'], show_alert=True)
        return

    result = await c.private.set_autorenew(server['_id'], call.from_user.id,
                                           not server.get('autorenew', True))
    if not result.ok:
        await call.answer(ERRORS.get(result.reason, 'Не получилось'), show_alert=True)
        return

    on = result.server.get('autorenew', True)
    await call.answer('Продление включено' if on else
                      f'Больше не спишем. Сервер работает до '
                      f'{fmt(result.server.get("paid_until"))}', show_alert=True)
    await server_screen(call, c, user, settings, result.server)


async def stats(call: types.CallbackQuery, callback_data: Server, c, user: dict,
                settings) -> None:
    server = await c.private.servers.get(callback_data.value)
    if not server or server.get('owner_id') != call.from_user.id:
        await call.answer(ERRORS['not_owner'], show_alert=True)
        return

    await call.answer('Спрашиваю панель…')
    rows = await c.private.stats(server)

    lines = []
    for row in rows:
        mark = e('user') if row['owner'] else e('friends')
        # «не заходил» — утверждение, а панель этой версии времени
        # подключения не отдаёт вовсе. Врать в обе стороны одинаково плохо.
        online = (fmt(row['online_at']) if row['online_at'] else
                  'не заходил' if row.get('online_known') else 'нет данных')
        detail = (f'{e("warning")} {row["error"]}' if row.get('error') else
                  f'трафик: <code>{ps.traffic(row["traffic"])}</code>, '
                  f'последний вход: {online}')
        lines.append(f'{mark} <b>{row["name"]}</b>\n   {detail}')

    location = ps.location_of(server)
    used = sum(row['traffic'] for row in rows)
    if location and location.limited:
        percent = round(ps.gb(used) / location.traffic_gb * 100, 1)
        # На долевой машине квота одна на всех, а цифра выше — только по
        # своим. Выдавать её за расход всей машины нельзя.
        quota = (f' из <code>{location.traffic_title}</code> общих на машину '
                 f'(ваши {percent}%)' if ps.is_shared(server) else
                 f' из <code>{location.traffic_title}</code> ({percent}%)')
    elif location:
        quota = ' (без ограничения)'
    else:
        # Сервер заведён до того, как появился выбор площадки: квоты у него
        # в базе нет. Врать «без ограничения» нельзя — она может быть.
        quota = ' (квота площадки не указана)'
    lines.append(f'\n<b>{e("traffic")} Всего за 30 дней:</b> '
                 f'<code>{ps.traffic(used)}</code>{quota}')

    # Панель не ответила — на экране это выглядит как «трафика нет», хотя
    # он есть. Нули и молчание должны различаться.
    if any(row.get('error') for row in rows):
        lines.append(f'\n{e("warning")} Цифры неполные — см. пометки выше.')
    elif any(row.get('source') == 'user' for row in rows):
        # Общий трафик человека больше расхода этого сервера: в нём и
        # обычные серверы RS VPN. Молча выдавать одно за другое нельзя.
        lines.append(f'\n{e("note")} Панель не дала разбивку по этому серверу — '
                     f'показан весь трафик участников, включая другие серверы.')

    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("refresh")} Обновить', 'stats', server['_id']))
    kb.row(_btn(f'{e("back")} К серверу', 'open', server['_id']))

    # Время в тексте — чтобы «Обновить» было видно: без него экран с теми же
    # цифрами не меняется, и понять, обновился он или завис, нельзя.
    text = (profile_caption(user, f'{e("stats")} Статистика сервера')
            + '\n'.join(lines) + '\n\n'
            + '<blockquote>Трафик считает панель нарастающим итогом с момента '
              'последнего сброса. Это ваш сервер — видеть, кто его расходует, '
              f'нормально.\n\nОбновлено в {now().strftime("%H:%M:%S")}</blockquote>')

    await footer(kb, settings, back=None)
    await render(call, Screen(text=text, markup=kb.as_markup(), image=c.media('profile')))


async def link(call: types.CallbackQuery, callback_data: Server, c, user: dict,
               settings) -> None:
    """Подключение — та же ссылка подписки, сервер в ней уже появился."""
    server = await c.private.servers.get(callback_data.value)
    if not server or not ps.is_member(server, call.from_user.id):
        await call.answer(ERRORS['not_owner'], show_alert=True)
        return
    await server_screen(
        call, c, user, settings, server,
        note=(f'{e("shield")} Сервер уже в вашей подписке — отдельная ссылка '
              f'не нужна. Откройте «Ваша подписка» и обновите конфиг в '
              f'приложении: сервер появится в списке.'))


async def router_setup(call: types.CallbackQuery, callback_data: Server, c, user: dict,
                       settings) -> None:
    """Ссылка для роутера. Подписка роутеру не годится — нужна прямая ссылка."""
    server = await c.private.servers.get(callback_data.value)
    if not server or not ps.is_member(server, call.from_user.id):
        await call.answer(ERRORS['not_owner'], show_alert=True)
        return

    await call.answer('Спрашиваю панель…')
    links, note = await c.private.router_links(server, call.from_user.id)
    if not links:
        await server_screen(call, c, user, settings, server,
                            note=f'{e("warning")} Ссылку для роутера получить не '
                                 f'вышло: {note}. Напишите в поддержку.')
        return

    # Ссылка — обычным блоком и в <code>: из цитаты роутерную строку не
    # скопировать, а вручную такое не перенабирают. Показываем одну: в
    # подписке рядом лежат общие серверы и строки-подсказки, и выбор из
    # пяти похожих ссылок — это способ подключиться не туда.
    shown = '\n\n'.join(f'<code>{link}</code>' for link in links[:2])
    await server_screen(
        call, c, user, settings, server,
        note=(f'{e("tools")} <b>Ссылка для роутера</b>\n\n'
              f'{shown}\n\n'
              f'Нажмите на ссылку, чтобы скопировать. В прошивке роутера '
              f'(Keenetic, OpenWrt с xray, Padavan) добавьте её как VLESS-'
              f'подключение — поля разберутся сами.\n\n'
              f'Через роутер работает вся домашняя сеть.'))


def create_router() -> Router:
    router = Router(name='private_servers')

    router.callback_query.register(entry, Menu.filter(F.screen == 'private'))
    router.callback_query.register(shop, Server.filter(F.action == 'shop'))
    router.callback_query.register(choose_location, Server.filter(F.action == 'buy'))
    router.callback_query.register(choose_profile, Server.filter(F.action == 'loc'))
    router.callback_query.register(buy_confirm, Server.filter(F.action == 'prof'))
    router.callback_query.register(order, Server.filter(F.action == 'order'))
    router.callback_query.register(open_server, Server.filter(F.action == 'open'))
    router.callback_query.register(invite, Server.filter(F.action == 'invite'))
    router.callback_query.register(accept, Server.filter(F.action == 'accept'))
    router.callback_query.register(decline, Server.filter(F.action == 'decline'))
    router.callback_query.register(members, Server.filter(F.action == 'members'))
    router.callback_query.register(kick, Server.filter(F.action == 'kick'))
    router.callback_query.register(leave, Server.filter(F.action == 'leave'))
    router.callback_query.register(toggle_renew, Server.filter(F.action == 'renew'))
    router.callback_query.register(stats, Server.filter(F.action == 'stats'))
    router.callback_query.register(link, Server.filter(F.action == 'link'))
    router.callback_query.register(router_setup, Server.filter(F.action == 'router'))
    return router
