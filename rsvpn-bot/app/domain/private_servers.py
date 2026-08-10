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
    # Сколько таких долей живёт на одной машине. Единица — сервер целиком,
    # больше — долевой тариф: у каждого своя доля со своими участниками,
    # и друг о друге они не знают.
    shares: int = 1

    @property
    def guests(self) -> int:
        """Сколько друзей можно позвать: владелец занимает один слот."""
        return max(0, self.slots - 1)

    @property
    def shared(self) -> bool:
        return self.shares > 1


# Себестоимость VPS — около 630₽/мес. На пятёрке маржа тонкая, но это
# входной тариф: он существует, чтобы попробовать, а не чтобы зарабатывать.
#
# «Доля» дешевле втрое, потому что машина одна на троих. Для человека это
# тот же личный сервер: своя ссылка, свои приглашения, своя статистика.
# Соседей он не видит — ни в участниках, ни в цифрах.
PLANS: tuple[ServerPlan, ...] = (
    ServerPlan('share', 'Доля', slots=6, price=390, order=5, shares=3),
    ServerPlan('mini', 'Мини', slots=5, price=990, order=10),
    ServerPlan('company', 'Компания', slots=10, price=1500, order=20),
    ServerPlan('team', 'Команда', slots=15, price=2000, order=30),
)

BY_CODE: dict[str, ServerPlan] = {p.code: p for p in PLANS}


@dataclass(frozen=True)
class Location:
    code: str
    title: str
    traffic_gb: int = 0          # 0 — безлимит

    @property
    def limited(self) -> bool:
        return self.traffic_gb > 0

    @property
    def traffic_title(self) -> str:
        return f'{self.traffic_gb // 1024} ТБ' if self.limited else 'безлимит'


# Коды короткие: они едут в callback_data вместе с тарифом и профилем, а
# там всего 64 байта на всё.
LOCATIONS: tuple[Location, ...] = (
    Location('ams', 'Амстердам', 1024),
    Location('fra', 'Франкфурт', 1024),
    Location('sto', 'Стокгольм', 1024),
    Location('bud', 'Будапешт', 1024),
    Location('mia', 'Майами', 1024),
    Location('nyc', 'Нью-Йорк', 1024),
    Location('hkg', 'Гонконг', 1024),
    # Безлимитные — отдельные площадки, поэтому Франкфурт встречается дважды
    # и это не опечатка: там другой провайдер и другой тариф по трафику.
    Location('nl', 'Нидерланды'),
    Location('fra2', 'Франкфурт'),
    Location('mil', 'Милан'),
    Location('tyo', 'Токио'),
)

BY_LOCATION: dict[str, Location] = {loc.code: loc for loc in LOCATIONS}
LIMITED = tuple(loc for loc in LOCATIONS if loc.limited)
UNLIMITED = tuple(loc for loc in LOCATIONS if not loc.limited)


@dataclass(frozen=True)
class Profile:
    code: str
    title: str
    hint: str
    # Ставится ли на роутер. Роутеры умеют VLESS через xray, а Hysteria2
    # (QUIC поверх UDP) в их прошивках почти не встречается — обещать его
    # для роутера значит отправить человека к неработающей инструкции.
    router: bool = False


# От профиля зависит скорость и то, как сервер переживает блокировки.
# Выбор осознанный: человек, который берёт свой сервер, обычно знает, что
# ему нужно, а кто не знает — берёт первый, он же и рекомендованный.
PROFILES: tuple[Profile, ...] = (
    Profile('reality', 'TCP Reality',
            'Универсальный. Незаметен для блокировок, стабилен почти везде. '
            'Ставится на роутер', router=True),
    Profile('grpc', 'gRPC',
            'Хорошо проходит там, где режут обычный TCP. Чуть выше задержка. '
            'Ставится на роутер', router=True),
    Profile('hysteria2', 'Hysteria2',
            'Самый быстрый на плохих каналах и мобильном интернете. '
            'Иногда режется провайдером'),
)

BY_PROFILE: dict[str, Profile] = {p.code: p for p in PROFILES}
DEFAULT_PROFILE = 'reality'

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

# За сколько дней предупреждать о ежемесячном списании. Полторы тысячи с
# баланса без предупреждения — это «бот украл деньги» в поддержке, даже
# когда всё по договорённости.
WARN_DAYS = 3


def plan_of(server: dict | None) -> ServerPlan | None:
    return BY_CODE.get((server or {}).get('plan', ''))


def location_of(server: dict | None) -> Location | None:
    return BY_LOCATION.get((server or {}).get('location', ''))


def profile_of(server: dict | None) -> Profile | None:
    return BY_PROFILE.get((server or {}).get('profile', ''))


def location_title(server: dict | None) -> str:
    location = location_of(server)
    return location.title if location else '—'


def profile_title(server: dict | None) -> str:
    profile = profile_of(server)
    return profile.title if profile else '—'


def is_shared(server: dict | None) -> bool:
    """Долевой сервер: машина одна, владельцев несколько."""
    plan = plan_of(server)
    return bool(plan and plan.shared)


def shares_of(server: dict | None) -> int:
    """Сколько долей помещается на машину этого сервера."""
    plan = plan_of(server)
    return plan.shares if plan else 1


def router_ready(server: dict | None) -> bool:
    profile = profile_of(server)
    return bool(profile and profile.router)


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
    """Байты панели → гигабайты. Для сравнения с квотой площадки."""
    try:
        return round(int(traffic_bytes or 0) / 1024 ** 3, 2)
    except (TypeError, ValueError):
        return 0.0


UNITS = (('ТБ', 1024 ** 4), ('ГБ', 1024 ** 3), ('МБ', 1024 ** 2), ('КБ', 1024))


def traffic(traffic_bytes) -> str:
    """Байты → человеческая строка.

    Гигабайты в чистом виде не годятся: 5 МБ округляются до 0.0, и экран
    показывает нули там, где трафик есть. На новом сервере первые дни это
    единственные цифры, которые вообще видны.
    """
    try:
        value = int(traffic_bytes or 0)
    except (TypeError, ValueError):
        return '0 Б'

    for title, size in UNITS:
        if value >= size:
            return f'{value / size:.2f}'.rstrip('0').rstrip('.') + f' {title}'
    return f'{value} Б'
