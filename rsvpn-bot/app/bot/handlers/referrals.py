"""Реферальная программа и заявки на вывод."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.bot.filters.feature import Feature
from app.bot.keyboards.common import footer
from app.bot.screens.base import Screen, render
from app.bot.screens.profile import profile_caption
from app.domain import ref_tags
from app.domain.referrals import referral_stats
from app.content.emoji import e


async def own_links(c, user_id: int, bot_username: str) -> list[str]:
    """Ссылки человека: именные, если ему их выдали, иначе обычная по id.

    Числовую при наличии именных не показываем: она продолжает работать, но
    на экране от неё только путаница — человек скопирует ту, что короче, и
    метка не посчитается.
    """
    tags = getattr(c, 'ref_tags', None)
    rows = await tags.of_user(user_id) if tags is not None else []
    if rows:
        return [ref_tags.link(bot_username, row['tag']) for row in rows]
    return [f'https://t.me/{bot_username}?start=ref_{user_id}']


async def referrals(call: types.CallbackQuery, c, user: dict, settings):
    stats = referral_stats(c.users.pick(user, 'info.ref_stats', {}))
    percent = round(await settings.rate('bonus.ref_rate') * 100)
    username = await settings.get('link.bot_username')
    links = await own_links(c, call.from_user.id, username)

    text = (
        profile_caption(user, f'{e("referrals")} Реферальная программа')
        + f'<b>{e("link")} ' + ('Ваши ссылки:' if len(links) > 1
                                else 'Ваша ссылка:') + '</b>\n'
        + '\n'.join(f'<code>{link}</code>' for link in links) + '\n\n'
        + f'<b>{e("stats")} Статистика:</b>\n'
          f'— {e("referrals")} Приглашено друзей: <code>{stats.invited}</code>\n'
          f'— {e("speaking")} Активных: <code>{stats.active}</code> '
          f'(<code>{stats.active_percent}%</code>)\n'
          f'— {e("money")} Оплат от друзей: <code>{stats.payments}</code>\n'
          f'— {e("card")} Средний чек: <code>{stats.average_payment} ₽</code>\n'
          f'— {e("growth")} Доход с 1 активного друга: <code>~{stats.per_active_friend} ₽</code>\n'
          f'— {e("payout")} Всего заработано: <code>{stats.earned} ₽</code>\n'
          f'— {e("exchange")} Доступно к выводу: <code>{stats.withdrawable} ₽</code>\n\n'
        + f'<blockquote>{e("referrals")} Вы получаете {percent}% с каждого пополнения '
          f'приглашённого друга — без ограничения по времени и количеству.</blockquote>'
    )

    kb = InlineKeyboardBuilder()
    if await settings.flag('features.payouts_enabled'):
        kb.row(types.InlineKeyboardButton(
            text=f'{e("withdraw")} Вывести средства', callback_data=Menu(screen='payout').pack()))
    await footer(kb, settings, back='profile')

    await render(call, Screen(text=text, markup=kb.as_markup(), image=c.media('referrals')))
    await call.answer()


def create_router() -> Router:
    """Собирает роутер раздела.

    Фабрика, а не модульный синглтон: Router подключается только к одному
    Dispatcher, поэтому синглтон ломает тесты и любой сценарий со вторым ботом.
    Заодно карта «событие → хендлер» видна одним списком.
    """
    router = Router(name='referrals')
    router.callback_query.register(referrals, Menu.filter(F.screen == 'referrals'), Feature('features.referrals_enabled'))
    return router
