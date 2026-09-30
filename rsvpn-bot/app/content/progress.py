"""Полоска выполнения для долгих дел.

Копия базы идёт минуту-другую, и всё это время сообщение «снимаю…»
неотличимо от зависшего бота. Полоска отвечает на два вопроса сразу:
идёт ли дело вообще и сколько ещё ждать.

Правила здесь простые и потому вынесены отдельно: полоска одинаковая
везде, где она появится, а не «в каждом месте своя».
"""

from __future__ import annotations

import time

FULL = '█'
EMPTY = '░'
WIDTH = 18

# Telegram не даёт править сообщение чаще примерно раза в секунду, а на
# правку одним и тем же текстом отвечает ошибкой. Поэтому обновляем не
# чаще, чем раз в EVERY_SEC, и только когда цифра изменилась.
EVERY_SEC = 2.0


def bar(done: int, total: int, width: int = WIDTH) -> str:
    share = 0.0 if total <= 0 else max(0.0, min(1.0, done / total))
    filled = round(share * width)
    return FULL * filled + EMPTY * (width - filled)


def percent(done: int, total: int) -> int:
    return 0 if total <= 0 else max(0, min(100, round(done * 100 / total)))


def screen(title: str, note: str, done: int = 0, total: int = 0) -> str:
    """Заголовок, полоска, а снизу — что происходит прямо сейчас."""
    return (f'<b>{title}</b>\n\n'
            f'<code>{bar(done, total)}</code>  {percent(done, total)}%\n\n'
            f'{note}')


class Ticker:
    """Пускать ли очередное обновление.

    Первый и последний шаги проходят всегда: начало и конец важнее любой
    экономии на запросах.
    """

    def __init__(self, every: float = EVERY_SEC):
        self.every = every
        self.last_at = 0.0
        self.last_value = -1

    def should(self, value: int, force: bool = False) -> bool:
        moment = time.monotonic()
        if force:
            self.last_at, self.last_value = moment, value
            return True
        if value == self.last_value:
            return False
        if moment - self.last_at < self.every:
            return False
        self.last_at, self.last_value = moment, value
        return True
