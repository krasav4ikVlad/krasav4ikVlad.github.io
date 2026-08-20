"""Заявки на личные серверы: карточка в админ-чате и выдача.

Провижининг ручной: бот принимает деньги и заводит заявку, VPS поднимает
человек. Поэтому вся автоматика здесь сводится к одному — принять от админа
UUID сквада и связать его с сервером, чтобы дальше доступ выдавался сам.
"""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import ServerAdmin as Adm
from app.content.emoji import e
from app.core.time import fmt
from app.domain import private_servers as ps

log = logging.getLogger(__name__)

UUID_LENGTH = 36


class Provision(StatesGroup):
    squad = State()


def looks_like_uuid(value: str) -> bool:
    """Панель молча проглотит мусор, и сервер будет «выдан», но пустой."""
    return len(value) == UUID_LENGTH and value.count('-') == 4


def _btn(text: str, action: str, server_id: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=text, callback_data=Adm(action=action, server_id=server_id).pack())


def request_markup(server_id: str, from_pool: bool = False) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if from_pool:
        # Первой — потому что это одно нажатие вместо «поднять VPS, завести
        # сквад, прислать UUID».
        kb.row(_btn(f'{e("rocket")} Выдать из запаса', 'pool', server_id))
    kb.row(_btn(f'{e("ok")} Выдать сервер', 'give', server_id))
    kb.row(_btn(f'{e("cross")} Отказать и вернуть деньги', 'reject', server_id))
    return kb.as_markup()


async def card_markup(c, server: dict) -> types.InlineKeyboardMarkup:
    """Кнопки заявки. «Из запаса» появляется, только если запас подходит."""
    ready = bool(await c.private.pick_squad(server))
    return request_markup(server['_id'], from_pool=ready)


async def request_card(c, server: dict, note: str = '') -> str:
    """Карточка заявки. `note` заменяет инструкцию «как выдать».

    Ход работы по заявке — это правки одной карточки, а не переписка с
    ботом в теме: «пришлите UUID», «выдан», «не вышло» приходят на место
    инструкции. Так в теме остаются только сами заявки.
    """
    owner = await c.users.get(server['owner_id'], {'user_data': 1, 'info.balance': 1})
    username = c.users.pick(owner or {}, 'user_data.username')
    plan = ps.plan_of(server)
    location = ps.location_of(server)

    return (f'{e("private")} <b>Заявка на личный сервер</b>\n\n'
            f'{e("user")} '
            + (f'@{username} ' if username else '')
            + f'(<code>{server["owner_id"]}</code>)\n'
            f'{e("document")} Тариф: <b>{plan.title if plan else server.get("plan")}</b>, '
            f'мест {server.get("slots")}\n'
            f'{e("pin")} Локация: <b>{ps.location_title(server)}</b>, '
            f'трафик {location.traffic_title if location else "—"}\n'
            f'{e("tools")} Протокол: <b>{ps.profile_title(server)}</b>\n'
            f'{e("money")} Оплачено: <b>{server.get("price")}₽</b> в месяц\n'
            f'{e("id")} Сервер: <code>{server["_id"]}</code>\n'
            f'{e("calendar")} Создана: {fmt(server.get("created_at"))}\n\n'
            + (f'<blockquote>{note}</blockquote>' if note
               else await _how_to_give(c, server)))


async def edit_card(bot, c, server: dict, note: str, markup=None) -> bool:
    """Переписать карточку заявки на месте. False — карточки не нашли."""
    chat_id = server.get('card_chat_id')
    message_id = server.get('card_message_id')
    if not chat_id or not message_id:
        return False

    from app.content.emoji import plain

    try:
        # админ-чат живёт на обычных значках — как и при отправке карточки
        with plain():
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                        text=await request_card(c, server, note),
                                        reply_markup=markup)
        return True
    except Exception as exc:      # карточку удалили или она слишком старая
        log.info('карточка заявки %s не обновлена: %s', server.get('_id'), exc)
        return False


async def drop_command(message: types.Message) -> None:
    """Убрать команду админа: в теме должны остаться только заявки.

    Право удалять чужие сообщения есть не всегда, поэтому неудача — не
    ошибка: команда просто останется висеть.
    """
    try:
        await message.delete()
    except Exception as exc:
        log.info('команда не удалена: %s', exc)


async def _how_to_give(c, server: dict) -> str:
    """Что делать админу. Для доли — куда её можно подсадить.

    Долевой сервер поднимать заново не надо, если машина уже есть и на ней
    осталось место. Держать в голове, какой сквад чем занят, невозможно,
    поэтому свободные машины бот показывает прямо в заявке.
    """
    squad = await c.private.pick_squad(server)
    if squad:
        return (f'<blockquote>Готовая машина есть: '
                f'<code>{squad}</code>. Нажмите «Выдать из запаса» — '
                f'поднимать ничего не нужно.</blockquote>')

    plan = ps.plan_of(server)
    if not plan or not plan.shared:
        return ('<blockquote>Поднимите VPS в указанной локации с указанным '
                'протоколом, заведите под него внутренний сквад в панели и '
                'нажмите «Выдать сервер» — бот попросит UUID сквада.\n\n'
                'Чтобы такие заявки уходили сразу, держите машины в запасе: '
                '<code>/pooladd UUID локация протокол</code>.</blockquote>')

    free = await c.private.free_squads(server.get('location') or '',
                                       server.get('profile') or '')
    if not free:
        limit = await c.private.shares_limit(plan)
        return (f'<blockquote>Долевой тариф: на машину сажаем {limit} доли. '
                f'Свободных машин в этой локации с этим протоколом нет — '
                f'поднимите новую и выдайте её сквад. Следующие две доли '
                f'сядут на неё же.</blockquote>')

    rows = '\n'.join(f'<code>{row["squad"]}</code> — занято '
                     f'{row["used"]} из {row["limit"]}' for row in free)
    return (f'<blockquote>Долевой тариф: есть машина со свободным местом, '
            f'новую поднимать не нужно — выдайте тот же сквад.\n\n{rows}'
            f'</blockquote>')


async def give(call: types.CallbackQuery, callback_data: Adm, state: FSMContext,
               c, settings) -> None:
    server = await c.private.servers.get(callback_data.server_id)
    if not server:
        await call.answer('Заявка не найдена', show_alert=True)
        return
    if server.get('status') != ps.REQUESTED:
        await call.answer(f'Заявка уже в статусе «{server.get("status")}»', show_alert=True)
        return

    await state.set_state(Provision.squad)
    await state.update_data(server_id=server['_id'])

    # Кнопку жмут под самой карточкой — значит, координаты карточки известны
    # даже у заявок, созданных до того, как их начали запоминать.
    if (server.get('card_message_id') != call.message.message_id
            or server.get('card_chat_id') != call.message.chat.id):
        await c.private.servers.set(server['_id'],
                                    card_chat_id=call.message.chat.id,
                                    card_message_id=call.message.message_id)
        server = await c.private.servers.get(server['_id'])

    # В группе обычный текст до бота не доходит: у ботов включён privacy mode,
    # и Telegram отдаёт им только команды, реплаи и упоминания. Поэтому в
    # чате-группе просим командой, а «пришлите сообщением» оставляем личке —
    # иначе кнопка выглядит сломанной, хотя дело в настройке Telegram.
    in_group = getattr(call.message.chat, 'type', 'private') != 'private'
    how = (f'Пришлите командой: <code>/squad {server["_id"]} UUID</code>'
           if in_group else 'Пришлите UUID сообщением.')

    # Просьба — на месте инструкции в той же карточке, а не отдельным
    # сообщением: иначе тема превращается в переписку с ботом.
    note = (f'{e("edit")} <b>Жду UUID внутреннего сквада.</b> {how}\n\n'
            f'Это тот сквад, в котором стоит только новая нода: участники '
            f'сервера получат его и ничего больше.')
    if not await edit_card(call.bot, c, server, note, request_markup(server['_id'])):
        await call.message.answer(
            f'{e("edit")} UUID сквада для <code>{server["_id"]}</code>. {how}')
    await call.answer()


async def take_squad(message: types.Message, state: FSMContext, c, settings) -> None:
    """UUID сообщением — работает в личке, где privacy mode не мешает."""
    squad = (message.text or '').strip()
    if not looks_like_uuid(squad):
        await message.answer('Это не похоже на UUID. Пришлите ещё раз или /cancel.')
        return

    data = await state.get_data()
    await state.clear()
    await provision(message, c, data.get('server_id', ''), squad)


async def squad_command(message: types.Message, command, state: FSMContext,
                        c, settings) -> None:
    """`/squad [id сервера] UUID` — форма, которая доходит и в группе.

    Id сервера необязателен: если кнопку только что нажали, он уже лежит в
    состоянии. Явная форма нужна, когда состояние потерялось (перезапуск
    бота) или заявок в работе несколько.
    """
    parts = (command.args or '').split()
    server_id, squad = '', ''
    if len(parts) >= 2:
        server_id, squad = parts[0], parts[1]
    elif len(parts) == 1:
        squad = parts[0]
        server_id = (await state.get_data()).get('server_id', '')

    if not looks_like_uuid(squad):
        # Опечатку тоже убираем из темы, а жалобу пишем в карточку заявки,
        # если знаем, о какой из них речь.
        await drop_command(message)
        note = (f'{e("cross")} <b>Это не похоже на UUID сквада.</b>\n'
                f'<code>/squad srv_xxxxxxxx '
                f'00000000-0000-0000-0000-000000000000</code>')
        server = await c.private.servers.get(server_id) if server_id else None
        if not server or not await edit_card(message.bot, c, server, note,
                                             request_markup(server_id)):
            await message.answer(note)
        return
    if not server_id:
        await message.answer(f'{e("cross")} Не понял, какой сервер. Укажите его id: '
                             f'<code>/squad srv_xxxxxxxx UUID</code>')
        return

    await state.clear()
    await provision(message, c, server_id, squad)


# Почему выдача не прошла. Код ошибки сам по себе ничего не объясняет, а
# ошибиться здесь легко: сквад один на несколько долей, и перепутать его с
# соседним — обычное дело.
GIVE_ERRORS = {
    'panel': 'панель не приняла выдачу — заявка осталась в очереди',
    'no_free_machine': 'свободной машины под эту заявку нет',
    'not_found': 'заявка не найдена — проверьте id',
    'wrong_status': 'заявка уже обработана',
    'squad_busy': 'этот сквад уже отдан другому серверу. Обычный тариф '
                  'занимает машину целиком — проверьте UUID',
    'squad_full': 'на этой машине уже все доли заняты — нужна новая',
    'squad_mismatch': 'на этой машине другой тариф или другая локация. '
                      'Соседи по машине должны совпадать',
}


async def provision(message: types.Message, c, server_id: str, squad: str) -> None:
    # Команда админа уходит сразу: в теме должны остаться только заявки, а
    # результат всё равно приедет в саму карточку.
    await drop_command(message)

    # Локацию не спрашиваем: её выбрал покупатель на витрине, и переспросить
    # значит дать возможность молча выдать не то, за что заплатили.
    result = await c.private.activate(server_id, squad)
    if not result.ok:
        # С чем именно конфликт — списком. Иначе на руках остаётся сквад,
        # который «чем-то занят», и что это, приходится искать перебором.
        trouble = (f'{e("cross")} <b>Не вышло:</b> '
                   f'{GIVE_ERRORS.get(result.reason, result.reason)}'
                   + (f'\n\n<code>{result.note}</code>' if result.reason == 'panel'
                      and result.note else '')
                   + (f'\n\nНа этой машине уже:\n<code>{result.note}</code>\n\n'
                      f'Если это старый тестовый сервер — уберите его '
                      f'(<code>/srvdel {result.note.split(" ")[0]}</code>). Если он '
                      f'настоящий, а разошлась только локация — поправьте её '
                      f'(<code>/srvloc</code>) или выдайте этой заявке другой сквад.'
                      if result.note and result.reason != 'panel' else ''))
        # Кнопки оставляем: заявка жива, выдачу надо повторить.
        card = result.server or await c.private.servers.get(server_id)
        if not card or not await edit_card(message.bot, c, card, trouble,
                                           request_markup(server_id)):
            await message.answer(trouble)
        return

    server = result.server
    done = (f'{e("ok")} <b>Сервер выдан.</b> Сквад <code>{squad}</code>, '
            f'владелец уведомлён.')
    if not await edit_card(message.bot, c, server, done):
        await message.answer(f'{e("ok")} Сервер <code>{server["_id"]}</code> выдан. '
                             f'Владелец уведомлён.')

    # Текст владельцу живёт в сервисе: выдач стало три (руками, кнопкой из
    # запаса, автоматически при покупке), и три копии одного письма разошлись
    # бы на первой же правке.
    await c.private.tell_ready(server)


async def from_pool(call: types.CallbackQuery, callback_data: Adm, c,
                    settings) -> None:
    """Выдать заявке машину из запаса — одним нажатием."""
    server = await c.private.servers.get(callback_data.server_id)
    if server and (server.get('card_message_id') != call.message.message_id):
        await c.private.servers.set(server['_id'],
                                    card_chat_id=call.message.chat.id,
                                    card_message_id=call.message.message_id)

    result = await c.private.give_from_pool(callback_data.server_id)
    if not result.ok:
        await call.answer(
            'Свободной машины под эту заявку нет — поднимите новую'
            if result.reason == 'no_free_machine'
            else GIVE_ERRORS.get(result.reason, result.reason), show_alert=True)
        return

    server = result.server
    await edit_card(call.bot, c, server,
                    f'{e("ok")} <b>Выдан из запаса.</b> Сквад '
                    f'<code>{server.get("squad_uuid")}</code>, владелец уведомлён.')
    await call.answer(f'Выдан {e("ok")}')


async def reject(call: types.CallbackQuery, callback_data: Adm, c, settings) -> None:
    result = await c.private.reject(callback_data.server_id, reason='админ отказал')
    if not result.ok:
        await call.answer('Заявка уже обработана', show_alert=True)
        return

    await call.answer(f'Отказано, {result.amount}₽ возвращены', show_alert=True)
    # Той же карточкой: в теме остаётся одна заявка с итогом, а не заявка
    # плюс сообщение о ней.
    note = f'{e("cross")} <b>Отказано</b>, {result.amount}₽ возвращены на баланс.'
    if not await edit_card(call.bot, c, result.server, note):
        try:
            await call.message.edit_text(
                f'{e("cross")} Заявка <code>{callback_data.server_id}</code> отклонена, '
                f'{result.amount}₽ возвращены на баланс.')
        except Exception:
            pass

    try:
        await call.bot.send_message(
            result.server['owner_id'],
            f'{e("cross")} Не получилось запустить сервер. '
            f'{result.amount}₽ вернулись на ваш баланс.')
    except Exception as exc:
        log.warning('отказ по серверу не доставлен: %s', exc)


async def listing(message: types.Message, c, settings) -> None:
    """`/servers` — что вообще происходит с личными серверами."""
    servers = await c.private.servers.col.find(
        {'status': {'$in': list(ps.LIVE_STATUSES)}}).to_list(length=100)

    if not servers:
        await message.answer(f'{e("private")} Личных серверов пока нет.')
        return

    # Сколько долей на каждой машине — чтобы было видно, куда сядет
    # следующая заявка и какие машины пора освобождать.
    on_squad: dict[str, int] = {}
    for server in servers:
        if server.get('squad_uuid'):
            on_squad[server['squad_uuid']] = on_squad.get(server['squad_uuid'], 0) + 1

    lines = [f'{e("private")} <b>Личные серверы</b>\n']
    total = 0
    for server in servers:
        if server.get('status') == ps.ACTIVE:
            total += int(server.get('price') or 0)
        squad = server.get('squad_uuid') or ''
        # Хвост сквада — у всех, а не только у долей: по нему видно, какие
        # серверы стоят на одной машине, и кто занял тот UUID, который бот
        # только что отказался принять.
        machine = (f', доля {on_squad.get(squad, 1)}/{ps.shares_of(server)}'
                   if ps.is_shared(server) else '')
        machine += f' на <code>…{squad[-6:]}</code>' if squad else ''
        lines.append(
            f'{ps.STATUS_TITLES.get(server.get("status"), "")} '
            f'<code>{server["_id"]}</code> — {server.get("title")}, '
            f'владелец <code>{server.get("owner_id")}</code>, '
            f'{ps.occupied(server)}/{server.get("slots")} мест, '
            f'{ps.location_title(server)}/{ps.profile_title(server)}, '
            f'{server.get("price")}₽'
            + (f', до {fmt(server.get("paid_until"))}' if server.get('paid_until') else '')
            + machine)

    lines.append(f'\n<b>{e("money")} Выручка в месяц:</b> <code>{total}₽</code>')
    await message.answer('\n'.join(lines))


async def set_location(message: types.Message, command, c, settings) -> None:
    """`/srvloc <id> <локация> [протокол]` — проставить площадку задним числом.

    Серверы, заведённые до того, как появился выбор площадки, лежат без кода
    локации: у них не с чем сравнивать расход, и экран честно пишет, что
    квота неизвестна. Пересоздавать их ради этого незачем.
    """
    parts = (command.args or '').split()
    if len(parts) < 2 or parts[1] not in ps.BY_LOCATION:
        await message.answer(
            f'{e("cross")} <code>/srvloc srv_xxxxxxxx локация [протокол]</code>\n\n'
            f'Локации: <code>{", ".join(ps.BY_LOCATION)}</code>\n'
            f'Протоколы: <code>{", ".join(ps.BY_PROFILE)}</code>')
        return

    server_id, code = parts[0], parts[1]
    server = await c.private.servers.get(server_id)
    if not server:
        await message.answer(f'{e("cross")} Сервер не найден.')
        return

    location = ps.BY_LOCATION[code]
    # Долю продаём только на безлимит: терабайт на трёх покупателей с их
    # людьми кончается за месяц, и разбираться придётся со всеми сразу.
    if not ps.allowed_location(ps.plan_of(server), location):
        await message.answer(
            f'{e("cross")} <code>{code}</code> — площадка с лимитом трафика, '
            f'а тариф долевой. Безлимитные: '
            f'<code>{", ".join(loc.code for loc in ps.UNLIMITED)}</code>')
        return

    fields = {'location': code, 'traffic_gb': location.traffic_gb}
    if len(parts) > 2 and parts[2] in ps.BY_PROFILE:
        fields['profile'] = parts[2]

    await c.private.servers.set(server_id, **fields)
    server = await c.private.servers.get(server_id)
    await message.answer(
        f'{e("ok")} <code>{server_id}</code>: {ps.location_title(server)}, '
        f'{location.traffic_title}, протокол {ps.profile_title(server)}.')


async def diagnose(message: types.Message, command, c, settings) -> None:
    """`/srvdiag <id сервера>` — что бот знает и что отвечает панель.

    Нужна, потому что «статистика по нулям» имеет три разные причины: нет
    подписки в панели, панель не ответила, поле называется иначе. По экрану
    пользователя они неразличимы, а по этой выдаче — сразу видно.
    """
    server_id = (command.args or '').strip()
    server = await c.private.servers.get(server_id) if server_id else None
    if not server:
        await message.answer(f'{e("cross")} Укажите id: <code>/srvdiag srv_xxxxxxxx</code>')
        return

    lines = [f'{e("tools")} <b>Диагностика {server_id}</b>',
             f'локация: <code>{server.get("location") or "—"}</code> '
             f'({ps.location_title(server)}), '
             f'протокол: <code>{server.get("profile") or "—"}</code>',
             f'сквад: <code>{server.get("squad_uuid") or "не задан"}</code>', '']

    for row in await c.private.stats(server):
        user = await c.users.get(row['user_id'], {'vpn.uuid': 1,
                                                  'vpn.activeInternalSquads': 1})
        squads = c.users.pick(user or {}, 'vpn.activeInternalSquads') or []
        lines.append(
            f'<code>{row["user_id"]}</code> uuid=<code>'
            f'{c.users.pick(user or {}, "vpn.uuid") or "нет"}</code>\n'
            f'  трафик: <code>{row["traffic"]}</code> байт, '
            f'статус: <code>{row["status"] or "—"}</code>\n'
            f'  сквад сервера выдан: {"да" if server.get("squad_uuid") in squads else "НЕТ"}\n'
            + (f'  {e("warning")} {row["error"]}\n' if row.get('error') else '')
            + f'  источник: <code>{row.get("source") or "—"}</code>, '
              f'id в панели: <code>{row.get("panel_id") or "—"}</code>\n'
            + (f'  по ноде не вышло: <code>{row["usage_note"]}</code>\n'
               if row.get('usage_note') else '')
            + (f'  трафик в ответе: <code>{row["traffic_fields"]}</code>\n'
               if row.get('traffic_fields') else '')
            + (f'  поля ответа: <code>{", ".join(row.get("fields") or [])}</code>\n'
               if row.get('fields') else '')
            + (f'  ответ панели: <code>{row["raw"]}</code>'
               if row.get('raw') else ''))

    lines.append('')
    lines.append(f'{e("traffic")} <b>Ручки расхода</b>')
    for line in await c.private.usage_probe(server):
        lines.append(f'<code>{line}</code>')

    await message.answer('\n'.join(lines))


async def delete_server(message: types.Message, command, c, settings) -> None:
    """`/srvdel <id сервера|id юзера|@username> [refund]` — стереть сервер.

    Нужна для тестов: пока раздел обкатывается, свой же сервер приходится
    заводить и заводить заново, а второй одному человеку бот не продаст.
    Деньги возвращаются только по слову `refund` — на тестовом прогоне
    возврат мешает, а на живом человеке без него нельзя.
    """
    parts = (command.args or '').split()
    if not parts:
        await message.answer(
            f'{e("trash")} <b>Удаление личного сервера</b>\n\n'
            f'<code>/srvdel srv_xxxxxxxx</code> — по id сервера\n'
            f'<code>/srvdel 123456789</code> или <code>/srvdel @username</code> — '
            f'по владельцу\n'
            f'<code>/srvdel srv_xxxxxxxx refund</code> — ещё и вернуть деньги\n\n'
            f'<blockquote>Доступ снимается у всех участников, документ стирается '
            f'совсем — владелец сможет купить сервер заново. Отменить нельзя. '
            f'VPS в панели бот не трогает.</blockquote>')
        return

    target, refund = parts[0], 'refund' in parts[1:]

    if target.startswith('srv_'):
        server = await c.private.servers.get(target)
    else:
        owner = await c.moderation.find_user(target)
        if not owner:
            await message.answer(f'{e("cross")} Пользователь <code>{target}</code> '
                                 f'не найден.')
            return
        server = await c.private.servers.of_owner(
            (owner.get('user_data') or {}).get('user_id'))

    if not server:
        await message.answer(f'{e("cross")} Сервера нет: ни по id, ни у этого '
                             f'человека.')
        return

    members = len(server.get('members') or [])
    result = await c.private.wipe(server['_id'], refund=refund)
    if not result.ok:
        await message.answer(f'{e("cross")} Не вышло: {result.reason}')
        return

    await message.answer(
        f'{e("trash")} Сервер <code>{server["_id"]}</code> удалён.\n'
        f'Владелец: <code>{server.get("owner_id")}</code>, '
        f'участников отключено: {members}.\n'
        + (f'Возвращено: <code>{result.amount}₽</code>.\n' if result.amount else '')
        + (f'\n<blockquote>Тариф долевой: на машине '
           f'<code>…{(server.get("squad_uuid") or "")[-6:]}</code> освободилась '
           f'доля, VPS гасить не нужно.</blockquote>' if ps.is_shared(server) else
           f'\n<blockquote>VPS и сквад в панели остались — если сервер больше '
           f'не нужен, погасите его руками.</blockquote>'))


# ── очередь и запас ─────────────────────────────────────────────────────────
#
# Заявок бывает больше, чем помнит голова, и главный вопрос по утрам один:
# сколько машин поднять и каких. Ответ считается из самих заявок, а не
# ведётся руками.

async def queue_text(c) -> str:
    queue = await c.private.queue()
    pool = await c.private.pool_rows()
    free = [row for row in pool if row['free']]

    lines = [f'{e("private")} <b>Серверы: очередь и запас</b>', '']

    if not queue:
        lines.append(f'{e("ok")} Заявок в работе нет.')
    else:
        # Первым — список покупок. Машины покупаются по странам, и это
        # первое действие дня: купить, потом настраивать.
        need = await c.private.needed_locations()
        if need:
            lines.append(f'{e("cart")} <b>Купить машин</b>')
            for row in need:
                location = row['location']
                title = location.title if location else 'локация не указана'
                traffic = f', {location.traffic_title}' if location else ''
                how = ', '.join(
                    f'{ps.BY_PROFILE[code].title} × {count}'
                    for code, count in row['profiles'].items() if code in ps.BY_PROFILE)
                lines.append(f'<b>{title}</b>{traffic} — <b>{row["machines"]}</b> '
                             + ('шт.' if row['machines'] > 1 else 'шт.')
                             + (f' ({how})' if how else ''))
            lines.append('')

        waiting = sum(group['count'] for group in queue)
        ready = sum(group['ready'] for group in queue)
        lines.append(f'<b>Заявок ждёт: {waiting}</b>'
                     + (f', из них {ready} можно выдать прямо сейчас' if ready else ''))
        lines.append('')
        for group in queue:
            plan = group['plan']
            location = group['location']
            profile = group['profile']
            head = (f'{plan.title if plan else group["plan_code"]} — '
                    f'{location.title if location else "локация не указана"}, '
                    f'{profile.title if profile else "протокол не указан"}')
            lines.append(f'<b>{head}</b>')
            lines.append(f'   заявок: {group["count"]}'
                         + (f', готовы к выдаче: {group["ready"]}'
                            if group['ready'] else '')
                         + (f', <b>поднять машин: {group["machines"]}</b>'
                            if group['machines'] else ''))
            lines.append('   ' + ', '.join(f'<code>{server["_id"]}</code>'
                                           for server in group['servers'][:10]))
        lines.append('')

    lines.append(f'{e("rocket")} <b>Запас: {len(free)} свободных из {len(pool)}</b>')
    for row in pool:
        location = ps.BY_LOCATION.get(row.get('location') or '')
        profile = ps.BY_PROFILE.get(row.get('profile') or '')
        state = ('свободна' if row['free'] and not row['used'] else
                 f'занято {row["used"]} из {row["limit"]}' if row['free'] else 'занята')
        lines.append(f'<code>{row["squad_uuid"]}</code>\n'
                     f'   {location.title if location else row.get("location")}, '
                     f'{profile.title if profile else row.get("profile")} — {state}')

    # Сервер «выдан», а доступа нет — след старой ошибки в порядке действий
    # (статус ставился до обращения к панели). Из очереди такие ушли, и
    # увидеть их можно только этой сверкой.
    broken = await c.private.granted_check()
    if broken:
        lines.append('')
        lines.append(f'{e("warning")} <b>Выданы, но доступа у владельца нет: '
                     f'{len(broken)}</b>')
        for row in broken[:10]:
            server = row['server']
            lines.append(f'<code>{server["_id"]}</code> — владелец '
                         f'<code>{server.get("owner_id")}</code>, {row["why"]}')
        lines.append('<i>Лечится командой <code>/srvfix srv_xxxxxxxx</code>: '
                     'бот заново выдаст доступ владельцу и участникам, '
                     'ничего больше не трогая.</i>')

    lines.append('')
    lines.append('<blockquote>Поднимайте машины заранее и добавляйте их сюда: '
                 '<code>/pooladd UUID локация протокол</code>. Заявка на такую '
                 'машину выдаётся сама в момент покупки — человек получает '
                 'сервер сразу, а вам не приходит заявка.\n\n'
                 'Коды локаций: <code>ams fra sto bud mia nyc hkg</code> (1 ТБ), '
                 '<code>nl fra2 mil tyo</code> (безлимит). '
                 'Протоколы: <code>reality grpc hysteria2</code>.</blockquote>')
    return '\n'.join(lines)


async def queue_screen(call: types.CallbackQuery, c, settings) -> None:
    from app.admin.panel import edit
    from app.bot.callbacks import Admin as PanelAdm

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text=f'{e("refresh")} Обновить', callback_data=PanelAdm(act='srvq').pack()))
    kb.row(types.InlineKeyboardButton(
        text=f'{e("back")} Назад', callback_data=PanelAdm(act='main').pack()))
    await edit(call, await queue_text(c), kb)


async def queue_command(message: types.Message, c, settings) -> None:
    await message.answer(await queue_text(c))


async def pool_add(message: types.Message, command, c, settings) -> None:
    """`/pooladd UUID [локация] [протокол]` — записать поднятую машину в запас.

    Локацию и протокол бот выясняет у панели: набирать их руками при каждом
    добавлении — лишний повод ошибиться, а ошибка здесь продаёт не ту
    страну. Спрашиваем, только если панель ответила неоднозначно.
    """
    parts = (command.args or '').split()
    if not parts:
        await message.answer(
            f'{e("cross")} <code>/pooladd UUID</code> — локацию и протокол бот '
            f'определит сам по панели.\n'
            f'Не вышло — укажите явно: <code>/pooladd UUID локация протокол</code>\n\n'
            f'Локации: <code>{", ".join(ps.BY_LOCATION)}</code>\n'
            f'Протоколы: <code>{", ".join(ps.BY_PROFILE)}</code>')
        return

    squad = parts[0]
    if not looks_like_uuid(squad):
        await message.answer(f'{e("cross")} Это не похоже на UUID сквада.')
        return

    location = parts[1] if len(parts) > 1 else ''
    profile = parts[2] if len(parts) > 2 else ''
    detected = ''
    if not location or not profile:
        found = await c.private.detect_machine(squad)
        location, profile, trouble = _guess(found, location, profile)
        if trouble:
            await message.answer(trouble)
            return
        detected = _detected_note(found, location, profile)

    result = await c.private.pool_add(squad, location, profile, message.from_user.id)
    if not result.ok:
        await message.answer(
            f'{e("cross")} '
            + {'unknown_location': 'такой локации нет',
               'unknown_profile': 'такого протокола нет',
               'already_in_pool': 'эта машина уже в запасе',
               'squad_busy': 'на этом скваде уже стоит сервер',
               'no_pool': 'запас не подключён'}.get(result.reason, result.reason)
            + (f'\n\n<code>{result.note}</code>' if result.note else ''))
        return

    # Заявка могла прийти раньше машины — тогда выдаём её сразу же.
    # Каждую отдельно: одна упавшая выдача не должна ронять остальные и
    # превращаться в общее «сервис не отвечает» без единого имени.
    given, failed = [], []
    for server in await c.private.servers.pending():
        if (await c.private.pick_squad(server)) != squad:
            continue
        try:
            handed = await c.private.give_from_pool(server['_id'])
        except Exception as exc:
            log.exception('выдача %s из запаса не удалась', server['_id'])
            failed.append(f'{server["_id"]}: {exc}')
            continue

        if handed.ok:
            given.append(handed.server)
            await edit_card(message.bot, c, handed.server,
                            f'{e("rocket")} <b>Выдан из запаса</b> — машина '
                            f'появилась после заявки. Сквад <code>{squad}</code>.')
        else:
            failed.append(f'{server["_id"]}: '
                          + GIVE_ERRORS.get(handed.reason, handed.reason)
                          + (f' ({handed.note})' if handed.note else ''))

    await message.answer(
        f'{e("ok")} Машина <code>{squad}</code> в запасе '
        f'({ps.location_title({"location": location})}, '
        f'{ps.profile_title({"profile": profile})}).'
        + (f'\n{detected}' if detected else '')
        + (f'\n\n{e("rocket")} Сразу выдана ждавшим заявкам: '
           + ', '.join(f'<code>{server["_id"]}</code>' for server in given)
           if given else '')
        + (f'\n\n{e("cross")} <b>Не вышло выдать:</b>\n<code>'
           + '\n'.join(failed) + '</code>' if failed else ''))


def _guess(found: dict, location: str, profile: str) -> tuple[str, str, str]:
    """Дополнить недостающее тем, что увидела панель.

    Третье значение — текст отказа: пусто, если всё сошлось. Гадать между
    двумя площадками одной страны бот не имеет права, поэтому в спорном
    случае просит уточнить, а не выбирает сам.
    """
    if not location:
        options = found.get('locations') or ()
        if len(options) == 1:
            location = options[0].code
        else:
            what = (', '.join(f'{loc.code} ({loc.title}, {loc.traffic_title})'
                              for loc in options)
                    if options else 'ничего похожего')
            return '', '', (
                f'{e("cross")} Не понял площадку по панели: {what}.\n\n'
                f'Укажите явно: <code>/pooladd UUID локация протокол</code>\n'
                f'Локации: <code>{", ".join(ps.BY_LOCATION)}</code>')

    if not profile:
        options = found.get('profiles') or ()
        if len(options) == 1:
            profile = options[0].code
        else:
            what = ', '.join(p.title for p in options) if options else 'ничего похожего'
            return '', '', (
                f'{e("cross")} Не понял протокол по панели: {what}.\n\n'
                f'Укажите явно: <code>/pooladd UUID {location or "локация"} '
                f'протокол</code>\n'
                f'Протоколы: <code>{", ".join(ps.BY_PROFILE)}</code>')

    return location, profile, ''


def _detected_note(found: dict, location: str, profile: str) -> str:
    """Что именно бот прочитал в панели — чтобы ошибку было видно сразу."""
    source = found.get('name') or found.get('country') or 'ответ панели'
    return (f'<i>Определено по панели ({source}): '
            f'{ps.location_title({"location": location})}, '
            f'{ps.profile_title({"profile": profile})}. Не то — '
            f'<code>/pooldel</code> и добавьте с явными аргументами.</i>')


async def server_fix(message: types.Message, command, c, settings) -> None:
    """`/srvfix srv_xxxxxxxx` — выдать доступ заново, ничего больше не трогая."""
    server_id = (command.args or '').strip()
    if not server_id:
        await message.answer(f'{e("cross")} <code>/srvfix srv_xxxxxxxx</code>\n\n'
                             f'<blockquote>Заново выдаёт доступ владельцу и '
                             f'участникам активного сервера. Нужна, когда '
                             f'сервер числится выданным, а доступа нет.</blockquote>')
        return

    result = await c.private.regrant(server_id)
    if not result.ok:
        await message.answer(
            f'{e("cross")} '
            + {'not_found': 'сервер не найден',
               'no_squad': 'у сервера не задан сквад — выдайте его через /squad',
               'panel': 'панель не приняла'}.get(result.reason, result.reason)
            + (f'\n\n<code>{result.note}</code>' if result.note else ''))
        return

    await message.answer(f'{e("ok")} Доступ выдан заново: {result.amount} чел. '
                         f'(владелец и участники).')


async def squad_check(message: types.Message, command, c, settings) -> None:
    """`/squadcheck UUID` — что панель отвечает про этот сквад.

    Отдельная команда, потому что вопрос «почему не выдаётся» почти всегда
    сводится к одному: знает ли панель такой сквад и что она про него
    говорит. Ответ показывается как есть, без пересказа.
    """
    squad = (command.args or '').strip()
    if not squad:
        await message.answer(f'{e("cross")} <code>/squadcheck UUID</code>')
        return

    lines = [f'{e("tools")} <b>Сквад</b> <code>{squad}</code>', '']

    known, why = await c.private.squad_exists(squad)
    lines.append(f'{e("ok") if known else e("cross")} {why}')

    try:
        info = await c.private.vpn.squad(squad)
        lines.append(f'<code>{str(info)[:600]}</code>')
    except Exception as exc:
        lines.append(f'GET /api/internal-squads/UUID: <code>{exc}</code>')

    try:
        nodes = await c.private.vpn.squad_nodes(squad)
        lines.append(f'нод: {len(nodes)}')
        lines.append(f'<code>{str(nodes)[:600]}</code>')
    except Exception as exc:
        lines.append(f'accessible-nodes: <code>{exc}</code>')

    found = await c.private.detect_machine(squad)
    lines.append('')
    lines.append('распознано: '
                 + (', '.join(loc.code for loc in found['locations']) or '—')
                 + ' / '
                 + (', '.join(p.code for p in found['profiles']) or '—'))

    live = await c.private.servers.on_squad(squad)
    lines.append(f'серверов на скваде: {len(live)}')
    if live:
        from app.services.private_servers import describe

        lines.append(f'<code>{describe(live)}</code>')

    await message.answer('\n'.join(lines))


async def pool_remove(message: types.Message, command, c, settings) -> None:
    """`/pooldel UUID` — убрать машину из запаса."""
    squad = (command.args or '').strip()
    if not squad:
        await message.answer(f'{e("cross")} <code>/pooldel UUID</code>')
        return

    result = await c.private.pool_remove(squad)
    await message.answer(f'{e("ok")} Убрана из запаса.' if result.ok
                         else f'{e("cross")} Такой машины в запасе нет.')


def register(router: Router) -> None:
    from aiogram.filters import Command

    router.message.register(listing, Command('servers'))
    router.message.register(queue_command, Command('queue'))
    router.message.register(pool_add, Command('pooladd'))
    router.message.register(pool_remove, Command('pooldel'))
    router.message.register(squad_check, Command('squadcheck'))
    router.message.register(server_fix, Command('srvfix'))
    router.message.register(delete_server, Command('srvdel'))
    router.message.register(squad_command, Command('squad'))
    router.message.register(diagnose, Command('srvdiag'))
    router.message.register(set_location, Command('srvloc'))
    router.callback_query.register(give, Adm.filter(F.action == 'give'))
    router.callback_query.register(from_pool, Adm.filter(F.action == 'pool'))
    router.callback_query.register(reject, Adm.filter(F.action == 'reject'))
    router.message.register(take_squad, Provision.squad)

    from app.bot.callbacks import Admin as PanelAdm
    router.callback_query.register(queue_screen, PanelAdm.filter(F.act == 'srvq'))
