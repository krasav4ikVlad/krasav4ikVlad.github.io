"""Личные серверы: тарифы и правила, без базы и без Telegram.

Модель — не «сервер для компании», а «свой сервер, который делят с друзьями».
Человек платит за весь сервер, зовёт своих и делит стоимость с ними сам:
денег между пользователями бот не считает, и это осознанно — иначе появляются
возвраты, споры и обязанность разбирать чужие расчёты.

Изоляция здесь настоящая: сервер = отдельный внутренний сквад в панели, и
участники видят только его. Поэтому и слот — это доступ к сквад,
а не «место в списке».
"""

from __future__ import annotations

from dataclasses import dataclass

from app.content.emoji import e


@dataclass(frozen=True)
class ServerPlan:
    code: str
    title: str
    slots: int          # сколько человек всего, включая владельца
    price: int          # ₽ в месяц
    order: int

    @property
    def guests(self) -> int:
        """Сколько друзей можно позвать: владелец занимает один слот."""
        return max(0, self.slots - 1)


# Себестоимость VPS — около 630₽/мес. На пятёрке маржа тонкая, но это
# входной тариф: он существует, чтобы попробовать, а не чтобы зарабатывать.
PLANS: tuple[ServerPlan, ...] = (
    ServerPlan('mini', 'Мини', slots=5, price=990, order=10),
    ServerPlan('company', 'Компания', slots=10, price=1500, order=20),
    ServerPlan('team', 'Команда', slots=15, price=2000, order=30),
)

BY_CODE: dict[str, ServerPlan] = {p.code: p for p in PLANS}

# Статусы заявки и сервера
REQUESTED = 'requested'      # оплачен, ждёт, пока админ поднимет VPS
ACTIVE = 'active'            # работает
SUSPENDED = 'suspended'      # не оплачен, доступ снят, VPS ещё жив
CANCELLED = 'cancelled'      # закрыт совсем

LIVE_STATUSES = (REQUESTED, ACTIVE, SUSPENDED)

STATUS_TITLES = {
    REQUESTED: f'{e("hourglass")} Готовится',
    ACTIVE: f'{e("green")} Работает',
    SUSPENDED: f'{e("warning")} Приостановлен',
    CANCELLED: f'{e("cross")} Закрыт',
}

# Сколько дней держать неоплаченный сервер, прежде чем закрыть совсем.
# VPS всё это время стоит денег, но человек мог просто не заметить списание.
GRACE_DAYS = 3

CHARGE_PERIOD_DAYS = 30


def plan_of(server: dict | None) -> ServerPlan | None:
    return BY_CODE.get((server or {}).get('plan', ''))


def occupied(server: dict | None) -> int:
    """Сколько слотов занято: владелец плюс принятые участники."""
    return 1 + len((server or {}).get('members') or [])


def free_slots(server: dict | None) -> int:
    plan = plan_of(server)
    if not plan:
        return 0
    return max(0, int((server or {}).get('slots') or plan.slots) - occupied(server))


def is_member(server: dict | None, user_id: int) -> bool:
    if (server or {}).get('owner_id') == user_id:
        return True
    return any(m.get('user_id') == user_id
               for m in ((server or {}).get('members') or []))


def gb(traffic_bytes) -> float:
    """Байты панели → гигабайты для экрана."""
    try:
        return round(int(traffic_bytes or 0) / 1024 ** 3, 2)
    except (TypeError, ValueError):
        return 0.0
