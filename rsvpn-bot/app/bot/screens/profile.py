"""Экраны профиля и подписки — сборка текста в одном месте.

Раньше блок «Профиль / Баланс / Друзей / Почта» был скопирован больше двадцати
раз, а блок «Дата окончания / Лимит устройств / Плата» — около десяти. Здесь
это функции, и правка формулировки правит все экраны сразу.
"""

from __future__ import annotations

from app.content import texts
from app.content.emoji import e
from app.core.time import fmt, parse_dt


def profile_caption(user: dict, title: str = '') -> str:
    info = user.get('info') or {}
    ref = info.get('ref_stats') or {}
    heading = title or f'{e("user")} Профиль'
    return (
        f'<b>{heading}</b>\n\n'
        f'<b>{e("id")} Идентификатор:</b> '
        f'<code>{(user.get("user_data") or {}).get("user_id", "")}</code>\n'
        f'<b>{e("money")} Баланс:</b> <code>{info.get("balance", 0)}₽</code>\n'
        f'<b>{e("friends")} Друзей:</b> <code>{len(ref.get("referrals") or [])}</code>\n'
        f'<b>{e("email")} Почта:</b> <code>{info.get("email", "Не привязана")}</code>\n\n'
    )


def period_label(days: int) -> str:
    """«месяц», «3 месяца» — как в старом боте, чтобы цена читалась привычно."""
    return {1: 'день', 30: 'месяц', 90: '3 месяца', 365: 'год', 1095: '3 года'}.get(
        int(days or 0), f'{days} дн.')


def price_line(plan_price: int, days: int, devices_price: int,
               full_price: int = 0) -> str:
    """«150₽ за месяц» плюс, если есть, отдельная строка про устройства.

    Раньше это была одна строка «150₽ за месяц + 1125₽/мес за устройства»:
    два <code>-блока с текстом между ними рвали фразу, а на телефоне она ещё
    и переносилась посередине. Складывать их в одно число тоже нельзя —
    это разные циклы: тариф списывается раз в период, устройства всегда
    раз в 30 дней, и на дневном тарифе сумма была бы бессмыслицей.

    full_price — цена без скидки. Если она больше, рядом встаёт зачёркнутая
    старая и размер скидки: человек должен видеть, что цена низкая не по
    ошибке. Вне <code>: внутри него разметка не разбирается.
    """
    if full_price and full_price > plan_price:
        percent = round((1 - plan_price / full_price) * 100)
        line = (f'<code>{plan_price}₽ за {period_label(days)}</code> '
                f'вместо <s>{full_price}₽</s> {e("hot")} −{percent}%')
    else:
        line = f'<code>{plan_price}₽ за {period_label(days)}</code>'
    if devices_price:
        line += (f'\n<b>{e("devices")} Плата за устройства:</b> '
                 f'<code>{devices_price}₽ в месяц</code>')
    return line


def subscription_block(user: dict, price: str, connect_base: str) -> str:
    vpn = user.get('vpn') or {}
    return (
        f'<b>{e("calendar")} Дата окончания:</b> <code>{fmt(vpn.get("expireAt"))}</code>\n'
        f'<b>{e("devices")} Лимит устройств:</b> <code>{vpn.get("hwidDeviceLimit", 0)}</code>\n'
        f'<b>{e("payout")} Плата за подписку:</b> {price}\n\n'
        f'<b>{e("link")} Ссылка на подключение:</b> {connect_base}{vpn.get("shortUuid", "")}\n\n'
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
        f'<b>{e("calendar")} Дата окончания:</b> <code>{fmt(vpn.get("expireAt"))}</code>\n'
        f'<b>{e("devices")} Лимит устройств:</b> <code>{vpn.get("hwidDeviceLimit", 0)}</code>\n'
        f'<b>{e("devices")} Доп. устройства:</b>\n{extra_devices_list(user)}\n'
        f'<b>{e("payout")} Плата за подписку:</b> {price}\n\n'
        f'<b>{e("link")} Ссылка на подключение:</b> {connect_base}{vpn.get("shortUuid", "")}\n\n'
    )


def gifts_block(gifts: dict, labels: dict[str, str]) -> str:
    """«1 месяц: 22 шт.» — человеческие названия вместо кодов тарифов.

    Нулевые остатки не показываются: ключ в документе остаётся навсегда после
    того, как подарок потратили, и строка «1 месяц: 0 шт.» выглядела как
    доступный подарок, которого нет.
    """
    lines = []
    for code, count in (gifts or {}).items():
        try:
            amount = int(count or 0)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            lines.append(f'{labels.get(code, code)}: {amount} шт.')
    return '\n'.join(lines) if lines else 'Пока нет'
