"""Справочник команд — чтобы не держать их в голове.

Команд у админа больше десятка, часть с аргументами, и половина
вспоминается ровно в тот момент, когда нужна срочно. Список лежит здесь
одной таблицей: добавили команду — дописали строку, и она сразу видна
в админке.

Таблица не выводится из роутеров нарочно: у обработчика нет ни примера
вызова, ни объяснения, когда команда пригодится, а без них список
бесполезен.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import Admin as Adm
from app.content.emoji import e


@dataclass(frozen=True)
class Cmd:
    usage: str          # как вызывать, с аргументами
    what: str           # что делает
    when: str = ''      # когда пригодится


@dataclass(frozen=True)
class Section:
    title: str
    items: tuple[Cmd, ...]


SECTIONS: tuple[Section, ...] = (
    Section(f'{e("settings")} Управление', (
        Cmd('/admin', 'Панель: настройки, тарифы, тексты, рассылка, скидки',
            'Всё, что меняется без выкладки, — отсюда'),
        Cmd('/diag', 'Что из фонового отработало и когда',
            'Первое, куда идти при «автопродление не работает» или '
            '«не приходят напоминания». Там же кнопка проверки напоминаний '
            'и текущие скидки с бонусами'),
        Cmd('/bypasssync', 'У кого ByPass разошёлся с подпиской',
            'Сверяет срок и лимит устройств: и то и другое у ByPass своё. '
            'Показ без правки; с аргументом <code>fix</code> выравнивает. '
            'То же делает фоновая задача раз в шесть часов — команда нужна, '
            'когда ждать некогда'),
    )),

    Section(f'{e("user")} Пользователи', (
        Cmd('/ban 123456789', 'Закрыть доступ к боту',
            'Подписка продолжает работать до конца оплаченного срока'),
        Cmd('/hardban 123456789', 'Закрыть доступ и отключить подписку в панели',
            'Когда конфиг нужно погасить сразу, а не по истечении'),
        Cmd('/unban 123456789', 'Снять блокировку'),
        Cmd('/deluser 123456789', 'Удалить из базы вместе с подпиской в панели',
            'Для своих тестовых аккаунтов: после удаления человек заходит '
            'как новый и снова видит бесплатный период'),
    )),

    Section(f'{e("private")} Личные серверы', (
        Cmd('/queue', 'Очередь заявок и запас машин',
            'Сколько заявок ждёт, что и сколько поднять, какие машины '
            'свободны. То же самое — кнопкой в /admin'),
        Cmd('/pooladd UUID', 'Добавить поднятую машину в запас',
            'Локацию и протокол бот определяет по панели сам; если ответ '
            'неоднозначный — попросит уточнить: <code>/pooladd UUID nl '
            'reality</code>. Заявка на такую машину выдаётся сама в момент '
            'покупки, а если заявка уже ждала — в момент добавления'),
        Cmd('/pooldel UUID', 'Убрать машину из запаса',
            'Уже выданные серверы это не трогает'),
        Cmd('/servers', 'Все серверы: владельцы, места, локации, выручка в месяц',
            'У долевых видно, сколько долей на машине и какая это машина'),
        Cmd('/squad srv_xxxxxxxx UUID', 'Выдать сервер: привязать сквад к заявке',
            'То же, что кнопка «Выдать сервер» под заявкой. В группе работает '
            'только команда: обычный текст боты там не получают. Долю сажайте '
            'на уже поднятую машину — её UUID бот показывает прямо в заявке'),
        Cmd('/srvloc srv_xxxxxxxx ams reality',
            'Проставить площадку и протокол задним числом',
            'Серверам, заведённым до того, как появился выбор локации. '
            'Коды: ams fra sto bud mia nyc hkg (1 ТБ), nl fra2 mil tyo (безлимит)'),
        Cmd('/srvdel srv_xxxxxxxx', 'Удалить сервер: снять доступ и стереть запись',
            'Принимает и id владельца, и @username. Второй аргумент '
            '<code>refund</code> вернёт деньги на баланс. Для тестов: второй '
            'сервер одному человеку бот не продаст, пока жив первый. VPS в '
            'панели остаётся — гасите руками'),
        Cmd('/srvdiag srv_xxxxxxxx',
            'Что бот знает о сервере и что отвечает панель',
            'Когда статистика показывает нули: видно uuid, выдан ли сквад, '
            'источник цифр и сырой ответ панели'),
    )),

    Section(f'{e("user")} Для всех', (
        Cmd('/start', 'Профиль. С аргументом — приглашение, подарок или сервер'),
        Cmd('/privacy', 'Политика конфиденциальности'),
    )),
)


def text() -> str:
    lines = [f'<b>{e("clipboard")} Команды бота</b>', '']
    for section in SECTIONS:
        lines.append(f'<b>{section.title}</b>')
        for cmd in section.items:
            lines.append(f'<code>{cmd.usage}</code>')
            lines.append(f'   {cmd.what}'
                         + (f'\n   <i>{cmd.when}</i>' if cmd.when else ''))
        lines.append('')

    lines.append('<blockquote>Команды видны только админам: у остальных бот '
                 'на них не отвечает. Аргумент с пользователем принимает и id, '
                 'и @username.</blockquote>')
    return '\n'.join(lines)


async def screen(call: types.CallbackQuery, c, settings) -> None:
    from app.admin.panel import edit

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(
        text=f'{e("back")} Назад', callback_data=Adm(act='main').pack()))
    await edit(call, text(), kb)


async def command(message: types.Message, c, settings) -> None:
    await message.answer(text())


def register(router: Router) -> None:
    router.message.register(command, Command('commands'))
    router.message.register(command, Command('help'))
    router.callback_query.register(screen, Adm.filter(F.act == 'cmds'))
