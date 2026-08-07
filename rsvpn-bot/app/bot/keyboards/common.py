"""Клавиатуры, которые повторяются на каждом экране."""

from __future__ import annotations

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Menu
from app.content.emoji import e


async def footer(builder: InlineKeyboardBuilder, settings,
                 back: str | None = 'profile') -> InlineKeyboardBuilder:
    """«Назад / Поддержка / Канал». Ссылки берутся из настроек, не из кода."""
    if back:
        builder.row(types.InlineKeyboardButton(
            text=f'{e("back")} Назад', callback_data=Menu(screen=back).pack()))
    builder.row(types.InlineKeyboardButton(
        text=f'{e("support")} Поддержка', url=await settings.get('link.support')))
    builder.add(types.InlineKeyboardButton(
        text=f'{e("channel")} RS VPN', url=await settings.get('link.channel')))
    return builder


def topup_button() -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=f'{e("card")} Пополнить баланс', callback_data=Menu(screen='payments').pack())


def extend_button() -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=f'{e("renew")} Продлить подписку', callback_data=Menu(screen='extend').pack())


def campaign_keyboards() -> dict:
    """Клавиатуры для рассылок — по ключу из CampaignStep.keyboard."""
    def topup():
        b = InlineKeyboardBuilder()
        b.row(topup_button())
        return b.as_markup()

    def extend():
        b = InlineKeyboardBuilder()
        b.row(extend_button())
        return b.as_markup()

    return {'topup': topup, 'extend': extend, 'none': None}
