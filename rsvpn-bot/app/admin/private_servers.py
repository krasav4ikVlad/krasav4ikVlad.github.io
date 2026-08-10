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


def request_markup(server_id: str) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn(f'{e("ok")} Выдать сервер', 'give', server_id))
    kb.row(_btn(f'{e("cross")} Отказать и вернуть деньги', 'reject', server_id))
    return kb.as_markup()


async def request_card(c, server: dict) -> str:
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
            + await _how_to_give(c, server))


async def _how_to_give(c, server: dict) -> str:
    """Что делать админу. Для доли — куда её можно подсадить.

    Долевой сервер поднимать заново не надо, если машина уже есть и на ней
    осталось место. Держать в голове, какой сквад чем занят, невозможно,
    поэтому свободные машины бот показывает прямо в заявке.
    """
    plan = ps.plan_of(server)
    if not plan or not plan.shared:
        return ('<blockquote>Поднимите VPS в указанной локации с указанным '
                'протоколом, заведите под него внутренний сквад в панели и '
                'нажмите «Выдать сервер» — бот попросит UUID сквада.</blockquote>')

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

    # В группе обычный текст до бота не доходит: у ботов включён privacy mode,
    # и Telegram отдаёт им только команды, реплаи и упоминания. Поэтому в
    # чате-группе просим командой, а «пришлите сообщением» оставляем личке —
    # иначе кнопка выглядит сломанной, хотя дело в настройке Telegram.
    in_group = getattr(call.message.chat, 'type', 'private') != 'private'
    how = (f'Пришлите командой:\n<code>/squad {server["_id"]} UUID</code>'
           if in_group else 'Пришлите UUID сообщением.')

    await call.message.answer(
        f'{e("edit")} <b>UUID внутреннего сквада</b> для сервера '
        f'<code>{server["_id"]}</code>.\n\n{how}\n\n'
        f'<blockquote>Это тот сквад, в котором стоит только новая нода. '
        f'Участники сервера получат его и ничего больше.</blockquote>')
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
        await message.answer(
            f'{e("cross")} Нужен UUID сквада.\n'
            f'<code>/squad srv_xxxxxxxx 00000000-0000-0000-0000-000000000000</code>')
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
    'not_found': 'заявка не найдена — проверьте id',
    'wrong_status': 'заявка уже обработана',
    'squad_busy': 'этот сквад уже отдан другому серверу. Обычный тариф '
                  'занимает машину целиком — проверьте UUID',
    'squad_full': 'на этой машине уже все доли заняты — нужна новая',
    'squad_mismatch': 'на этой машине другой тариф или другая локация. '
                      'Соседи по машине должны совпадать',
}


async def provision(message: types.Message, c, server_id: str, squad: str) -> None:
    # Локацию не спрашиваем: её выбрал покупатель на витрине, и переспросить
    # значит дать возможность молча выдать не то, за что заплатили.
    result = await c.private.activate(server_id, squad)
    if not result.ok:
        await message.answer(
            f'{e("cross")} Не вышло: {GIVE_ERRORS.get(result.reason, result.reason)}')
        return

    server = result.server
    await message.answer(f'{e("ok")} Сервер <code>{server["_id"]}</code> выдан. '
                         f'Владелец уведомлён.')

    try:
        await message.bot.send_message(
            server['owner_id'],
            f'{e("private")} <b>Ваш сервер готов</b>\n\n'
            f'«{server.get("title")}» уже работает.\n'
            f'Локация: {ps.location_title(server)}, '
            f'протокол: {ps.profile_title(server)}.\n'
            + f'Мест: {server.get("slots")}, оплачен до '
              f'{fmt(server.get("paid_until"))}.\n\n'
            + (f'Эту же машину купили {ps.others_on_machine(ps.shares_of(server))}, '
               f'но места и статистика у вас свои: их и их гостей вы не видите, '
               f'они вас тоже.\n\n' if ps.is_shared(server) else '')
            + 'Откройте профиль → «Свой сервер», чтобы позвать друзей.')
    except Exception as exc:
        log.warning('владелец %s не уведомлён о выдаче: %s', server['owner_id'], exc)


async def reject(call: types.CallbackQuery, callback_data: Adm, c, settings) -> None:
    result = await c.private.reject(callback_data.server_id, reason='админ отказал')
    if not result.ok:
        await call.answer('Заявка уже обработана', show_alert=True)
        return

    await call.answer(f'Отказано, {result.amount}₽ возвращены', show_alert=True)
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
        shared = (f', доля {on_squad.get(squad, 1)}/{ps.shares_of(server)} '
                  f'на <code>…{squad[-6:]}</code>' if ps.is_shared(server) and squad else '')
        lines.append(
            f'{ps.STATUS_TITLES.get(server.get("status"), "")} '
            f'<code>{server["_id"]}</code> — {server.get("title")}, '
            f'владелец <code>{server.get("owner_id")}</code>, '
            f'{ps.occupied(server)}/{server.get("slots")} мест, '
            f'{ps.location_title(server)}/{ps.profile_title(server)}, '
            f'{server.get("price")}₽'
            + (f', до {fmt(server.get("paid_until"))}' if server.get('paid_until') else '')
            + shared)

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


def register(router: Router) -> None:
    from aiogram.filters import Command

    router.message.register(listing, Command('servers'))
    router.message.register(delete_server, Command('srvdel'))
    router.message.register(squad_command, Command('squad'))
    router.message.register(diagnose, Command('srvdiag'))
    router.message.register(set_location, Command('srvloc'))
    router.callback_query.register(give, Adm.filter(F.action == 'give'))
    router.callback_query.register(reject, Adm.filter(F.action == 'reject'))
    router.message.register(take_squad, Provision.squad)
