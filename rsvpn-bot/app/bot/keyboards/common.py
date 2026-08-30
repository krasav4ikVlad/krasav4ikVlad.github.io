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


def subscription_button() -> types.InlineKeyboardMarkup:
    """«Моя подписка» — куда идти сразу после пополнения."""
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text=f'{e("shield")} Моя подписка',
        callback_data=Menu(screen='my_subscription').pack()))
    return builder.as_markup()


def extend_button() -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(
        text=f'{e("renew")} Продлить подписку', callback_data=Menu(screen='extend').pack())


async def broadcast_keyboard(settings) -> types.InlineKeyboardMarkup:
    """Две кнопки под каждым письмом рассылки: подписка и канал.

    Письмо без кнопок — тупик: человек прочитал и закрыл. «Ваша подписка»
    ведёт туда, где видно срок и кнопку продления, канал — туда, где
    новости и статус узлов. Ссылка на канал берётся из настроек, а не из
    кода: она уже есть в подвале всех экранов, и второе место, где её надо
    не забыть поменять, однажды разъедется с первым.
    """
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text=f'{e("shield")} Ваша подписка',
        callback_data=Menu(screen='my_subscription').pack()))

    channel = str(await settings.get('link.channel') or '').strip()
    if channel:
        builder.row(types.InlineKeyboardButton(
            text=f'{e("channel")} Новости RS VPN', url=channel))
    return builder.as_markup()


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
