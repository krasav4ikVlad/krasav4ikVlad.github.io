"""Первый вход: регистрация, реферальная ссылка, подарок по ссылке.

Регистрация вынесена в отдельную функцию: раньше документ нового пользователя
собирался прямо в хендлере /start, поэтому в форму нельзя было заглянуть,
не читая 80 строк UI-кода, и её нельзя было переиспользовать.
"""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext

from app.bot.handlers.profile import show_profile
from app.core.time import now
from app.content.emoji import e

log = logging.getLogger(__name__)
# «Красивые» ссылки: /start?ref_<slug> вместо цифрового id
REF_ALIASES_SETTING = 'link.ref_aliases'


def new_user_document(tg_user: types.User, referrer, utm: str, start_balance: int) -> dict:
    return {
        'user_data': {
            'user_id': tg_user.id,
            'first_name': tg_user.first_name,
            'last_name': tg_user.last_name,
            'username': tg_user.username,
            'date_joined': now(),
            'referrer': referrer or '',
            'utm': utm,
        },
        'info': {
            'balance': start_balance,
            'email': 'Не привязана',
            'transactions': [],
            'ref_stats': {'withdrawable': 0, 'referrals': [], 'paying_referrals': [],
                          'payments_count': 0, 'turnover_total': 0, 'earned_total': 0,
                          'method': [], 'method_draft': None},
            'gifts': {},
            'support': {'status': 'open', 'thread_id': None},
            'bypass_stats': {'purchases': []},
        },
        'vpn': {'period': 0, 'shortUuid': '', 'uuid': '', 'expireAt': None,
                'createdAt': None, 'hwidDeviceLimit': 2, 'notified': {}},
        'growth': {'joined_at': now(), 'segment': 'new_trial_d0',
                   'has_topup': False, 'topups_count': 0, 'balance': start_balance},
    }


async def resolve_referrer(c, raw: str):
    """Из аргумента /start достаём того, кто привёл. Алиасы — в настройках."""
    if raw.isdigit():
        return int(raw)

    aliases = str(await c.settings.get(REF_ALIASES_SETTING, ''))
    for pair in aliases.split(','):
        if ':' in pair:
            name, user_id = (x.strip() for x in pair.split(':', 1))
            if name == raw and user_id.isdigit():
                return int(user_id)
    return ''


async def start(message: types.Message, command: CommandObject, state: FSMContext,
                c, user: dict | None, settings):
    await state.clear()
    args = command.args or ''

    if user is None:
        user = await register(message.from_user, args, c, settings)

    if args.startswith('gift_'):
        await accept_gift(message, args, c)
        return

    await show_profile(message, c, user, settings)


async def register(tg_user: types.User, args: str, c, settings) -> dict:
    referrer = ''
    if args.startswith('ref_'):
        referrer = await resolve_referrer(c, args[4:])
    elif args.startswith('gift_'):
        parts = args.split('_')
        referrer = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else ''
    elif args.isdigit():
        referrer = int(args)

    # По умолчанию 0: вместо денег новичок получает бесплатный период за
    # подписку на канал. Тумблер features.trial_enabled к балансу отношения
    # больше не имеет — он про кнопку бесплатного периода.
    start_balance = await settings.int('price.start_balance')

    document = new_user_document(tg_user, referrer, args, start_balance)
    await c.users.create(document)

    if referrer:
        await c.users.col.update_one(
            {'user_data.user_id': referrer},
            {'$addToSet': {'info.ref_stats.referrals': tg_user.id}})

    if c.notifier:
        await c.notifier.registered(tg_user.id, username=tg_user.username, utm=args)

    log.info('регистрация %s (utm=%s, реферер=%s)', tg_user.id, args, referrer or '—')
    return document


async def accept_gift(message: types.Message, args: str, c) -> None:
    parts = args.split('_')
    if len(parts) < 4:
        await message.answer(f'{e("cross")} Ссылка на подарок неполная.')
        return

    result = await c.gifts.activate(gift_id=parts[1], plan_code=parts[2],
                                    from_user_id=int(parts[3]),
                                    to_user_id=message.from_user.id)
    if result.ok:
        action = 'продлена' if result.extended else 'активирована'
        await message.answer(
            f'{e("gift")} <b>Подарок принят!</b>\n\nПодписка {action} на {result.days} дней.')
        return

    await message.answer(GIFT_ERRORS.get(result.reason, GIFT_ERRORS['default']))


GIFT_ERRORS = {
    'used': f'{e("cross")} Этот подарок уже активирован.',
    'self': f'{e("cross")} Нельзя принять собственный подарок.',
    'no_funds': f'{e("cross")} У отправителя недостаточно средств.',
    'no_sender': f'{e("cross")} Отправитель подарка не найден.',
    'panel_error': f'{e("cross")} Не удалось выдать подписку. Попробуйте позже.',
    'disabled': f'{e("cross")} Подарки временно недоступны.',
    'default': f'{e("cross")} Подарок недействителен.',
}


async def profile_button(message: types.Message, c, user: dict, settings):
    await show_profile(message, c, user, settings)


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='start')
    router.message.register(start, Command('start'))
    router.message.register(profile_button, F.text.in_({'Профиль', f'{e("user")} Профиль', 'профиль'}))
    return router
