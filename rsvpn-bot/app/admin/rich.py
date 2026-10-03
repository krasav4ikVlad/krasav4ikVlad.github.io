"""`/rich` — посмотреть, как главное меню выглядело бы Rich Message.

Команда ничего не меняет в боте: это примерка. Сообщение приходит тому,
кто её набрал, с его настоящими данными и рабочими кнопками — нажатие
ведёт в тот же раздел, что и обычное меню.

    /rich        главное меню
    /rich sub    действующая подписка
    /rich pay    пополнение баланса
    /rich all    все возможности разом, чтобы выбрать нужные

Если клиент или сервер Telegram ещё не умеет Rich Messages, команда
покажет ответ API словами, а не промолчит: именно это и надо выяснить
до того, как переводить на такой вид весь бот.

Два места, где примерка отличается от обычного экрана
─────────────────────────────────────────────────────
* Кастомные эмодзи. У sendRichMessage нет поля parse_mode, и middleware
  сессии (app/bot/middlewares/emoji.py) такое сообщение не трогает —
  значки навешиваются здесь, вызовом decorate().
* Картинка. Она уезжает отдельным списком media, а в HTML стоит ссылкой
  tg://photo?id=. Если Telegram её не примет, экран отправится второй
  попыткой без шапки: вёрстку посмотреть важнее, чем картинку.
"""

from __future__ import annotations

import logging

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import InputMediaPhoto, InputRichMessage, InputRichMessageMedia

from app.bot.callbacks import Payment
from app.bot.screens import rich_menu
from app.bot.screens.pricing import price_parts_for, price_tag
from app.bot.screens.profile import period_label
from app.content.emoji import decorate, e

log = logging.getLogger(__name__)

# Как позвали экран → какой это экран. Русские слова тоже: команду зовут
# с телефона, и переключать раскладку ради «sub» никто не станет.
ALIASES = {
    '': '', 'menu': '', 'меню': '', 'профиль': '',
    'sub': 'sub', 'подписка': 'sub', 'моя': 'sub',
    'pay': 'pay', 'оплата': 'pay', 'пополнение': 'pay', 'баланс': 'pay',
    'all': 'all', 'всё': 'all', 'все': 'all',
    'help': 'help', '?': 'help', 'помощь': 'help',
}

HELP = (
    f'{e("design")} <b>Примерка Rich Message</b>\n\n'
    f'<code>/rich</code> — главное меню на новый лад\n'
    f'<code>/rich sub</code> — действующая подписка\n'
    f'<code>/rich pay</code> — пополнение баланса\n'
    f'<code>/rich all</code> — все элементы разом\n\n'
    f'<blockquote>Это одно текстовое сообщение: его можно копировать, '
    f'переводить и читать с экрана — в отличие от картинки. Кнопки внутри '
    f'настоящие и ведут в те же разделы.</blockquote>'
)


async def rich_preview(message: types.Message, command: CommandObject, c,
                       settings) -> None:
    what = ALIASES.get((command.args or '').strip().lower(), '')
    if what == 'help':
        await message.answer(HELP)
        return

    if what == 'all':
        tries = [(rich_menu.showcase(), [], '')]
    else:
        tries = await attempts(what, message.from_user.id, c, settings)

    # Отказ запоминается в переменную: имя из `except ... as` после блока
    # уже не существует, а показать его надо в самом конце.
    failure: BaseException | None = None
    for html, media, note in tries:
        try:
            await _send(message, html, media)
        except Exception as exc:      # noqa: BLE001 — ответ API важнее красоты
            failure = exc
            log.warning('rich-сообщение не ушло: %s', exc)
            continue
        if note:
            await message.answer(note)
        return

    await message.answer(
        f'{e("cross")} <b>Telegram не принял Rich Message</b>\n'
        f'<code>{_shorten(failure)}</code>\n\n'
        f'<blockquote>Нужны Bot API 10.3+ на стороне Telegram и '
        f'свежий клиент. Если в ответе «method not found» — дело в '
        f'версии aiogram, она должна быть 3.31 и выше.</blockquote>')


def _shorten(exc: BaseException | None) -> str:
    return str(exc)[:600] if exc else 'без объяснения'


async def _send(message: types.Message, html: str, media: list) -> None:
    """Отправка с кастомными эмодзи: middleware сессии сюда не достаёт."""
    await message.bot.send_rich_message(
        chat_id=message.chat.id,
        rich_message=InputRichMessage(html=decorate(html),
                                      media=media or None))


async def attempts(what: str, user_id: int, c,
                   settings) -> list[tuple[str, list, str]]:
    """Что пробовать отправить, от красивого к надёжному.

    Две вещи на экране новые для API и могут не пройти: картинка (едет
    списком media) и таблица с кнопкой в ячейке — документация разрешает
    в ячейках только строчное оформление. Отказ приходит один на всё
    сообщение, и по тексту не всегда видно, что именно не понравилось,
    поэтому следующие попытки снимают сначала одно, потом другое. Человек
    видит экран и приписку, чего в нём не хватает, — вместо пустоты.
    """
    key, maker = SCREENS[what]
    user = await c.users.get(user_id) or {}
    build = await maker(user, c, settings)

    # Та же картинка, что на обычном экране, и через тот же кэш file_id:
    # примерка не должна заливать PNG заново на каждый вызов.
    photo = c.media(key)
    media = []
    if photo:
        media.append(InputRichMessageMedia(
            id=rich_menu.PHOTO, media=InputMediaPhoto(media=photo.as_input())))

    def html(*, boxed: bool, picture: bool) -> str:
        return build(boxed=boxed,
                     photo=rich_menu.PHOTO_LINK if (picture and media) else '')

    no_photo = (f'{e("attention")} <b>Картинку Telegram не принял</b>, '
                f'остальное — выше.')
    no_table = (f'{e("attention")} <b>Таблицу Telegram не принял</b> — '
                f'строки выше собраны переносами.')

    tries = [(html(boxed=True, picture=True), media, '')]
    if media:
        tries.append((html(boxed=True, picture=False), [], no_photo))
    tries.append((html(boxed=False, picture=True), media, no_table))
    if media:
        tries.append((html(boxed=False, picture=False), [],
                      f'{no_table}\n{no_photo}'))
    return tries


async def price_cells(c, user: dict) -> tuple[str, str]:
    """Цена тарифа и плата за устройства — строками, годными для ячейки.

    Обычный экран собирает их в одну фразу с переносом строки; в таблице
    перенос не нужен, а числа те же самые и считаются там же.

    Цена складывается из тарифа, скидки аудитории и правил пополнения, и
    любой из этих кусков может быть не настроен — на боте без ключей
    оплаты так и есть. Примерка из-за этого молчать не должна: строки с
    ценой просто не будет, а остальной экран приедет.
    """
    try:
        parts = await price_parts_for(c, user)
    except Exception as exc:      # noqa: BLE001
        log.warning('цена для примерки не собралась: %s', exc)
        return '', ''
    price = ''
    if parts['days']:
        price = (f'{price_tag(parts["full"], parts["price"])} '
                 f'за {period_label(parts["days"])}')
    return price, (f'{parts["devices"]}₽ в месяц' if parts['devices'] else '')


async def menu_screen(user: dict, c, settings):
    """Главное меню: профиль, подписка одной строкой, разделы."""
    from app.bot.handlers.raffle import running

    info = user.get('info') or {}
    stats = info.get('ref_stats') or {}
    limit = int(c.users.pick(user, 'vpn.hwidDeviceLimit', 0) or 0)
    support = str(await settings.get('link.support') or '')
    raffle = await running(settings)

    def build(*, boxed: bool, photo: str) -> str:
        return rich_menu.main_menu(
            user,
            balance=int(info.get('balance') or 0),
            friends=len(stats.get('referrals') or []),
            devices=f'до {limit}' if limit else '',
            email=str(info.get('email') or ''),
            support_url=support,
            raffle=raffle,
            photo=photo, boxed=boxed)

    return build


async def subscription_screen(user: dict, c, settings):
    """Действующая подписка: сроки, устройства, цены, ссылка, инструкция."""
    vpn = user.get('vpn') or {}
    support = str(await settings.get('link.support') or '')
    price, devices_fee = await price_cells(c, user)

    connect = ''
    if vpn.get('shortUuid'):
        connect = (f'{await settings.get("link.connect_base")}'
                   f'{vpn["shortUuid"]}')

    def build(*, boxed: bool, photo: str) -> str:
        return rich_menu.my_subscription(
            user,
            devices=int(vpn.get('hwidDeviceLimit') or 0),
            price=price, devices_fee=devices_fee, connect=connect,
            support_url=support, photo=photo, boxed=boxed)

    return build


async def topup_screen(user: dict, c, settings):
    """Пополнение: способы таблицей, бонусы маркером, вопросы в блоке."""
    from app.bot.handlers.payments import bonus_offer

    info = user.get('info') or {}
    support = str(await settings.get('link.support') or '')
    providers = await c.payments.available() if c.payments else []

    methods = []
    for provider in providers:
        pay = (rich_menu.link('Оплатить', provider.direct_url, 'success')
               if provider.direct_url else
               rich_menu.action('Оплатить',
                                Payment(provider=provider.code).pack(),
                                'success'))
        methods.append({'title': provider.title, 'fee': provider.fee,
                        'speed': provider.speed, 'button': pay})

    # Проценты считает не экран: он берёт их там же, где начисление, —
    # обещать невыплаченный рубль нельзя.
    offer = await bonus_offer(user, settings)
    bonus = (f'{e("gift")} При пополнении — +{offer["percent"]}% сверху'
             if offer['percent'] > 0 else '')

    extra = round(await settings.rate('bonus.tribute_extra_rate') * 100)
    tribute = ''
    if extra > 0 and any(p.code.startswith('tribute') for p in providers):
        tribute = f'{e("hot")} Оплата через Tribute — ещё +{extra}% сверху'

    price = (await price_cells(c, user))[0] if c.users.pick(
        user, 'vpn.shortUuid') else ''

    def build(*, boxed: bool, photo: str) -> str:
        return rich_menu.topup(
            user,
            balance=int(info.get('balance') or 0),
            price=price, methods=methods, bonus=bonus,
            tribute_bonus=tribute, support_url=support,
            photo=photo, boxed=boxed)

    return build


# Какой экран примеряем: аргумент команды → картинка и сборщик.
SCREENS = {
    '': ('profile', menu_screen),
    'sub': ('subscription_active', subscription_screen),
    'pay': ('payment', topup_screen),
}


def register(router: Router) -> None:
    router.message.register(rich_preview, Command('rich'))
