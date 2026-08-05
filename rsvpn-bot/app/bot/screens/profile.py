"""Экраны профиля и подписки — сборка текста в одном месте.

Раньше блок «Профиль / Баланс / Друзей / Почта» был скопирован больше двадцати
раз, а блок «Дата окончания / Лимит устройств / Плата» — около десяти. Здесь
это две функции, и правка формулировки правит все экраны сразу.
"""

from __future__ import annotations

from app.content import texts
from app.core.time import fmt


def profile_caption(user: dict, settings_values: dict | None = None) -> str:
    info = user.get('info') or {}
    ref = info.get('ref_stats') or {}
    return texts.render(
        'screen.profile.caption',
        user_id=(user.get('user_data') or {}).get('user_id', ''),
        balance=info.get('balance', 0),
        friends=len(ref.get('referrals') or []),
        email=info.get('email', 'Не привязана'),
    )


def subscription_block(user: dict, price: int, connect_base: str) -> str:
    vpn = user.get('vpn') or {}
    return (
        f'<b>📅 Дата окончания:</b> <code>{fmt(vpn.get("expireAt"))}</code>\n'
        f'<b>📲 Лимит устройств:</b> <code>{vpn.get("hwidDeviceLimit", 0)}</code>\n'
        f'<b>💸 Плата за подписку:</b> <code>{price}₽</code>\n\n'
        f'<b>🔗 Ссылка на подключение:</b> {connect_base}{vpn.get("shortUuid", "")}\n\n'
    )


def devices_block(user: dict, free_limit: int, device_price: int) -> str:
    packages = [p for p in ((user.get('vpn') or {}).get('extraDevices') or [])
                if p.get('active', True)]
    lines = [texts.render('screen.devices.hint',
                          free_devices=free_limit, device_price=device_price)]
    for package in packages:
        lines.append(f'• {package.get("amount")} шт. — следующее списание '
                     f'{fmt(package.get("nextChargeAt"))}')
    return '\n'.join(lines)
