"""Значки: реестр вместо символа в строке.

Главный здесь — test_no_raw_emoji_anywhere_in_the_code. Он проходит по всем
файлам app/ и падает, если хоть один значок написан прямо в строке. Без
такого сторожа «все эмодзи в одном месте» держится ровно до первой правки:
поставить символ руками быстрее, чем вспомнить имя, и реестр расползается
обратно.
"""

import pathlib
import re
import tokenize

import pytest

from app.content import emoji
from app.content.emoji import BY_CHAR, EMOJI, decorate, e, missing_ids

APP = pathlib.Path(__file__).resolve().parents[1] / 'app'

# Диапазоны символов, которые Telegram считает эмодзи. Стрелки (→, ↳),
# рамки (─) и прочая типографика сюда намеренно не входят: это не значки,
# кастомных вариантов у них не бывает.
EMOJI_RE = re.compile('[\U0001F000-\U0001FAFF☀-➿⬀-⯿]')


@pytest.fixture(autouse=True)
def enabled():
    emoji.set_enabled(True)
    yield
    emoji.set_enabled(True)


def string_literals(path: pathlib.Path):
    """Строковые литералы файла, кроме докстрок — те комментарии, а не текст."""
    with path.open(encoding='utf-8') as handle:
        try:
            tokens = list(tokenize.generate_tokens(handle.readline))
        except tokenize.TokenError:
            return

    for token in tokens:
        if token.type != tokenize.STRING:
            continue
        prefix = re.match(r'[a-zA-Z]*', token.string).group(0)
        body = token.string[len(prefix):]
        if body.startswith(('"""', "'''")):
            continue
        yield token.start[0], token.string


def test_no_raw_emoji_anywhere_in_the_code():
    """Единственное место, где значок написан символом, — сам реестр."""
    offenders = []
    for path in sorted(APP.rglob('*.py')):
        if path.name == 'emoji.py':
            continue
        for line_no, literal in string_literals(path):
            if EMOJI_RE.search(literal):
                offenders.append(f'{path.relative_to(APP.parent)}:{line_no}  {literal[:60]}')

    assert not offenders, (
        'значки написаны мимо реестра — используйте e("имя") '
        'из app/content/emoji.py:\n' + '\n'.join(offenders))


# ── реестр ──────────────────────────────────────────────────────────────────
def test_e_returns_a_plain_character():
    """Тег навешивается на выходе из бота, а не здесь: в подписи кнопки
    разметка не работает, и готовый тег показался бы буквами."""
    assert e('money') == '💰'
    assert '<' not in e('back')


def test_unknown_name_does_not_raise():
    """Опечатка должна быть видна на экране, а не падать посреди отрисовки."""
    assert e('нет такого имени') == 'нет такого имени'


def test_registry_has_no_duplicate_ids():
    """Один id на два значка — это copy-paste: покажется чужая картинка."""
    ids = [emoji_id for _, emoji_id in EMOJI.values() if emoji_id]
    assert len(ids) == len(set(ids))


def test_every_entry_has_a_character():
    for name, (char, _) in EMOJI.items():
        assert char.strip(), f'у «{name}» нет значка — покажется пустое место'


def test_missing_ids_are_listed():
    assert 'back' not in missing_ids()
    assert set(missing_ids()) <= set(EMOJI)


# ── подстановка на выходе ───────────────────────────────────────────────────
def test_known_character_becomes_a_tag():
    assert decorate('💰 Баланс') == (
        '<tg-emoji emoji-id="5317017827088049911">💰</tg-emoji> Баланс')


def test_character_without_id_stays_as_is():
    assert decorate('📊 Статистика') == '📊 Статистика'


def test_toggle_turns_everything_back_into_plain_characters():
    emoji.set_enabled(False)
    assert decorate('💰 Баланс') == '💰 Баланс'


def test_code_blocks_are_left_alone():
    """Вложить tg-emoji внутрь <code> нельзя — Telegram отклонит сообщение."""
    text = decorate('💰 <code>тут 💰 не трогаем</code> 💰')

    assert text.count('<tg-emoji') == 2
    assert '<code>тут 💰 не трогаем</code>' in text


def test_modifier_is_not_torn_off():
    """«⚠️» — это «⚠» плюс модификатор. Разорвать их значит показать
    чёрно-белый значок вместо цветного."""
    assert decorate('⚠️') in ('⚠️', '<tg-emoji emoji-id="">⚠️</tg-emoji>')
    assert '⚠️' in decorate('⚠️ Внимание')


def test_empty_text_is_safe():
    assert decorate('') == ''


# ── связка с сообщениями ────────────────────────────────────────────────────
def test_text_templates_use_names_not_characters():
    """В шаблонах значки — плейсхолдеры {gift}, подставляются при отрисовке."""
    from app.content import texts

    assert '{gift}' in texts.REGISTRY['campaign.expired.d3'].default
    assert '🎁' in texts.render('campaign.expired.d3', credited=100, total=200)


def test_profile_screen_goes_through_the_registry():
    from app.bot.screens.profile import profile_caption

    caption = profile_caption({'user_data': {'user_id': 5}, 'info': {'balance': 0}})
    assert e('id') in caption
    assert '<tg-emoji' not in caption      # тег появится только при отправке


async def test_middleware_decorates_outgoing_text():
    from aiogram.methods import SendMessage

    from app.bot.middlewares.emoji import emoji_middleware

    sent = []

    async def make_request(bot, method):
        sent.append(method)
        return None

    await emoji_middleware(make_request, None,
                           SendMessage(chat_id=1, text=f'{e("money")} Баланс'))

    assert sent[0].text.startswith('<tg-emoji')


async def send(method):
    """Прогнать метод через middleware, как это сделает сессия бота."""
    from app.bot.middlewares.emoji import emoji_middleware

    async def make_request(bot, m):
        return None

    await emoji_middleware(make_request, None, method)
    return method


def button(text: str):
    from aiogram.methods import SendMessage
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return SendMessage(chat_id=1, text='привет', reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data='x')]]))


async def test_button_label_gets_an_icon():
    """В подписи кнопки HTML не работает, зато работает icon_custom_emoji_id:
    значок уезжает туда, а из текста убирается — иначе покажется дважды."""
    method = await send(button(f'{e("back")} Назад'))
    btn = method.reply_markup.inline_keyboard[0][0]

    assert btn.icon_custom_emoji_id == '5321133913291135005'
    assert btn.text == 'Назад'


async def test_button_without_id_keeps_its_character():
    """Иконки нет — обычный значок лучше, чем голая подпись."""
    method = await send(button(f'{e("stats")} Статистика'))
    btn = method.reply_markup.inline_keyboard[0][0]

    assert btn.icon_custom_emoji_id is None
    assert btn.text == '📊 Статистика'


async def test_emoji_in_the_middle_of_a_label_stays_put():
    """Иконка рисуется слева от подписи — переносить туда значок из
    середины строки значит переставить его местами со словами."""
    method = await send(button(f'Ежедневная {e("gift")} — 6₽'))
    btn = method.reply_markup.inline_keyboard[0][0]

    assert btn.icon_custom_emoji_id is None
    assert btn.text == 'Ежедневная 🎁 — 6₽'


async def test_button_labels_are_not_given_html():
    method = await send(button(f'{e("money")} Баланс'))

    assert '<tg-emoji' not in method.reply_markup.inline_keyboard[0][0].text


async def test_popup_answer_is_left_alone():
    """У answerCallbackQuery нет parse_mode: тег показался бы буквами."""
    from aiogram.methods import AnswerCallbackQuery

    method = await send(AnswerCallbackQuery(
        callback_query_id='1', text=f'{e("money")} Баланс пополнен'))

    assert method.text == '💰 Баланс пополнен'


async def test_photo_caption_is_decorated():
    """Экраны бота — фото с подписью, а не текст."""
    from aiogram.methods import SendPhoto

    method = await send(SendPhoto(chat_id=1, photo='id',
                                  caption=f'{e("money")} Баланс'))

    assert method.caption.startswith('<tg-emoji')


async def test_caption_inside_media_is_decorated(caplog):
    """При смене экрана подпись лежит на уровень глубже — в media.

    Проверка на лог не лишняя: InputMediaPhoto заморожен, и присваивание
    поля молча уходило в except — подписи экранов оставались обычными.
    """
    from aiogram.methods import EditMessageMedia
    from aiogram.types import InputMediaPhoto

    method = await send(EditMessageMedia(
        chat_id=1, message_id=1,
        media=InputMediaPhoto(media='id', caption=f'{e("money")} Баланс')))

    assert method.media.caption.startswith('<tg-emoji')
    assert not caplog.records, 'оформление свалилось в except'


async def test_toggle_switches_off_the_whole_middleware():
    emoji.set_enabled(False)
    method = await send(button(f'{e("back")} Назад'))
    btn = method.reply_markup.inline_keyboard[0][0]

    assert btn.text == '⬅️ Назад'
    assert btn.icon_custom_emoji_id is None
    assert method.text == 'привет'


async def test_manual_icon_is_not_overridden():
    """Если id проставлен руками — это осознанный выбор, не переигрываем."""
    from aiogram.methods import SendMessage
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=f'{e("back")} Назад', callback_data='x',
        icon_custom_emoji_id='111')]])
    method = await send(SendMessage(chat_id=1, text='.', reply_markup=markup))
    btn = method.reply_markup.inline_keyboard[0][0]

    assert btn.icon_custom_emoji_id == '111'
    assert btn.text == '⬅️ Назад'


# ── подписи кнопок ──────────────────────────────────────────────────────────
def test_leading_emoji_is_split_off():
    from app.content.emoji import leading_emoji_id

    assert leading_emoji_id(f'{e("back")} Назад') == ('5321133913291135005', 'Назад')
    assert leading_emoji_id(f'{e("stats")} Статистика') == ('', '📊 Статистика')
    assert leading_emoji_id('Просто текст') == ('', 'Просто текст')
    assert leading_emoji_id('') == ('', '')


def test_every_registered_character_is_reachable():
    """BY_CHAR строится из EMOJI — расхождение означало бы, что часть
    значков не превратится в кастомные при отправке."""
    assert set(BY_CHAR) == {char for char, _ in EMOJI.values()}
