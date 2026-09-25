"""Сапёр на кнопках: правила поля, без Telegram и без базы.

Игра появилась ради одного экрана — того, который человек видит во время
техработ. Ждать молча неприятно, а «совсем скоро вернёмся» звучит лучше,
когда рядом есть чем занять пару минут.

Размер ограничен не вкусом, а Telegram: в ряду не больше восьми кнопок, на
всю клавиатуру — сотня. Поэтому ширина упирается в восемь, а высота — в то,
что останется после кнопок управления. По умолчанию 8×8 с десятью минами:
это классический «новичок» из оригинальной игры, и он проходится за пару
минут, не успевая надоесть.

Размер живёт в самом поле, а не в константах модуля. Иначе смена настройки
посреди партии превратила бы сохранённые поля в мусор: индексы клеток
считаются от ширины, и у поля 5×5 клетка 12 — центр, а у 8×8 — совсем
другое место.

Здесь только числа: индексы клеток, множества мин и открытого. Как это
выглядит — забота экрана, а не правил.
"""

from __future__ import annotations

import random

# Пределы Telegram: восемь кнопок в ряд, сто на всю клавиатуру. Высоту
# ограничиваем с запасом на строки управления — их три.
MAX_WIDTH = 8
MAX_HEIGHT = 10

WIDTH = 8
HEIGHT = 8
MINES = 10

PLAY, LOST, WON = 'play', 'lost', 'won'


def fit(width: int, height: int, mines: int) -> tuple[int, int, int]:
    """Загнать размер в то, что Telegram согласится показать.

    Настройку правит человек, а не код, и «12 в ряд» он введёт однажды
    обязательно. Молча урезать лучше, чем отдать клавиатуру, которую
    Telegram отвергнет целиком: тогда экран не покажется вовсе.
    """
    width = max(3, min(int(width or WIDTH), MAX_WIDTH))
    height = max(3, min(int(height or HEIGHT), MAX_HEIGHT))
    # Хотя бы одна клетка должна остаться свободной, иначе поле не пройти.
    mines = max(1, min(int(mines or MINES), width * height - 1))
    return width, height, mines


def new_board(width: int = WIDTH, height: int = HEIGHT,
              mines: int = MINES) -> dict:
    """Пустое поле. Мины появятся при первом нажатии, а не сейчас.

    Так первый ход не может закончиться взрывом: раскладываем мины уже зная,
    куда человек нажал. Проиграть первым же нажатием — верный способ закрыть
    игру навсегда, а она здесь для того, чтобы скрасить ожидание.
    """
    width, height, mines = fit(width, height, mines)
    return {'w': width, 'h': height, 'm': mines,
            'mines': [], 'open': [], 'flags': [], 'state': PLAY,
            'flag_mode': False}


def size(board: dict) -> tuple[int, int]:
    """Ширина и высота поля. Старые сохранённые партии размера не знают."""
    return int(board.get('w') or WIDTH), int(board.get('h') or HEIGHT)


def cells(board: dict) -> int:
    width, height = size(board)
    return width * height


def neighbours(board: dict, index: int) -> list[int]:
    width, height = size(board)
    row, column = divmod(index, width)
    found = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            r, c = row + dr, column + dc
            if 0 <= r < height and 0 <= c < width:
                found.append(r * width + c)
    return found


def around(board: dict, index: int) -> int:
    """Сколько мин рядом с клеткой."""
    mines = set(board.get('mines') or [])
    return sum(1 for cell in neighbours(board, index) if cell in mines)


def _place_mines(board: dict, first: int) -> None:
    """Разложить мины, обойдя первую клетку и всё вокруг неё.

    Свободный пятачок вокруг первого нажатия — не поблажка, а то, что
    открывает сразу область: иначе половина партий начинается с одинокой
    цифры, от которой некуда шагнуть.
    """
    total = cells(board)
    count = int(board.get('m') or MINES)
    safe = {first, *neighbours(board, first)}
    free = [cell for cell in range(total) if cell not in safe]
    if len(free) < count:
        # Поле такое тесное, что пятачок вокруг первого хода не помещается.
        # Тогда бережём только саму клетку — иначе мин просто не хватит.
        free = [cell for cell in range(total) if cell != first]
    board['mines'] = sorted(random.sample(free, min(count, len(free))))


def open_cell(board: dict, index: int) -> dict:
    """Открыть клетку. Возвращает то же поле — правила меняют его на месте."""
    if board.get('state') != PLAY or not 0 <= index < cells(board):
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
            queue.extend(n for n in neighbours(board, cell) if n not in opened)

    board['open'] = sorted(opened)
    board['flags'] = sorted(set(board.get('flags') or []) - opened)
    if is_won(board):
        board['state'] = WON
    return board


def toggle_flag(board: dict, index: int) -> dict:
    if board.get('state') != PLAY or not 0 <= index < cells(board):
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
    return len(set(board.get('open') or [])) == cells(board) - len(mines)


def mines_left(board: dict) -> int:
    """Сколько мин на поле. До первого хода их ещё нет — берём задуманное."""
    return len(board.get('mines') or []) or int(board.get('m') or MINES)


def flags_left(board: dict) -> int:
    """Сколько мин не отмечено. Может уйти в минус — это подсказка игроку."""
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
