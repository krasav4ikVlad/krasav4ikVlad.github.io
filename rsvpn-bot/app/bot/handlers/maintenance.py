"""Экран техработ и сапёр на кнопках.

Пока включён режим техработ, бот ничего не делает — и человек об этом
узнаёт из всплывающего окошка на любое нажатие. Такой ответ хуже молчания:
он не говорит ни что случилось, ни когда пройдёт, и повторяется на каждое
нажатие, пока человек не бросит.

Здесь вместо этого один нормальный экран: что происходит, что подписка
работает, и кнопка «пока подождём — сыграем». Сапёр маленький, партия на
минуту; он ничего не меняет в боте и работает при любой поломке базы
подписок, потому что о ней не знает.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Game
from app.content.emoji import e
from app.domain import minesweeper as ms

# Цифра в открытой клетке — обычный символ, а не значок: кнопок двадцать
# пять, и рябь из цветных эмодзи мешает увидеть само поле.
DIGITS = {'0': '·', '1': '1', '2': '2', '3': '3',
          '4': '4', '5': '5', '6': '6', '7': '7', '8': '8'}


def cell_label(board: dict, index: int) -> str:
    state = ms.cell_state(board, index)
    if state in DIGITS:
        return DIGITS[state]
    return e({'closed': 'cell', 'flag': 'flag',
              'mine': 'mine', 'boom': 'boom'}[state])


def board_markup(board: dict, playing: bool = True) -> InlineKeyboardBuilder:
    width, height = ms.size(board)
    kb = InlineKeyboardBuilder()
    for row in range(height):
        kb.row(*[types.InlineKeyboardButton(
            text=cell_label(board, row * width + column),
            callback_data=Game(action='tap', value=str(row * width + column)).pack())
            for column in range(width)])

    if playing:
        # Переключатель, а не долгое нажатие: второго способа нажать кнопку
        # в Telegram нет, а без флажков в сапёра не играют.
        flagging = board.get('flag_mode')
        kb.row(types.InlineKeyboardButton(
            text=(f'{e("flag")} Ставим флажки' if flagging
                  else f'{e("cell")} Открываем клетки'),
            callback_data=Game(action='mode').pack()))

    kb.row(types.InlineKeyboardButton(
        text=f'{e("refresh")} Новое поле', callback_data=Game(action='new').pack()))
    kb.row(types.InlineKeyboardButton(
        text=f'{e("back")} К сообщению', callback_data=Game(action='exit').pack()))
    return kb


def board_text(board: dict, wins: int = 0) -> str:
    state = board.get('state')
    if state == ms.LOST:
        head = f'{e("boom")} <b>Мина.</b> Ну и ладно — техработы всё равно идут.'
    elif state == ms.WON:
        head = f'{e("trophy")} <b>Поле чистое!</b> Отличная работа.'
    elif not board.get('mines'):
        head = (f'{e("mine")} <b>Сапёр</b>\n\nНажмите любую клетку — первая '
                f'никогда не взрывается.')
    else:
        head = (f'{e("mine")} <b>Сапёр</b>\n\nМин на поле: '
                f'<b>{ms.mines_left(board)}</b>, осталось отметить: '
                f'<b>{ms.flags_left(board)}</b>.')

    tail = (f'\n\n<blockquote>Цифра — сколько мин рядом с клеткой. '
            f'Победа, когда открыты все клетки без мин.</blockquote>'
            if state == ms.PLAY else
            f'\n\n<blockquote>Полей пройдено: <b>{wins}</b></blockquote>'
            if wins else '')

    return head + tail


async def fresh_board(settings) -> dict:
    """Новое поле того размера, который выставлен в админке."""
    return ms.new_board(await settings.int('game.width'),
                        await settings.int('game.height'),
                        await settings.int('game.mines'))


async def maintenance_text(settings) -> str:
    """Сообщение о техработах. Текст берётся из настроек — его правят чаще
    всего и обычно в тот момент, когда выкладывать новую сборку нельзя."""
    body = str(await settings.get('text.maintenance') or '').strip()
    return (f'{e("wrench")} <b>Идут технические работы</b>\n\n'
            f'{body}\n\n'
            f'<blockquote>Ваша подписка продолжает работать: VPN включается '
            f'и работает как обычно. Недоступен только сам бот — покупка, '
            f'продление и настройки вернутся вместе с ним.</blockquote>')


async def maintenance_markup(settings) -> types.InlineKeyboardMarkup | None:
    kb = InlineKeyboardBuilder()
    if await settings.flag('features.maintenance_game'):
        kb.row(types.InlineKeyboardButton(
            text=f'{e("mine")} Сыграть в сапёра, пока ждём',
            callback_data=Game(action='open').pack()))
    channel = str(await settings.get('link.channel') or '').strip()
    if channel:
        kb.row(types.InlineKeyboardButton(
            text=f'{e("channel")} Новости RS VPN', url=channel))
    return kb.as_markup() if kb.buttons else None


async def show_maintenance(event, settings) -> None:
    """Показать экран техработ поверх текущего — или отдельным сообщением."""
    text = await maintenance_text(settings)
    markup = await maintenance_markup(settings)

    if isinstance(event, types.CallbackQuery):
        try:
            await event.answer()
        except Exception:
            pass
        try:
            await event.message.edit_text(text, reply_markup=markup)
            return
        except Exception:
            # Сообщение могло быть с картинкой или уже с этим же текстом —
            # тогда просто пишем новое, а не оставляем человека без ответа.
            try:
                await event.message.answer(text, reply_markup=markup)
            except Exception:
                pass
        return

    if isinstance(event, types.Message):
        try:
            await event.answer(text, reply_markup=markup)
        except Exception:
            pass


# ── сапёр ───────────────────────────────────────────────────────────────────
async def _render(call: types.CallbackQuery, c, board: dict, wins: int = 0) -> None:
    markup = board_markup(board, playing=board.get('state') == ms.PLAY).as_markup()
    try:
        await call.message.edit_text(board_text(board, wins), reply_markup=markup)
    except Exception:
        # «message is not modified» — нажали уже открытую клетку
        pass


async def open_game(call: types.CallbackQuery, c, settings) -> None:
    board = await c.games.load(call.from_user.id)
    if not board or board.get('state') != ms.PLAY:
        board = await fresh_board(settings)
    await c.games.save(call.from_user.id, board)
    await call.answer()
    await _render(call, c, board)


async def new_game(call: types.CallbackQuery, c, settings) -> None:
    board = await fresh_board(settings)
    await c.games.save(call.from_user.id, board)
    await call.answer('Новое поле')
    await _render(call, c, board)


async def toggle_mode(call: types.CallbackQuery, c, settings) -> None:
    board = await c.games.load(call.from_user.id) or await fresh_board(settings)
    board['flag_mode'] = not board.get('flag_mode')
    await c.games.save(call.from_user.id, board)
    await call.answer('Флажки' if board['flag_mode'] else 'Открываем')
    await _render(call, c, board)


async def tap(call: types.CallbackQuery, callback_data: Game, c, settings) -> None:
    board = await c.games.load(call.from_user.id) or await fresh_board(settings)
    if board.get('state') != ms.PLAY:
        await call.answer('Партия окончена — начните новое поле', show_alert=False)
        return

    try:
        index = int(callback_data.value)
    except (TypeError, ValueError):
        await call.answer()
        return

    if board.get('flag_mode'):
        ms.toggle_flag(board, index)
    else:
        ms.open_cell(board, index)

    won = board.get('state') == ms.WON
    await c.games.save(call.from_user.id, board, wins=1 if won else 0)
    wins = await c.games.wins(call.from_user.id) if board['state'] != ms.PLAY else 0

    await call.answer({'won': 'Чисто!', 'lost': 'Бабах'}.get(board['state'], ''))
    await _render(call, c, board, wins)


async def exit_game(call: types.CallbackQuery, c, settings) -> None:
    await show_maintenance(call, settings)


async def game_command(message: types.Message, c, settings) -> None:
    """`/game` — сыграть и без техработ, если админ разрешил."""
    if not await settings.flag('features.maintenance_game'):
        return
    board = await c.games.load(message.from_user.id)
    if not board or board.get('state') != ms.PLAY:
        board = await fresh_board(settings)
    await c.games.save(message.from_user.id, board)
    await message.answer(board_text(board),
                         reply_markup=board_markup(board).as_markup())


def create_router() -> Router:
    router = Router(name='minesweeper')
    router.callback_query.register(open_game, Game.filter(F.action == 'open'))
    router.callback_query.register(new_game, Game.filter(F.action == 'new'))
    router.callback_query.register(toggle_mode, Game.filter(F.action == 'mode'))
    router.callback_query.register(tap, Game.filter(F.action == 'tap'))
    router.callback_query.register(exit_game, Game.filter(F.action == 'exit'))
    router.message.register(game_command, Command('game'))
    return router
