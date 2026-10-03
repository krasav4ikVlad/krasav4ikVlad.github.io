"""Именные ссылки приглашения: ref_vlad вместо ref_802421217.

Смысл не в красоте. Числовая ссылка не говорит, откуда пришёл человек, а
метку можно выдать на конкретный канал и потом увидеть, сколько она
привела. Поэтому проверяется не только «ссылка работает», но и «привод
посчитался тому, кому надо».
"""

import pytest

from app.bot.handlers.referrals import own_links
from app.bot.handlers.start import register, resolve_referrer
from app.domain import ref_tags as domain
from app.repositories.ref_tags import RefTagsRepository


@pytest.fixture
async def tags(db):
    """С индексами, как на старте бота: уникальность метки держит индекс, и
    без него проверка «одна метка — один человек» ничего не проверяет."""
    repo = RefTagsRepository(db['ref_tags'])
    await repo.ensure_indexes()
    return repo


class Container:
    """Ровно то, что нужно разбору ссылки и экрану рефералки."""

    def __init__(self, tags=None, users=None, aliases=''):
        self.ref_tags = tags
        self.users = users
        self.settings = Settings(aliases)
        self.notifier = None


class Settings:
    def __init__(self, aliases=''):
        self.values = {'link.ref_aliases': aliases, 'price.start_balance': 0}

    async def get(self, key, default=''):
        return self.values.get(key, default)

    async def int(self, key):
        return int(self.values.get(key) or 0)


class User:
    def __init__(self, user_id=5, username='friend'):
        self.id = user_id
        self.username = username
        self.first_name = 'Друг'
        self.last_name = ''
        self.language_code = 'ru'
        self.is_premium = False


# ── правила метки ───────────────────────────────────────────────────────────
def test_a_good_tag_is_accepted():
    assert domain.check('vlad') == ''
    assert domain.check('vlad_tg') == ''
    assert domain.check('blog2024') == ''


def test_case_does_not_matter():
    """Дали Vlad — в посте напишут vlad, и ссылка обязана работать."""
    assert domain.normalize('  @VLAD ') == 'vlad'


def test_a_numeric_tag_is_refused():
    """ref_12345 уже значит «пригласил человек с таким id»."""
    assert domain.check('12345')


def test_tags_with_odd_characters_are_refused():
    for bad in ('влад', 'vlad!', 'vlad vlad', 'vlad-tg', 'a'):
        assert domain.check(bad), bad


def test_the_link_is_built_from_the_tag():
    assert domain.link('rsconnect_bot', 'vlad') == \
        'https://t.me/rsconnect_bot?start=ref_vlad'


# ── хранение ────────────────────────────────────────────────────────────────
async def test_a_tag_points_at_its_owner(db, tags):
    await tags.create('vlad', 802421217, note='блогер')

    assert await tags.owner('vlad') == 802421217
    assert await tags.owner('VLAD') == 802421217, 'регистр не должен мешать'
    assert await tags.owner('нет-такой') == 0


async def test_the_same_tag_cannot_go_to_two_people(db, tags):
    """Иначе деньги за приглашённого ушли бы не тому."""
    assert await tags.create('vlad', 1) is True
    assert await tags.create('vlad', 2) is False
    assert await tags.owner('vlad') == 1


async def test_one_person_can_have_several_tags(db, tags):
    """По одной на канал — иначе непонятно, какая работает."""
    await tags.create('vlad_tg', 1)
    await tags.create('vlad_yt', 1)

    assert {row['tag'] for row in await tags.of_user(1)} == {'vlad_tg', 'vlad_yt'}


async def test_registrations_are_counted_per_tag(db, tags):
    await tags.create('vlad', 1)
    await tags.count_hit('vlad')
    await tags.count_hit('vlad')

    row = (await tags.of_user(1))[0]
    assert row['registrations'] == 2 and row['last_at']


async def test_a_freed_tag_can_be_given_to_someone_else(db, tags):
    await tags.create('vlad', 1)
    assert await tags.remove('vlad') is True

    assert await tags.create('vlad', 2) is True
    assert await tags.owner('vlad') == 2


async def test_removing_a_missing_tag_is_not_a_success(db, tags):
    assert await tags.remove('nope') is False


# ── разбор ссылки ───────────────────────────────────────────────────────────
async def test_a_tag_resolves_to_its_owner(db, tags):
    await tags.create('vlad', 802421217)

    assert await resolve_referrer(Container(tags), 'vlad') == (802421217, 'vlad')


async def test_a_numeric_link_still_works(db, tags):
    assert await resolve_referrer(Container(tags), '802421217') == (802421217, '')


async def test_old_aliases_from_settings_keep_working(db, tags):
    """Их уже раздали людям — ломать выданные ссылки нельзя."""
    c = Container(tags, aliases='blog:555, vk:777')

    assert await resolve_referrer(c, 'blog') == (555, 'blog')
    assert await resolve_referrer(c, 'vk') == (777, 'vk')


async def test_a_tag_beats_an_old_alias_with_the_same_name(db, tags):
    """Новая метка — свежее решение, и счётчик есть только у неё."""
    await tags.create('blog', 999)
    c = Container(tags, aliases='blog:555')

    assert await resolve_referrer(c, 'blog') == (999, 'blog')


async def test_an_unknown_tag_brings_nobody(db, tags):
    assert await resolve_referrer(Container(tags), 'nope') == ('', '')


# ── регистрация по метке ────────────────────────────────────────────────────
async def test_registering_by_a_tag_credits_the_owner_and_the_tag(db, tags,
                                                                  user_factory):
    from app.repositories.users import UsersRepository

    users = UsersRepository(db['users'])
    await user_factory(**{'user_data.user_id': 802421217})
    await tags.create('vlad', 802421217)
    c = Container(tags, users=users)

    document = await register(User(user_id=5), 'ref_vlad', c, c.settings)

    assert document['user_data']['referrer'] == 802421217
    assert document['user_data']['ref_tag'] == 'vlad', 'откуда пришёл — в документе'
    assert (await tags.of_user(802421217))[0]['registrations'] == 1
    owner = await users.get(802421217)
    assert 5 in owner['info']['ref_stats']['referrals']


async def test_registering_by_a_numeric_link_leaves_no_tag(db, tags, user_factory):
    from app.repositories.users import UsersRepository

    users = UsersRepository(db['users'])
    await user_factory(**{'user_data.user_id': 802421217})
    c = Container(tags, users=users)

    document = await register(User(user_id=5), 'ref_802421217', c, c.settings)

    assert document['user_data']['referrer'] == 802421217
    assert 'ref_tag' not in document['user_data']


# ── что видит сам человек ───────────────────────────────────────────────────
async def test_a_person_without_tags_sees_the_numeric_link(db, tags):
    links = await own_links(Container(tags), 5, 'rsconnect_bot')

    assert links == ['https://t.me/rsconnect_bot?start=ref_5']


async def test_a_person_with_tags_sees_them_instead(db, tags):
    """Числовую рядом не показываем: скопируют короткую, и метка не посчитается."""
    await tags.create('vlad', 5)

    links = await own_links(Container(tags), 5, 'rsconnect_bot')

    assert links == ['https://t.me/rsconnect_bot?start=ref_vlad']


async def test_all_of_the_persons_tags_are_shown(db, tags):
    await tags.create('vlad_tg', 5)
    await tags.create('vlad_yt', 5)

    links = await own_links(Container(tags), 5, 'rsconnect_bot')

    assert len(links) == 2 and all('ref_vlad' in link for link in links)
