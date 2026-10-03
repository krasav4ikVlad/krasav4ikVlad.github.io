"""Сторож базы: пишет админам лично, когда бот перестал до неё доставать.

25 сентября про аварию узнали от людей через несколько часов. Всё, что
было у бота для таких случаев, писалось в админ-чат по номеру из настроек,
а настройки лежат в базе — то есть недоступны ровно тогда, когда нужны.

Отсюда главные проверки этого файла: сторож пишет по адресам из .env, не
трогая ни настроек, ни коллекций; не будит из-за одного моргания сети;
не шлёт одно и то же каждую минуту; и обязательно говорит, когда база
вернулась.
"""

import asyncio

import pytest

from app.services.watchdog import FAILS_BEFORE_ALARM, Watchdog


class FakeDB:
    """База, которую можно уронить и поднять."""

    def __init__(self, alive: bool = True, hang: bool = False):
        self.alive = alive
        self.hang = hang
        self.pings = 0

    async def command(self, name):
        self.pings += 1
        if self.hang:
            await asyncio.sleep(30)
        if not self.alive:
            raise RuntimeError('195.133.30.98:27017: timed out')
        return {'ok': 1}


class FakeBot:
    def __init__(self, broken: bool = False):
        self.sent: list[tuple[int, str]] = []
        self.broken = broken

    async def send_message(self, chat_id, text, **kwargs):
        if self.broken:
            raise RuntimeError('bot was blocked by the user')
        self.sent.append((chat_id, text))
        return True


def guard(db, bot, admins=(802421217, 1107871653)) -> Watchdog:
    return Watchdog(db, bot, admins)


async def break_it(watchdog: Watchdog) -> None:
    """Провалить столько проверок, сколько нужно для тревоги."""
    for _ in range(FAILS_BEFORE_ALARM):
        await watchdog.check()


# ── тревога ─────────────────────────────────────────────────────────────────
async def test_the_admins_are_told_personally():
    """Не в админ-чат: его номер лежит в базе, которая и не отвечает."""
    bot = FakeBot()
    await break_it(guard(FakeDB(alive=False), bot))

    assert [chat for chat, _ in bot.sent] == [802421217, 1107871653]
    assert 'не достаёт до базы' in bot.sent[0][1]


async def test_the_reason_is_quoted_as_is():
    """По тексту ошибки видно, это сеть или сама база."""
    bot = FakeBot()
    await break_it(guard(FakeDB(alive=False), bot))

    assert '195.133.30.98:27017' in bot.sent[0][1]


async def test_one_blink_of_the_network_does_not_wake_anyone():
    bot = FakeBot()
    watchdog = guard(FakeDB(alive=False), bot)

    await watchdog.check()

    assert bot.sent == []


async def test_a_hanging_database_counts_as_a_dead_one(monkeypatch):
    """Висящая проверка — это не проверка: ответа нет так же, как и при
    отказе, только ждать его можно вечно."""
    monkeypatch.setattr('app.services.watchdog.PING_TIMEOUT_SEC', 0.05)
    bot = FakeBot()

    await break_it(guard(FakeDB(hang=True), bot))

    assert bot.sent and 'не ответила' in bot.sent[0][1]


# ── не спамить ──────────────────────────────────────────────────────────────
async def test_the_same_alarm_is_not_repeated_every_minute():
    bot = FakeBot()
    watchdog = guard(FakeDB(alive=False), bot)

    await break_it(watchdog)
    for _ in range(10):
        await watchdog.check()

    assert len(bot.sent) == 2      # по одному каждому админу, и всё


async def test_a_reminder_comes_after_the_pause(monkeypatch):
    """Ночную аварию нельзя проспать — но и двести сообщений не нужны."""
    monkeypatch.setattr('app.services.watchdog.REMIND_MINUTES', 0)
    bot = FakeBot()
    watchdog = guard(FakeDB(alive=False), bot)

    await break_it(watchdog)
    await watchdog.check()

    assert len(bot.sent) == 4
    assert 'не отвечает уже' in bot.sent[-1][1]


# ── возвращение ─────────────────────────────────────────────────────────────
async def test_recovery_is_reported_too():
    """Иначе непонятно, чинить дальше или уже нет."""
    db = FakeDB(alive=False)
    bot = FakeBot()
    watchdog = guard(db, bot)
    await break_it(watchdog)

    db.alive = True
    await watchdog.check()

    assert 'снова отвечает' in bot.sent[-1][1]
    assert '/outage' in bot.sent[-1][1]


async def test_a_second_outage_is_reported_again():
    """Состояние должно сбрасываться: иначе про вторую аварию за день
    сторож промолчит."""
    db = FakeDB(alive=False)
    bot = FakeBot()
    watchdog = guard(db, bot)

    await break_it(watchdog)
    db.alive = True
    await watchdog.check()
    db.alive = False
    await break_it(watchdog)

    assert sum('не достаёт до базы' in text for _, text in bot.sent) == 4


async def test_a_healthy_database_is_silent():
    bot = FakeBot()
    watchdog = guard(FakeDB(), bot)

    for _ in range(5):
        await watchdog.check()

    assert bot.sent == []


# ── сторож не должен падать сам ─────────────────────────────────────────────
async def test_a_blocked_admin_does_not_break_the_watchdog():
    """Один недоступный адресат не должен лишать сообщения остальных и
    ронять задачу планировщика."""
    watchdog = guard(FakeDB(alive=False), FakeBot(broken=True))

    await break_it(watchdog)      # не должно выбросить исключение

    assert watchdog.down is True


async def test_it_works_without_a_bot_at_all():
    """Контейнер собирают и без Bot — сторож не должен этого бояться."""
    watchdog = Watchdog(FakeDB(alive=False), None, [1])

    await break_it(watchdog)

    assert watchdog.down is True


# ── видно ли сторожа до аварии ──────────────────────────────────────────────
#
# Знать, что сторож заведён, нужно заранее: во время аварии выяснять,
# работает ли то, что должно было о ней сообщить, уже поздно.

def test_diag_says_the_watchdog_is_on_duty():
    from app.admin.diag import watchdog_line

    line = watchdog_line(guard(FakeDB(), FakeBot()))

    assert 'следит' in line and '2 чел.' in line


def test_diag_shouts_when_the_watchdog_is_missing():
    from app.admin.diag import watchdog_line

    assert 'не собран' in watchdog_line(None)


async def test_diag_shows_the_outage_while_it_lasts():
    from app.admin.diag import watchdog_line

    watchdog = guard(FakeDB(alive=False), FakeBot())
    await break_it(watchdog)

    assert 'не отвечает' in watchdog_line(watchdog)
