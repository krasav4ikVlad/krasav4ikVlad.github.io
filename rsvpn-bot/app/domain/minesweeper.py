"""Сапёр на кнопках: правила поля, без Telegram и без базы.

Игра появилась ради одного экрана — того, который человек видит во время
техработ. Ждать молча неприятно, а «совсем скоро вернёмся» звучит лучше,
когда рядом есть чем занять полминуты.

Поле маленькое нарочно: кнопок в ряду у Telegram восемь, а весь экран
должен помещаться без прокрутки, вместе с текстом и кнопками управления.
Пять на пять с четырьмя минами проходится за минуту и не успевает надоесть.

Здесь только числа: индексы клеток, множества мин и открытого. Как это
выглядит — забота экрана, а не правил.
"""

from __future__ import annotations

import random

WIDTH = 5
HEIGHT = 5
MINES = 4
CELLS = WIDTH * HEIGHT

PLAY, LOST, WON = 'play', 'lost', 'won'


def new_board() -> dict:
    """Пустое поле. Мины появятся при первом нажатии, а не сейчас.

    Так первый ход не может закончиться взрывом: раскладываем мины уже зная,
    куда человек нажал. Проиграть первым же нажатием — верный способ закрыть
    игру навсегда, а она здесь для того, чтобы скрасить ожидание.
    """
    return {'mines': [], 'open': [], 'flags': [], 'state': PLAY, 'flag_mode': False}


def neighbours(index: int) -> list[int]:
    row, column = divmod(index, WIDTH)
    found = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            r, c = row + dr, column + dc
            if 0 <= r < HEIGHT and 0 <= c < WIDTH:
                found.append(r * WIDTH + c)
    return found


def around(board: dict, index: int) -> int:
    """Сколько мин рядом с клеткой."""
    mines = set(board.get('mines') or [])
    return sum(1 for cell in neighbours(index) if cell in mines)


def _place_mines(board: dict, first: int) -> None:
    """Разложить мины, обойдя первую клетку и всё вокруг неё.

    Свободный пятачок вокруг первого нажатия — не поблажка, а то, что
    открывает сразу область: иначе половина партий начинается с одинокой
    цифры, от которой некуда шагнуть.
    """
    safe = {first, *neighbours(first)}
    free = [cell for cell in range(CELLS) if cell not in safe]
    # Мин может оказаться больше, чем осталось клеток: поле маленькое.
    board['mines'] = sorted(random.sample(free, min(MINES, len(free))))


def open_cell(board: dict, index: int) -> dict:
    """Открыть клетку. Возвращает то же поле — правила меняют его на месте."""
    if board.get('state') != PLAY or not 0 <= index < CELLS:
        return board
    if index in set(board.get('open') or []) or index in set(board.get('flags') or []):
        return board

    if not board.get('mines'):
        _place_mines(board, index)

    if index in set(board['mines']):
        board['state'] = LOST
        board['boom'] = index
        return board

    opened = set(board.get('open') or [])
    # Обход в ширину: у пустой клетки открываются соседи, и так до цифр.
    # Без этого поле приходится выщёлкивать по клетке, и игра из отдыха
    # превращается в работу.
    queue = [index]
    while queue:
        cell = queue.pop()
        if cell in opened:
            continue
        opened.add(cell)
        if around(board, cell) == 0:
            queue.extend(n for n in neighbours(cell) if n not in opened)

    board['open'] = sorted(opened)
    board['flags'] = sorted(set(board.get('flags') or []) - opened)
    if is_won(board):
        board['state'] = WON
    return board


def toggle_flag(board: dict, index: int) -> dict:
    if board.get('state') != PLAY or not 0 <= index < CELLS:
        return board
    if index in set(board.get('open') or []):
        return board

    flags = set(board.get('flags') or [])
    board['flags'] = sorted(flags ^ {index})
    return board


def is_won(board: dict) -> bool:
    """Победа — когда открыто всё, кроме мин. Флажки для этого не нужны."""
    mines = set(board.get('mines') or [])
    if not mines:
        return False
    return len(set(board.get('open') or [])) == CELLS - len(mines)


def mines_left(board: dict) -> int:
    """Сколько мин ещё не отмечено. Может уйти в минус — это подсказка игроку."""
    return len(board.get('mines') or []) or MINES


def flags_left(board: dict) -> int:
    return mines_left(board) - len(board.get('flags') or [])


def cell_state(board: dict, index: int) -> str:
    """Что показывать в клетке: closed | flag | mine | boom | число строкой."""
    state = board.get('state')
    mines = set(board.get('mines') or [])
    opened = set(board.get('open') or [])
    flags = set(board.get('flags') or [])

    if state == LOST and index in mines:
        return 'boom' if index == board.get('boom') else 'mine'
    if index in flags:
        return 'flag'
    if index not in opened:
        return 'closed'
    return str(around(board, index))
