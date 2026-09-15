"""Режим техработ и сапёр на кнопках.

Смысл режима — «человек не может сделать ничего», и проверять надо именно
это: не то, что появился экран, а то, что мимо него ничего не проходит.
Единственное исключение — игра, и она обязана оставаться исключением.
"""

import pytest

from app.bot.callbacks import Game, Menu
from app.bot.handlers.maintenance import board_markup, board_text, cell_label
from app.bot.middlewares.maintenance import MaintenanceMiddleware, is_game
from app.domain import minesweeper as ms


# ── правила поля ────────────────────────────────────────────────────────────
def test_the_first_tap_never_explodes():
    """Проиграть первым же нажатием — верный способ закрыть игру навсегда."""
    for cell in range(ms.cells(ms.new_board())):
        board = ms.open_cell(ms.new_board(), cell)
        assert board['state'] == ms.PLAY, cell
        assert cell not in set(board['mines'])


def test_the_first_tap_opens_a_whole_area():
    """Вокруг первой клетки мин нет, значит открывается область, а не цифра."""
    board = ms.open_cell(ms.new_board(), 12)

    assert len(board['open']) > 1


def test_the_mines_are_laid_only_once():
    board = ms.open_cell(ms.new_board(), 0)
    mines = list(board['mines'])

    ms.open_cell(board, next(c for c in range(ms.cells(board))
                             if c not in set(board['open']) and c not in set(mines)))

    assert board['mines'] == mines, 'поле не должно пересоздаваться посреди партии'


def test_stepping_on_a_mine_ends_the_game():
    board = ms.open_cell(ms.new_board(), 0)
    mine = board['mines'][0]

    ms.open_cell(board, mine)

    assert board['state'] == ms.LOST and board['boom'] == mine


def test_a_finished_game_ignores_further_taps():
    board = ms.open_cell(ms.new_board(), 0)
    ms.open_cell(board, board['mines'][0])
    opened = list(board['open'])

    ms.open_cell(board, 24)
    ms.toggle_flag(board, 24)

    assert board['open'] == opened and not board['flags']


def test_opening_everything_but_the_mines_wins():
    board = ms.open_cell(ms.new_board(), 0)
    for cell in range(ms.cells(board)):
        if cell not in set(board['mines']):
            ms.open_cell(board, cell)

    assert board['state'] == ms.WON


def test_an_empty_board_is_not_a_win():
    """Мин ещё нет — открыто ноль клеток из нуля, и это не победа."""
    assert not ms.is_won(ms.new_board())


def test_a_flag_protects_the_cell_from_opening():
    board = ms.new_board()
    ms.toggle_flag(board, 7)
    ms.open_cell(board, 7)

    assert board['open'] == [] and board['flags'] == [7]


def test_a_flag_can_be_taken_back():
    board = ms.new_board()
    ms.toggle_flag(board, 7)
    ms.toggle_flag(board, 7)

    assert board['flags'] == []


def test_flags_left_can_go_negative_as_a_hint():
    board = ms.open_cell(ms.new_board(), 0)
    for cell in (c for c in range(ms.cells(board)) if c not in set(board['open'])):
        ms.toggle_flag(board, cell)

    assert ms.flags_left(board) < 0, 'наставил больше, чем есть мин — это видно'


def test_the_numbers_match_the_neighbours():
    board = ms.new_board(5, 5, 4)
    board['mines'] = [0, 1]

    assert ms.around(board, 2) == 1     # сосед только с одной
    assert ms.around(board, 6) == 2     # по диагонали от обеих
    assert ms.around(board, 24) == 0    # дальний угол


def test_corners_have_three_neighbours_not_eight():
    board = ms.new_board(5, 5, 4)

    assert len(ms.neighbours(board, 0)) == 3
    assert len(ms.neighbours(board, ms.cells(board) - 1)) == 3
    assert len(ms.neighbours(board, 12)) == 8


# ── размер ──────────────────────────────────────────────────────────────────
def test_the_board_remembers_its_own_size():
    """Иначе смена настройки посреди партии превращает поле в мусор:
    индексы клеток считаются от ширины."""
    board = ms.new_board(6, 4, 5)

    assert ms.size(board) == (6, 4) and ms.cells(board) == 24
    assert len(ms.open_cell(board, 0)['mines']) == 5


def test_a_board_saved_before_sizes_existed_still_works():
    """У партий из прежней сборки полей w/h нет вовсе."""
    old = {'mines': [], 'open': [], 'flags': [], 'state': ms.PLAY}

    assert ms.size(old) == (ms.WIDTH, ms.HEIGHT)
    assert ms.open_cell(old, 0)['state'] == ms.PLAY


def test_an_impossible_size_is_trimmed_not_obeyed():
    """Клавиатуру шире восьми Telegram отвергает целиком — экрана не будет."""
    assert ms.fit(20, 40, 500) == (8, 10, 79)
    assert ms.fit(0, 0, 0) == (ms.WIDTH, ms.HEIGHT, ms.MINES)


def test_at_least_one_cell_stays_free_of_mines():
    """Поле, сплошь заминированное, пройти нельзя — и это не игра.

    Мин оставляем на одну меньше, чем клеток: единственный ход сразу же и
    выигрывает, но взорваться на первом нажатии человек не может.
    """
    board = ms.new_board(3, 3, 99)

    assert board['m'] == 8
    assert ms.open_cell(board, 4)['state'] == ms.WON


def test_a_tight_board_still_gets_all_its_mines():
    """На 3×3 свободный пятачок вокруг первого хода — это всё поле."""
    board = ms.open_cell(ms.new_board(3, 3, 8), 4)

    assert len(board['mines']) == 8 and 4 not in set(board['mines'])


# ── как это выглядит ────────────────────────────────────────────────────────
@pytest.mark.parametrize('width,height,mines', [
    (3, 3, 2), (5, 5, 4), (8, 8, 10), (8, 10, 20), (20, 40, 500),
])
def test_any_field_fits_telegram_limits(width, height, mines):
    """Восемь кнопок в ряд и сотня всего — иначе Telegram отвергнет
    клавиатуру целиком, и человек не увидит вообще ничего."""
    markup = board_markup(ms.new_board(width, height, mines)).as_markup()

    assert all(len(row) <= 8 for row in markup.inline_keyboard)
    assert sum(len(row) for row in markup.inline_keyboard) <= 100


def test_the_mines_stay_hidden_while_the_game_is_on():
    board = ms.open_cell(ms.new_board(), 0)
    labels = [cell_label(board, cell) for cell in board['mines']]

    assert all(label == cell_label(ms.new_board(), 0) for label in labels), \
        'закрытая мина не отличается от закрытой клетки'


def test_losing_shows_where_the_mines_were():
    board = ms.open_cell(ms.new_board(), 0)
    mine = board['mines'][0]
    ms.open_cell(board, mine)

    assert ms.cell_state(board, mine) == 'boom'
    assert all(ms.cell_state(board, other) == 'mine'
               for other in board['mines'][1:])


def test_a_finished_board_hides_the_mode_switch():
    """Переключать «флажки/открыть» после конца партии не на чем."""
    board = ms.open_cell(ms.new_board(), 0)
    ms.open_cell(board, board['mines'][0])

    labels = [b.text for row in board_markup(board, playing=False).as_markup()
              .inline_keyboard for b in row]

    assert not any('лажки' in label for label in labels)


def test_the_text_says_how_many_mines_are_left():
    board = ms.open_cell(ms.new_board(), 0)
    ms.toggle_flag(board, board['mines'][0])

    assert str(ms.MINES - 1) in board_text(board)


# ── перехват ────────────────────────────────────────────────────────────────
class Settings:
    def __init__(self, **values):
        self.values = {'features.maintenance_mode': True,
                       'features.maintenance_game': True,
                       'text.maintenance': 'Скоро вернёмся.', **values}

    async def flag(self, key):
        return bool(self.values.get(key))

    async def get(self, key):
        return self.values.get(key)


class Event:
    """Не CallbackQuery: перехватчик разбирает событие по типу, и подделка
    должна проваливаться во все ветки «это не сообщение и не нажатие»."""


async def run(settings, event, user_id=1, admins=(99,)):
    passed = []

    async def handler(event, data):
        passed.append(event)
        return 'прошло'

    class User:
        id = user_id

    result = await MaintenanceMiddleware(settings, admins)(
        handler, event, {'event_from_user': User()})
    return result, passed


async def test_nothing_gets_through_while_the_mode_is_on():
    result, passed = await run(Settings(), Event())

    assert result is None and passed == []


async def test_everything_works_when_the_mode_is_off():
    result, passed = await run(Settings(**{'features.maintenance_mode': False}),
                               Event())

    assert result == 'прошло' and len(passed) == 1


async def test_the_admin_is_not_affected():
    """Иначе включивший режим сам себя из бота и запирает."""
    result, _ = await run(Settings(), Event(), user_id=99)

    assert result == 'прошло'


def test_the_game_is_recognised_by_its_callback():
    from aiogram import types

    def call(data):
        return types.CallbackQuery(
            id='1', from_user=types.User(id=1, is_bot=False, first_name='В'),
            chat_instance='ci', data=data)

    assert is_game(call(Game(action='tap', value='7').pack()))
    assert not is_game(call(Menu(screen='profile').pack()))
    assert not is_game(Event())


async def test_the_game_still_works_during_maintenance():
    from aiogram import types

    event = types.CallbackQuery(
        id='1', from_user=types.User(id=1, is_bot=False, first_name='В'),
        chat_instance='ci', data=Game(action='tap', value='7').pack())

    result, passed = await run(Settings(), event)

    assert result == 'прошло' and len(passed) == 1


async def test_the_game_is_blocked_too_when_it_is_switched_off():
    from aiogram import types

    event = types.CallbackQuery(
        id='1', from_user=types.User(id=1, is_bot=False, first_name='В'),
        chat_instance='ci', data=Game(action='tap', value='7').pack())

    result, passed = await run(
        Settings(**{'features.maintenance_game': False}), event)

    assert result is None and passed == []
