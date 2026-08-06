"""Экраны профиля и подписки — сборка текста в одном месте.

Раньше блок «Профиль / Баланс / Друзей / Почта» был скопирован больше двадцати
раз, а блок «Дата окончания / Лимит устройств / Плата» — около десяти. Здесь
это функции, и правка формулировки правит все экраны сразу.
"""

from __future__ import annotations

from app.content import texts
from app.core.time import fmt, parse_dt


def profile_caption(user: dict, title: str = '👤 Профиль') -> str:
    info = user.get('info') or {}
    ref = info.get('ref_stats') or {}
    return (
        f'<b>{title}</b>\n\n'
        f'<b>🆔 Идентификатор:</b> <code>{(user.get("user_data") or {}).get("user_id", "")}</code>\n'
        f'<b>💰 Баланс:</b> <code>{info.get("balance", 0)}₽</code>\n'
        f'<b>👥 Друзей:</b> <code>{len(ref.get("referrals") or [])}</code>\n'
        f'<b>✉️ Почта:</b> <code>{info.get("email", "Не привязана")}</code>\n\n'
    )


def period_label(days: int) -> str:
    """«месяц», «3 месяца» — как в старом боте, чтобы цена читалась привычно."""
    return {1: 'день', 30: 'месяц', 90: '3 месяца', 365: 'год', 1095: '3 года'}.get(
        int(days or 0), f'{days} дн.')


def price_line(plan_price: int, days: int, devices_price: int) -> str:
    """«150₽ за месяц + 1125₽/мес за устройства»."""
    text = f'<code>{plan_price}₽ за {period_label(days)}</code>'
    if devices_price:
        text += f' + <code>{devices_price}₽/мес</code> за устройства'
    return text


def subscription_block(user: dict, price: str, connect_base: str) -> str:
    vpn = user.get('vpn') or {}
    return (
        f'<b>📅 Дата окончания:</b> <code>{fmt(vpn.get("expireAt"))}</code>\n'
        f'<b>📲 Лимит устройств:</b> <code>{vpn.get("hwidDeviceLimit", 0)}</code>\n'
        f'<b>💸 Плата за подписку:</b> {price}\n\n'
        f'<b>🔗 Ссылка на подключение:</b> {connect_base}{vpn.get("shortUuid", "")}\n\n'
    )


def extra_devices_list(user: dict) -> str:
    """Список пакетов доп. устройств с датами следующего списания."""
    packages = [p for p in ((user.get('vpn') or {}).get('extraDevices') or [])
                if p.get('active', True) and int(p.get('amount', 0) or 0) > 0]
    if not packages:
        return 'Нет дополнительных устройств'

    packages.sort(key=lambda p: parse_dt(p.get('nextChargeAt')) or fmt(None))
    return '\n'.join(f'• {p.get("amount", 0)} шт. до {fmt(p.get("nextChargeAt"))}'
                     for p in packages)


def devices_block(user: dict, price: str, connect_base: str) -> str:
    vpn = user.get('vpn') or {}
    return (
        f'<b>📅 Дата окончания:</b> <code>{fmt(vpn.get("expireAt"))}</code>\n'
        f'<b>📲 Лимит устройств:</b> <code>{vpn.get("hwidDeviceLimit", 0)}</code>\n'
        f'<b>📦 Доп. устройства:</b>\n{extra_devices_list(user)}\n'
        f'<b>💸 Плата за подписку:</b> {price}\n\n'
        f'<b>🔗 Ссылка на подключение:</b> {connect_base}{vpn.get("shortUuid", "")}\n\n'
    )


def gifts_block(gifts: dict, labels: dict[str, str]) -> str:
    """«1 День: 22 шт.» — человеческие названия вместо кодов тарифов."""
    lines = []
    for code, count in (gifts or {}).items():
        lines.append(f'{labels.get(code, code)}: {int(count or 0)} шт.')
    return '\n'.join(lines) if lines else 'Пока нет'
