"""Кастомные эмодзи: реестр вместо тега в каждой строке."""

import pytest

from app.content import emoji
from app.content.emoji import EMOJI, e, missing_ids, plain


@pytest.fixture(autouse=True)
def enabled():
    emoji.set_enabled(True)
    yield
    emoji.set_enabled(True)


def test_known_emoji_becomes_a_tag():
    assert e('back') == '<tg-emoji emoji-id="5321133913291135005">⬅️</tg-emoji>'


def test_emoji_without_id_stays_a_plain_character():
    """Не у всех значков есть свой id — экран из-за этого ломаться не должен."""
    assert e('stats') == '📊'


def test_unknown_name_does_not_raise():
    """Опечатка в коде должна быть видна на экране, а не падать посреди
    отрисовки чужого профиля."""
    assert e('нет такого имени') == 'нет такого имени'


def test_toggle_turns_all_of_them_into_plain_characters():
    emoji.set_enabled(False)

    assert e('back') == '⬅️'
    assert e('money') == '💰'


def test_plain_never_returns_a_tag():
    """В кнопках разметка не работает — там нужен только символ."""
    emoji.set_enabled(True)
    assert plain('back') == '⬅️'
    assert '<' not in plain('money')


def test_registry_has_no_duplicate_ids():
    """Один id на два значка — это copy-paste: покажется чужая картинка."""
    ids = [emoji_id for _, emoji_id in EMOJI.values() if emoji_id]
    assert len(ids) == len(set(ids)), 'один и тот же id у разных эмодзи'


def test_every_entry_has_a_character():
    for name, (char, _) in EMOJI.items():
        assert char.strip(), f'у «{name}» нет значка — покажется пустое место'


def test_missing_ids_are_listed():
    """Чтобы было видно, что осталось заполнить."""
    assert 'stats' in missing_ids()
    assert 'back' not in missing_ids()


def test_profile_screen_uses_the_registry():
    from app.bot.screens.profile import profile_caption

    user = {'user_data': {'user_id': 5}, 'info': {'balance': 0}}
    assert 'tg-emoji' in profile_caption(user)

    emoji.set_enabled(False)
    assert 'tg-emoji' not in profile_caption(user)
