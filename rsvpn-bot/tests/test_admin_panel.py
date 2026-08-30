"""Админка целиком: реальный Dispatcher aiogram, но без сети и без Mongo."""

from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.admin import panel
from app.bot.callbacks import Admin as Adm
from app.bot.middlewares.deps import DependenciesMiddleware
from app.bot.middlewares.emoji import PlainEmojiMiddleware, emoji_middleware
from app.bot.middlewares.user import UserMiddleware
from app.content.emoji import BY_CHAR, e

CHAT = Chat(id=1, type='private')
ADMIN = User(id=1, is_bot=False, first_name='Admin')


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, str]] = []
        self.markups: list = []
        self.all_markups: list = []      # markups обход чистит, этот — нет

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b''

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, getattr(method, 'text', None)
                           or getattr(method, 'caption', None) or ''))
        if getattr(method, 'reply_markup', None) is not None:
            self.markups.append(method.reply_markup)
            self.all_markups.append(method.reply_markup)
        if name == 'AnswerCallbackQuery':
            return True
        return Message(message_id=999, date=datetime.now(), chat=CHAT,
                       text=getattr(method, 'text', '') or '', from_user=ADMIN)

    @property
    def last_text(self) -> str:
        """Последний непустой текст: ответ на callback приходит пустым."""
        return next((text for _, text in reversed(self.calls) if text), '')


@pytest.fixture
async def admin_env(container):
    from app.admin.entities import build_entities

    await container.startup()
    container.entities = build_entities(container)

    session = RecordingSession()
    bot = Bot(token='1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', session=session)
    # как в create_bot: иначе тесты админки проверяли бы не то, что уходит
    bot.session.middleware(emoji_middleware)

    dp = Dispatcher()
    for middleware in (DependenciesMiddleware(container), UserMiddleware(container.users)):
        dp.message.middleware(middleware)
        dp.callback_query.middleware(middleware)
    dp.include_router(panel.create_router(container.config.admin_ids))

    yield dp, bot, session, container
    await bot.session.close()


def callback(data: str) -> Update:
    message = Message(message_id=10, date=datetime.now(), chat=CHAT, text='panel',
                      from_user=ADMIN)
    return Update(update_id=1, callback_query=CallbackQuery(
        id='q', from_user=ADMIN, chat_instance='ci', data=data, message=message))


def message(text: str) -> Update:
    return Update(update_id=2, message=Message(
        message_id=11, date=datetime.now(), chat=CHAT, text=text, from_user=ADMIN))


async def test_admin_opens_and_shows_stats(admin_env):
    dp, bot, session, _ = admin_env
    await dp.feed_update(bot, message('/admin'))
    assert 'Админ-панель' in session.last_text


async def test_toggle_feature_from_panel(admin_env):
    dp, bot, session, container = admin_env

    assert await container.settings.flag('features.extend_enabled') is True
    await dp.feed_update(bot, callback(Adm(act='tgl', a='features.extend_enabled').pack()))
    assert await container.settings.flag('features.extend_enabled') is False


async def test_change_plan_price_from_panel(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, callback(Adm(act='eedit', a='plan|price', b='1month').pack()))
    await dp.feed_update(bot, message('199'))

    plan = await container.plans.get('1month')
    assert plan['price'] == 199


async def test_bad_value_is_rejected(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, callback(Adm(act='fld', a='pay.min_topup').pack()))
    await dp.feed_update(bot, message('не число'))

    assert 'целое число' in session.calls[-1][1]
    assert await container.settings.int('pay.min_topup') == 75


async def test_non_admin_is_ignored(admin_env):
    dp, bot, session, container = admin_env
    stranger = User(id=999, is_bot=False, first_name='Stranger')
    update = Update(update_id=3, message=Message(
        message_id=12, date=datetime.now(), chat=Chat(id=999, type='private'),
        text='/admin', from_user=stranger))

    before = len(session.calls)
    await dp.feed_update(bot, update)
    assert len(session.calls) == before


# ── обход всей админки ──────────────────────────────────────────────────────
#
# Кнопка «⬅️ Назад» в настройках вела в admin_main, а тот вызывал
# build_stats_text() и main_kb() вообще без аргументов — выход в /admin падал
# с TypeError. Ни один тест этого не ловил: они проверяли отдельные действия,
# но никогда не проходили меню целиком.
#
# Поэтому здесь не ещё один точечный тест, а обход: жмём каждую кнопку,
# до которой можно дойти, и требуем, чтобы ни одна не упала.

def callback_targets(markup) -> list[str]:
    return [button.callback_data
            for row in (getattr(markup, 'inline_keyboard', None) or [])
            for button in row
            if button.callback_data]


async def crawl(dp, bot, session, limit: int = 200) -> int:
    """Нажать каждую кнопку, до которой можно дойти. Вернуть, сколько нажали."""
    await dp.feed_update(bot, message('/admin'))
    queue = callback_targets(session.markups[-1])
    seen, clicked = set(queue), 0

    while queue and clicked < limit:
        data = queue.pop(0)
        clicked += 1

        session.markups.clear()
        # падение хендлера прорастёт сюда: ErrorsMiddleware в этой сборке нет
        await dp.feed_update(bot, callback(data))

        for target in (callback_targets(session.markups[-1]) if session.markups else []):
            if target not in seen:
                seen.add(target)
                queue.append(target)

    return clicked


async def test_every_button_in_the_panel_leads_somewhere(admin_env):
    dp, bot, session, container = admin_env

    clicked = await crawl(dp, bot, session)

    assert clicked > 20, f'обход прошёл всего {clicked} кнопок — меню не раскрылось'


async def test_back_from_settings_returns_to_the_panel(admin_env):
    """Ровно тот путь, который был сломан: настройки → назад → /admin."""
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, callback(Adm(act='sets').pack()))
    assert 'Настройки бота' in session.last_text

    await dp.feed_update(bot, callback(Adm(act='main').pack()))
    assert 'Админ-панель' in session.last_text


async def test_back_from_a_settings_group_returns_to_the_list(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, callback(Adm(act='grp', a='pricing').pack()))
    await dp.feed_update(bot, callback(Adm(act='sets').pack()))

    assert 'Настройки бота' in session.last_text


# ── админка без кастомных эмодзи ────────────────────────────────────────────
#
# Аварийный выход. Право отправлять кастомные эмодзи есть не у каждого бота,
# и id иногда протухают — Telegram отвечает ошибкой и не доставляет сообщение
# целиком. Если бы админка ходила на тех же значках, отвалилась бы вместе со
# всем остальным, и выключить тумблер стало бы негде.

async def test_no_custom_emoji_anywhere_in_the_panel(admin_env):
    """Обход всей админки: ни одного тега и ни одной иконки на кнопке."""
    dp, bot, session, container = admin_env

    assert await container.settings.flag('content.custom_emoji') is True

    await crawl(dp, bot, session)

    with_tags = [text for _, text in session.calls if '<tg-emoji' in text]
    assert not with_tags, f'кастомные эмодзи в тексте админки: {with_tags[:3]}'

    icons = [button.text for markup in session.all_markups
             for row in markup.inline_keyboard for button in row
             if button.icon_custom_emoji_id]
    assert not icons, f'кастомные иконки на кнопках админки: {icons[:3]}'


async def test_panel_keeps_the_plain_characters(admin_env):
    """Не «убрали значки», а «оставили обычные»: подписи не должны опустеть."""
    dp, bot, session, _ = admin_env

    await dp.feed_update(bot, callback(Adm(act='sets').pack()))
    labels = [button.text for row in session.markups[-1].inline_keyboard
              for button in row]

    assert any(char in label for label in labels for char in BY_CHAR), labels


async def test_toggle_applies_without_a_restart(admin_env):
    """Тумблер, который сработает «после перезапуска», бесполезен: в аварии
    перезапускать некому и некогда."""
    from app.content import emoji

    dp, bot, session, container = admin_env
    assert emoji.enabled() is True

    await dp.feed_update(bot, callback(Adm(act='tgl', a='content.custom_emoji').pack()))
    assert emoji.enabled() is False

    await dp.feed_update(bot, callback(Adm(act='tgl', a='content.custom_emoji').pack()))
    assert emoji.enabled() is True


async def test_user_screens_still_get_custom_emoji(admin_env):
    """Обычные значки — только у админки. Проверка, что заглушили не всё."""
    dp, bot, session, _ = admin_env

    await bot.send_message(chat_id=5, text=f'{e("money")} Баланс')

    assert '<tg-emoji' in session.calls[-1][1]


async def test_broadcast_reaches_users_with_custom_emoji(admin_env):
    """Рассылка запускается из хендлера админки, и фоновая задача уносит с
    собой его контекст. Письмо при этом уходит пользователю."""
    from app.campaigns.sender import Sender

    dp, bot, session, _ = admin_env
    sent = {}

    async def handler(call, data):
        sent['ok'] = await Sender().send(bot, 5, f'{e("money")} Баланс')

    await PlainEmojiMiddleware()(handler, None, {})

    assert sent['ok'] is True
    assert '<tg-emoji' in session.calls[-1][1]


# ── кнопки под письмом рассылки ─────────────────────────────────────────────
#
# Письмо без кнопок — тупик: человек прочитал и закрыл. Обе ведут туда, куда
# он пойдёт дальше: смотреть свой срок и читать новости.

async def test_a_broadcast_letter_carries_two_buttons(admin_env):
    from app.bot.keyboards.common import broadcast_keyboard

    dp, bot, session, container = admin_env

    markup = await broadcast_keyboard(container.settings)
    buttons = [b for row in markup.inline_keyboard for b in row]

    assert len(buttons) == 2, buttons
    assert 'подписка' in buttons[0].text.lower()
    assert 'my_subscription' in buttons[0].callback_data
    assert buttons[1].url == await container.settings.get('link.channel')


async def test_the_channel_button_disappears_without_a_link(admin_env):
    """Кнопка с пустым url — это ошибка Telegram и непринятое письмо."""
    from app.bot.keyboards.common import broadcast_keyboard

    dp, bot, session, container = admin_env
    await container.settings.set('link.channel', '')

    markup = await broadcast_keyboard(container.settings)

    assert len([b for row in markup.inline_keyboard for b in row]) == 1


async def test_the_preview_shows_the_same_buttons(admin_env):
    """Предпросмотр смотрят затем, чтобы увидеть настоящее письмо."""
    from app.admin.broadcast import Broadcast

    dp, bot, session, container = admin_env
    state = dp.fsm.get_context(bot=bot, chat_id=CHAT.id, user_id=ADMIN.id)
    await state.set_state(Broadcast.text)
    await state.update_data(total=10, audience='all')

    session.markups.clear()
    await dp.feed_update(bot, message('Привет, мы всё починили'))

    letter = session.markups[0]
    labels = [b.text for row in letter.inline_keyboard for b in row]
    assert any('подписка' in label.lower() for label in labels), labels


async def test_trial_reset_from_the_panel(admin_env):
    """Кнопка в админке действительно снимает метку, а не только рисует экран."""
    from app.core.time import now

    dp, bot, session, container = admin_env
    await container.users.create({'user_data': {'user_id': 50},
                                  'growth': {'trial_claimed_at': now(),
                                             'segment': 'expired_3d'}})

    await dp.feed_update(bot, callback(Adm(act='trial').pack()))
    assert 'Сброс бесплатного периода' in session.last_text

    await dp.feed_update(bot, callback(Adm(act='trask', a='expired').pack()))
    assert 'Сбросим у <code>1</code>' in session.last_text

    await dp.feed_update(bot, callback(Adm(act='trgo', a='expired').pack()))
    assert 'trial_claimed_at' not in (await container.users.get(50))['growth']


# ── удаление тестового пользователя ─────────────────────────────────────────
async def test_deluser_asks_before_deleting(admin_env):
    """Опасное действие не должно срабатывать с одной команды."""
    dp, bot, session, container = admin_env
    container.wipe.vpn = None
    await container.users.create({'user_data': {'user_id': 77, 'username': 'twink'},
                                  'info': {'balance': 100}, 'vpn': {'uuid': 'u'}})

    await dp.feed_update(bot, message('/deluser 77'))

    assert 'Удалить пользователя?' in session.last_text
    assert await container.users.get(77) is not None, 'удалил без подтверждения'


async def test_deluser_removes_after_confirmation(admin_env):
    dp, bot, session, container = admin_env
    container.wipe.vpn = None
    await container.users.create({'user_data': {'user_id': 77}, 'vpn': {}})

    await dp.feed_update(bot, message('/deluser 77'))
    await dp.feed_update(bot, callback(Adm(act='delusr', a='77').pack()))

    assert await container.users.get(77) is None
    assert 'удалён' in session.last_text.lower()


async def test_deluser_finds_by_username(admin_env):
    dp, bot, session, container = admin_env
    await container.users.create({'user_data': {'user_id': 78, 'username': 'twink'},
                                  'vpn': {}})

    await dp.feed_update(bot, message('/deluser @twink'))

    assert 'Удалить пользователя?' in session.last_text


async def test_admin_cannot_delete_himself(admin_env):
    """Иначе можно снести доступ к админке вместе с собственным документом."""
    dp, bot, session, container = admin_env
    admin_id = container.config.admin_ids[0]
    await container.users.create({'user_data': {'user_id': admin_id}, 'vpn': {}})

    await dp.feed_update(bot, message(f'/deluser {admin_id}'))
    await dp.feed_update(bot, callback(Adm(act='delusr', a=str(admin_id)).pack()))

    assert await container.users.get(admin_id) is not None
    assert 'Администратора удалить нельзя' in session.last_text


async def test_deluser_without_arguments_explains_itself(admin_env):
    dp, bot, session, _ = admin_env

    await dp.feed_update(bot, message('/deluser'))

    assert 'Кого удалить?' in session.last_text


# ── сколько людей в аудитории ───────────────────────────────────────────────
#
# Скидка «истёкшим 50%» без числа получателей ничего не говорит о своей
# цене: полсотни процентов можно раздать и десяти людям, и десяти тысячам.

async def make_segments(container, **by_segment):
    for segment, count in by_segment.items():
        for i in range(count):
            await container.users.create({
                'user_data': {'user_id': hash((segment, i)) % 10 ** 8},
                'growth': {'segment': segment}})


async def test_discount_screen_shows_the_number_of_people(admin_env):
    dp, bot, session, container = admin_env
    await make_segments(container, expired_3d=3, active_paid=2, inactive_no_sub=1)

    await dp.feed_update(bot, callback(Adm(act='grp', a='discounts').pack()))
    text = session.last_text

    assert 'Истёкшие: <b>0%</b> — <code>3</code> чел.' in text
    assert 'С активной подпиской: <b>0%</b> — <code>2</code> чел.' in text
    assert 'Всем: <b>0%</b> — <code>6</code> чел.' in text
    # «без активной» шире «истёкших»: плюс тот, у кого подписки не было
    assert 'Без активной подписки: <b>0%</b> — <code>4</code> чел.' in text


async def test_the_number_is_on_the_button_too(admin_env):
    dp, bot, session, container = admin_env
    await make_segments(container, expired_3d=3)

    await dp.feed_update(bot, callback(Adm(act='grp', a='discounts').pack()))
    labels = [b.text for row in session.markups[-1].inline_keyboard for b in row]

    assert any('Истёкшие' in label and '(3)' in label for label in labels), labels


async def test_setting_screen_repeats_the_number(admin_env):
    dp, bot, session, container = admin_env
    await make_segments(container, churned_60d=4)

    await dp.feed_update(bot, callback(Adm(act='fld', a='discount.churned').pack()))

    assert 'Касается:</b> <code>4</code> чел.' in session.last_text


async def test_settings_without_an_audience_say_nothing_about_people(admin_env):
    """Цены и лимиты к аудиториям отношения не имеют — там числа лишние."""
    dp, bot, session, _ = admin_env

    await dp.feed_update(bot, callback(Adm(act='grp', a='pricing').pack()))

    assert 'чел.' not in session.last_text


async def test_broken_count_is_not_shown_as_zero(container):
    """«Не смогли посчитать» и «никого нет» на экране должны отличаться:
    мнимый ноль получателей — повод отменить нужную рассылку."""
    class Broken:
        async def count_documents(self, *a, **kw):
            raise RuntimeError('база недоступна')

    container.users.col = Broken()

    assert await container.audiences.all() == {}


async def test_counts_are_cached(container):
    calls = {'n': 0}
    original = container.users.col.count_documents

    async def counting(*args, **kwargs):
        calls['n'] += 1
        return await original(*args, **kwargs)

    container.users.col.count_documents = counting
    await container.audiences.all()
    first = calls['n']
    await container.audiences.all()

    assert calls['n'] == first, 'второе открытие экрана не должно ходить в базу'


# ── /diag: что из фонового работает ─────────────────────────────────────────
#
# Автопродление живёт в планировщике бота, напоминания приходят вебхуками
# панели в другой процесс. Понять по симптомам, что именно молчит, нельзя —
# отсюда экран, который показывает обе стороны и, главное, пустоту.

async def test_diag_says_the_panel_never_called(admin_env):
    """Самый частый и самый полезный ответ: вебхуков не было вовсе."""
    dp, bot, session, _ = admin_env

    await dp.feed_update(bot, message('/diag'))

    assert 'Событий от панели: <b>не было</b>' in session.last_text
    assert 'WEBHOOK_ENABLED=true' in session.last_text, 'нет подсказки, что чинить'


async def test_diag_shows_the_last_renewal_run(admin_env):
    from app.admin import health

    dp, bot, session, container = admin_env
    await container.health.mark(health.RENEWAL, checked=120, renewed=8,
                                no_funds=3, failed=0)

    await dp.feed_update(bot, message('/diag'))
    text = session.last_text

    assert 'Последний проход: <b>только что</b>' in text
    assert 'renewed=8' in text and 'no_funds=3' in text


async def test_diag_reports_a_disabled_scheduler(admin_env):
    """SCHEDULER_ENABLED=0 — списаний не будет, и это надо сказать прямо."""
    import dataclasses

    dp, bot, session, container = admin_env
    container.config = dataclasses.replace(container.config, scheduler_enabled=False)

    await dp.feed_update(bot, message('/diag'))

    assert 'Планировщик выключен' in session.last_text


async def test_diag_reports_a_disabled_setting(admin_env):
    dp, bot, session, container = admin_env
    await container.settings.set('features.autorenew_enabled', False)

    await dp.feed_update(bot, message('/diag'))

    assert 'Выключено в настройках' in session.last_text


async def test_bypasssync_shows_before_it_fixes(admin_env):
    """Правка ходит в панель на каждого — вслепую её запускать не стоит."""
    from datetime import timedelta

    from app.core.time import now, parse_dt

    dp, bot, session, container = admin_env
    container.vpn = FakePanel()
    await container.users.create({
        'user_data': {'user_id': 77}, 'info': {'balance': 0},
        'vpn': {'uuid': 'u-77', 'shortUuid': 's-77',
                'expireAt': now() + timedelta(days=30),
                'bypass_uuid': 'bp-77', 'bypass_expireAt': now()}})

    await dp.feed_update(bot, message('/bypasssync'))
    assert 'Расхождений: 1' in session.last_text
    user = await container.users.get(77)
    assert parse_dt(user['vpn']['bypass_expireAt']) < now() + timedelta(days=1)

    await dp.feed_update(bot, message('/bypasssync fix'))
    user = await container.users.get(77)
    assert parse_dt(user['vpn']['bypass_expireAt']) == parse_dt(user['vpn']['expireAt'])


async def test_bypasssync_without_a_panel_says_so(admin_env):
    """Иначе отчёт «не вышло у 300 человек» выглядит как беда с датами."""
    from datetime import timedelta

    from app.core.time import now

    dp, bot, session, container = admin_env
    container.vpn = None
    await container.users.create({
        'user_data': {'user_id': 78}, 'info': {'balance': 0},
        'vpn': {'uuid': 'u-78', 'expireAt': now() + timedelta(days=30),
                'bypass_uuid': 'bp-78', 'bypass_expireAt': now()}})

    await dp.feed_update(bot, message('/bypasssync fix'))

    assert 'панели не собран' in session.last_text


async def test_bypasssync_says_when_everything_matches(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, message('/bypasssync'))

    assert 'совпадают' in session.last_text


async def test_diag_notices_a_wrong_secret(admin_env):
    """Панель зовёт, но подпись не сходится — это отдельная беда, и по
    молчанию бота её не отличить от ненастроенных вебхуков."""
    from app.admin import health

    dp, bot, session, container = admin_env
    await container.health.mark(health.PANEL_WEBHOOK, note='bad_signature')

    await dp.feed_update(bot, message('/diag'))

    assert 'подпись не сходится' in session.last_text


async def test_diag_lists_the_enabled_reminders(admin_env):
    dp, bot, session, container = admin_env
    await container.settings.set('expiry.send_2d', True)

    await dp.feed_update(bot, message('/diag'))

    assert 'Включены:' in session.last_text
    assert '2d' in session.last_text


async def test_diag_changes_nothing(admin_env):
    """Команда только читает — её можно жать смело."""
    from app.admin import health

    dp, bot, session, container = admin_env
    before = await container.health.read()

    await dp.feed_update(bot, message('/diag'))
    await dp.feed_update(bot, callback(Adm(act='diag').pack()))

    assert await container.health.read() == before
    assert health.RENEWAL not in await container.health.read()


async def test_blocked_screen_offers_to_clear_the_list(admin_env):
    dp, bot, session, container = admin_env
    for user_id in (900, 901):
        await container.users.create({'user_data': {'user_id': user_id},
                                      'growth': {'blocked_bot': True}})

    await dp.feed_update(bot, callback(Adm(act='blocked').pack()))
    assert 'Таких сейчас: <code>2</code>' in session.last_text

    await dp.feed_update(bot, callback(Adm(act='bcunbl').pack()))
    assert await container.users.blocked_count() == 0
    assert 'Таких сейчас: <code>0</code>' in session.last_text


async def test_empty_blocked_screen_has_no_clear_button(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, callback(Adm(act='blocked').pack()))
    labels = [b.text for row in session.markups[-1].inline_keyboard for b in row]

    assert not any('Очистить' in label for label in labels), labels


# ── заявки на вывод ─────────────────────────────────────────────────────────
from app.bot.callbacks import PayoutAdmin as Pay          # noqa: E402


def card(session) -> str:
    """Текст самой карточки: ответ на нажатие («Обновлено») приходит позже
    и в last_text перекрывает её."""
    return next((text for name, text in reversed(session.calls)
                 if name == 'EditMessageText'), '')


async def applicant(container, user_id: int = 700, *, to_bot: bool = True,
                    balance: int = 50, ref: int = 540):
    stats = {'withdrawable': ref, 'pending_payout_active': True,
             'method': [{'id': 'm1', 'type': 'sbp',
                         'data': {'fio': 'Иван', 'phone': '+79000000000',
                                  'bank': 'Сбербанк'}}],
             'payout_selected': 'bot_balance' if to_bot else 'm1'}
    await container.users.create({
        'user_data': {'user_id': user_id, 'username': 'mazilka003',
                      'date_joined': '09.12.2025 16:40:23', 'utm': 'ref_882698012'},
        'info': {'balance': balance, 'ref_stats': stats}})
    return user_id


async def test_the_card_shows_both_balances_and_the_method(admin_env):
    dp, bot, session, container = admin_env
    await applicant(container)

    await dp.feed_update(bot, callback(Pay(action='refresh', user_id=700).pack()))
    text = card(session)

    assert '@mazilka003 (<code>700</code>)' in text
    assert 'Регистрация: 09.12.2025 16:40:23' in text
    assert 'UTM: <code>ref_882698012</code>' in text
    assert 'Обычный баланс: <b>50₽</b>' in text
    assert 'Реферальный баланс: <b>540₽</b>' in text
    assert 'обновлено в' in text


async def test_the_action_button_matches_the_chosen_method(admin_env):
    dp, bot, session, container = admin_env
    await applicant(container, 700, to_bot=True)
    await applicant(container, 701, to_bot=False)

    await dp.feed_update(bot, callback(Pay(action='refresh', user_id=700).pack()))
    labels = [b.text for row in session.markups[-1].inline_keyboard for b in row]
    assert any('На баланс' in label for label in labels), labels
    assert not any('Выведено' in label for label in labels), labels

    await dp.feed_update(bot, callback(Pay(action='refresh', user_id=701).pack()))
    labels = [b.text for row in session.markups[-1].inline_keyboard for b in row]
    assert any('Выведено' in label for label in labels), labels
    assert not any('На баланс' in label for label in labels), labels


async def test_to_balance_moves_the_whole_referral_balance(admin_env):
    dp, bot, session, container = admin_env
    await applicant(container, balance=50, ref=540)

    await dp.feed_update(bot, callback(Pay(action='balance', user_id=700).pack()))

    info = (await container.users.get(700))['info']
    assert info['balance'] == 590
    assert info['ref_stats']['withdrawable'] == 0
    assert any('Заявка на вывод исполнена' in text and '590₽' in text
               for _, text in session.calls), 'человеку не сказали про баланс'


async def test_paid_clears_the_referral_balance_only(admin_env):
    """Выводятся реферальные деньги — их и обнуляем. Обычный баланс человек
    пополнял сам, к заявке он отношения не имеет."""
    dp, bot, session, container = admin_env
    await applicant(container, to_bot=False, balance=50, ref=540)

    await dp.feed_update(bot, callback(Pay(action='paid', user_id=700).pack()))

    info = (await container.users.get(700))['info']
    assert info['ref_stats']['withdrawable'] == 0
    assert info['balance'] == 50
    assert any('в течение пары часов' in text for _, text in session.calls)


async def test_reject_can_be_stepped_back_from(admin_env):
    """«Отказать» нажимают и случайно, а с экрана причин иначе не выйти."""
    dp, bot, session, container = admin_env
    await applicant(container)

    await dp.feed_update(bot, callback(Pay(action='reject_ask', user_id=700).pack()))
    labels = [b.text for row in session.markups[-1].inline_keyboard for b in row]
    assert any('Назад' in label for label in labels), labels

    await dp.feed_update(bot, callback(Pay(action='refresh', user_id=700).pack()))
    labels = [b.text for row in session.markups[-1].inline_keyboard for b in row]
    assert any('Отказать' in label for label in labels), 'назад вернул не карточку'
    assert (await container.users.get(700))['info']['ref_stats']['withdrawable'] == 540


async def test_reject_keeps_the_money(admin_env):
    dp, bot, session, container = admin_env
    await applicant(container)

    await dp.feed_update(bot, callback(Pay(action='reject', user_id=700,
                                           reason='data').pack()))

    assert (await container.users.get(700))['info']['ref_stats']['withdrawable'] == 540
    assert any('отклонена' in text for _, text in session.calls)


# ── карточки, оставшиеся от старого бота ────────────────────────────────────
#
# Заявку бот присылает один раз, в момент нажатия человеком. Если её кнопки
# перестали работать после переезда, закрыть заявку нечем: новой карточки
# не будет, а человек ждёт деньги.

async def test_a_legacy_to_balance_button_still_works(admin_env):
    dp, bot, session, container = admin_env
    await applicant(container, balance=50, ref=540)

    await dp.feed_update(bot, callback('po:tobalance:700'))

    info = (await container.users.get(700))['info']
    assert info['balance'] == 590 and info['ref_stats']['withdrawable'] == 0


async def test_a_legacy_done_button_still_works(admin_env):
    dp, bot, session, container = admin_env
    await applicant(container, to_bot=False, ref=540)

    await dp.feed_update(bot, callback('po:done:700'))

    info = (await container.users.get(700))['info']
    assert info['ref_stats']['withdrawable'] == 0 and info['balance'] == 50
    assert any('в течение пары часов' in text for _, text in session.calls)


async def test_a_legacy_refresh_redraws_with_the_new_buttons(admin_env):
    """После обновления карточка становится обычной, и дальше всё как всегда."""
    dp, bot, session, container = admin_env
    await applicant(container)

    await dp.feed_update(bot, callback('po:refresh:700'))

    targets = [b.callback_data for row in session.markups[-1].inline_keyboard
               for b in row]
    assert targets and all(t.startswith('poa:') for t in targets), targets


async def test_a_legacy_reject_asks_for_a_reason_and_keeps_the_money(admin_env):
    dp, bot, session, container = admin_env
    await applicant(container)

    await dp.feed_update(bot, callback('po:reject:700'))
    labels = [b.text for row in session.markups[-1].inline_keyboard for b in row]
    assert any('Назад' in label for label in labels), labels

    await dp.feed_update(bot, callback('po:rej:data:700'))

    assert (await container.users.get(700))['info']['ref_stats']['withdrawable'] == 540
    assert any('отклонена' in text for _, text in session.calls)


def test_the_users_own_payout_buttons_are_not_intercepted():
    """У пользовательских кнопок вывода тот же префикс «po:». Владелец —
    админ, и широкий фильтр отобрал бы у него собственное меню вывода."""
    import re

    from app.admin.payouts import LEGACY_PATTERN
    from app.bot.callbacks import Payout

    for data in (Payout(action='order').pack(), Payout(action='menu').pack(),
                 Payout(action='pick', value='m1').pack(),
                 Payout(action='confirm').pack()):
        assert not re.match(LEGACY_PATTERN, data), data

    assert re.match(LEGACY_PATTERN, 'po:done:700')
    assert re.match(LEGACY_PATTERN, 'po:rej:data:700')


def test_legacy_callbacks_are_parsed():
    from app.admin.payouts import parse_legacy

    assert parse_legacy('po:tobalance:700') == Pay(action='balance', user_id=700)
    assert parse_legacy('po:rej:min:700') == Pay(action='reject', user_id=700,
                                                 reason='min')
    assert parse_legacy('po:done:not-a-number') is None
    assert parse_legacy('po:unknown:700') is None
    # новые кнопки этим путём не ходят: у них другой префикс
    assert parse_legacy(Pay(action='balance', user_id=700).pack()) is None


async def test_refresh_shows_fresh_numbers(admin_env):
    """Между заявкой и решением человек мог пополнить баланс — на старых
    цифрах решение принимать нельзя."""
    dp, bot, session, container = admin_env
    await applicant(container, balance=50)

    await dp.feed_update(bot, callback(Pay(action='refresh', user_id=700).pack()))
    assert 'Обычный баланс: <b>50₽</b>' in card(session)

    await container.users.credit(700, 100, 'пополнение')
    await dp.feed_update(bot, callback(Pay(action='refresh', user_id=700).pack()))

    assert 'Обычный баланс: <b>150₽</b>' in card(session)


async def test_diag_names_the_discount_that_is_running(admin_env):
    """Включённую на выходные скидку забывают выключить. /diag о ней помнит."""
    dp, bot, session, container = admin_env
    await container.settings.set('discount.expired', 0.5)

    await dp.feed_update(bot, message('/diag'))

    assert 'Истёкшие: <b>−50%</b>' in session.last_text


async def test_diag_says_when_no_discounts_are_running(admin_env):
    dp, bot, session, _ = admin_env

    await dp.feed_update(bot, message('/diag'))

    assert 'Скидок по аудиториям нет' in session.last_text
    assert 'Бонус к пополнению' in session.last_text


# ── проверка напоминаний ────────────────────────────────────────────────────
#
# «Придёт ли мне сообщение о продлении» — вопрос, на который иначе отвечает
# только ожидание: событие рождается в панели и приходит по её расписанию.

async def test_expiry_test_button_sends_the_real_reminder(admin_env):
    from app.bot.callbacks import Admin as Adm

    dp, bot, session, container = admin_env
    container.attach_bot(bot)
    await container.users.create({'user_data': {'user_id': ADMIN.id},
                                  'info': {'balance': 50}})

    session.calls.clear()
    await dp.feed_update(bot, callback(Adm(act='exptestgo', a='1d').pack()))

    texts = [t for name, t in session.calls if name == 'SendMessage']
    assert texts, 'напоминание не отправлено'
    assert 'RS VPN' in texts[0] or 'подписка' in texts[0].lower()


async def test_expiry_test_does_not_eat_the_real_reminder(admin_env):
    """Иначе проверка «съедала» бы настоящее письмо: флаг однократности."""
    from app.bot.callbacks import Admin as Adm

    dp, bot, session, container = admin_env
    container.attach_bot(bot)
    await container.users.create({'user_data': {'user_id': ADMIN.id}})
    await dp.feed_update(bot, callback(Adm(act='exptestgo', a='1d').pack()))

    doc = await container.users.col.find_one({'user_data.user_id': ADMIN.id})
    assert not ((doc.get('vpn') or {}).get('notified') or {}).get('1d')


async def test_expiry_test_reports_a_disabled_threshold(admin_env):
    from app.bot.callbacks import Admin as Adm

    dp, bot, session, container = admin_env
    container.attach_bot(bot)
    await container.users.create({'user_data': {'user_id': ADMIN.id}})
    await container.settings.set('expiry.send_1d', False)

    session.calls.clear()
    await dp.feed_update(bot, callback(Adm(act='exptestgo', a='1d').pack()))

    assert not [t for name, t in session.calls if name == 'SendMessage']


# ── личные серверы ──────────────────────────────────────────────────────────

async def test_private_server_button_is_open_to_everyone_by_default(admin_env):
    """Раздел обкатали и открыли: по умолчанию его видит любой."""
    from app.bot.handlers.private_servers import visible_for

    dp, bot, session, container = admin_env

    assert await visible_for(ADMIN.id, container, container.settings) is True
    assert await visible_for(999999, container, container.settings) is True


async def test_private_server_can_be_hidden_from_regular_users(admin_env):
    """Обратный переключатель нужен: обкатывать следующее придётся так же."""
    from app.bot.handlers.private_servers import visible_for

    dp, bot, session, container = admin_env
    await container.settings.set('private.visibility', 'admins')

    assert await visible_for(ADMIN.id, container, container.settings) is True
    assert await visible_for(999999, container, container.settings) is False


async def test_private_server_visibility_can_be_opened_to_everyone(admin_env):
    from app.bot.handlers.private_servers import visible_for

    dp, bot, session, container = admin_env
    await container.settings.set('private.visibility', 'all')

    assert await visible_for(999999, container, container.settings) is True


async def test_private_server_can_be_switched_off_entirely(admin_env):
    from app.bot.handlers.private_servers import visible_for

    dp, bot, session, container = admin_env
    await container.settings.set('private.visibility', 'off')

    assert await visible_for(ADMIN.id, container, container.settings) is False


async def test_admin_gives_the_server_by_squad_uuid(admin_env):
    from app.bot.callbacks import ServerAdmin

    dp, bot, session, container = admin_env
    container.attach_bot(bot)
    container.private.vpn = FakePanel()
    await container.users.create({'user_data': {'user_id': ADMIN.id},
                                  'info': {'balance': 3000},
                                  'vpn': {'uuid': 'u-1', 'shortUuid': 's-1'}})
    request = await container.private.request(ADMIN.id, 'mini', location='ams')

    await dp.feed_update(bot, callback(
        ServerAdmin(action='give', server_id=request.server['_id']).pack()))
    await dp.feed_update(bot, message('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'))

    server = await container.private.servers.get(request.server['_id'])
    assert server['status'] == 'active'
    assert server['squad_uuid'] == 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    # локацию выбрал покупатель, админ её не переспрашивает
    assert server['location'] == 'ams'


async def test_admin_is_not_allowed_to_paste_garbage_instead_of_a_uuid(admin_env):
    """Панель проглотит мусор молча, и сервер будет «выдан», но пустой."""
    from app.bot.callbacks import ServerAdmin

    dp, bot, session, container = admin_env
    container.attach_bot(bot)
    await container.users.create({'user_data': {'user_id': ADMIN.id},
                                  'info': {'balance': 3000}})
    request = await container.private.request(ADMIN.id, 'mini', location='ams')

    await dp.feed_update(bot, callback(
        ServerAdmin(action='give', server_id=request.server['_id']).pack()))
    await dp.feed_update(bot, message('какой-то сквад'))

    server = await container.private.servers.get(request.server['_id'])
    assert server['status'] == 'requested'
    assert 'UUID' in session.last_text


class FakePanel:
    async def update_subscription(self, uuid, **kw):
        return {'uuid': uuid}

    async def create_subscription(self, user_id, days):
        return {'uuid': f'u-{user_id}', 'shortUuid': f's-{user_id}',
                'expireAt': None, 'createdAt': None}


# ── выдача сервера в группе ─────────────────────────────────────────────────
#
# У ботов включён privacy mode: в группах Telegram отдаёт им только команды,
# реплаи и упоминания. Обычный текст с UUID до бота не доходил вовсе, и
# кнопка «Выдать сервер» выглядела сломанной.

GROUP = Chat(id=-1002433849803, type='supergroup')


def group_message(text: str) -> Update:
    return Update(update_id=7, message=Message(
        message_id=12, date=datetime.now(), chat=GROUP, text=text,
        from_user=ADMIN, message_thread_id=1561465))


async def _pending_server(container, bot):
    container.attach_bot(bot)
    container.private.vpn = FakePanel()
    await container.users.create({'user_data': {'user_id': ADMIN.id},
                                  'info': {'balance': 3000},
                                  'vpn': {'uuid': 'u-1', 'shortUuid': 's-1'}})
    result = await container.private.request(ADMIN.id, 'mini', location='ams')
    return result.server['_id']


async def test_squad_can_be_sent_by_command_from_a_group(admin_env):
    dp, bot, session, container = admin_env
    server_id = await _pending_server(container, bot)

    await dp.feed_update(bot, group_message(
        f'/squad {server_id} aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'))

    server = await container.private.servers.get(server_id)
    assert server['status'] == 'active'
    assert server['squad_uuid'] == 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


async def test_squad_command_remembers_the_server_from_the_button(admin_env):
    from app.bot.callbacks import ServerAdmin

    dp, bot, session, container = admin_env
    server_id = await _pending_server(container, bot)

    await dp.feed_update(bot, callback(
        ServerAdmin(action='give', server_id=server_id).pack()))
    await dp.feed_update(bot, message(
        '/squad aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'))

    assert (await container.private.servers.get(server_id))['status'] == 'active'


async def test_squad_command_refuses_garbage(admin_env):
    dp, bot, session, container = admin_env
    server_id = await _pending_server(container, bot)

    await dp.feed_update(bot, group_message(f'/squad {server_id} не-uuid'))

    assert (await container.private.servers.get(server_id))['status'] == 'requested'
    assert 'UUID' in session.last_text


async def test_squad_command_without_a_server_says_so(admin_env):
    dp, bot, session, container = admin_env
    await _pending_server(container, bot)

    await dp.feed_update(bot, group_message(
        '/squad aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'))

    assert 'какой сервер' in session.last_text


async def test_a_busy_squad_error_names_the_occupant(admin_env):
    """«Машина занята» без имени занявшего ищется перебором /srvdiag."""
    from app.bot.callbacks import ServerAdmin

    dp, bot, session, container = admin_env
    squad = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    taken = await _pending_server(container, bot)
    await container.private.activate(taken, squad)

    await container.users.create({'user_data': {'user_id': 557},
                                  'info': {'balance': 3000},
                                  'vpn': {'uuid': 'u-557', 'shortUuid': 's-557'}})
    second = await container.private.request(557, 'company', location='tyo')

    await dp.feed_update(bot, message(f'/squad {second.server["_id"]} {squad}'))

    assert taken in session.last_text, session.last_text
    assert 'Мини' in session.last_text and 'Амстердам' in session.last_text
    assert '/srvdel' in session.last_text, 'что делать дальше — не сказано'


async def test_srvdel_removes_the_server_by_owner(admin_env):
    """Своего же сервера мешает завести второй — команда нужна для тестов."""
    dp, bot, session, container = admin_env
    server_id = await _pending_server(container, bot)

    await dp.feed_update(bot, message(f'/srvdel {ADMIN.id}'))

    assert await container.private.servers.get(server_id) is None
    assert 'удалён' in session.last_text


async def test_srvdel_does_not_refund_unless_asked(admin_env):
    dp, bot, session, container = admin_env
    await _pending_server(container, bot)
    before = (await container.users.get(ADMIN.id))['info']['balance']

    await dp.feed_update(bot, message(f'/srvdel {ADMIN.id}'))

    assert (await container.users.get(ADMIN.id))['info']['balance'] == before


async def test_srvdel_refunds_on_request(admin_env):
    dp, bot, session, container = admin_env
    server_id = await _pending_server(container, bot)
    price = (await container.private.servers.get(server_id))['price']
    before = (await container.users.get(ADMIN.id))['info']['balance']

    await dp.feed_update(bot, message(f'/srvdel {server_id} refund'))

    assert (await container.users.get(ADMIN.id))['info']['balance'] == before + price


async def test_srvdel_without_arguments_explains_itself(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, message('/srvdel'))

    assert 'refund' in session.last_text and 'srv_' in session.last_text


async def test_a_share_request_shows_where_it_fits(admin_env):
    """Иначе админ поднимает новый VPS там, где хватило бы места на старом."""
    from app.admin.private_servers import request_card

    dp, bot, session, container = admin_env
    await _pending_server(container, bot)
    await container.users.create({'user_data': {'user_id': 555},
                                  'info': {'balance': 3000},
                                  'vpn': {'uuid': 'u-555', 'shortUuid': 's-555'}})
    taken = await container.private.request(555, 'share', location='nl',
                                            profile='reality')
    await container.private.activate(taken.server['_id'],
                                     'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')

    await container.users.create({'user_data': {'user_id': 556},
                                  'info': {'balance': 3000},
                                  'vpn': {'uuid': 'u-556', 'shortUuid': 's-556'}})
    second = await container.private.request(556, 'share', location='nl',
                                             profile='reality')

    card = await request_card(container, second.server)

    # Место на машине уже есть — заявка выдаётся одним нажатием, поднимать
    # ничего не нужно, и карточка говорит именно это.
    assert 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee' in card
    assert 'Выдать из запаса' in card


async def test_a_whole_server_request_says_to_raise_a_machine(admin_env):
    from app.admin.private_servers import request_card

    dp, bot, session, container = admin_env
    server_id = await _pending_server(container, bot)

    card = await request_card(container, await container.private.servers.get(server_id))

    assert 'Поднимите VPS' in card


async def test_prompt_in_a_group_asks_for_the_command(admin_env):
    """В личке — «пришлите сообщением», в группе так работать не будет."""
    from app.bot.callbacks import ServerAdmin

    dp, bot, session, container = admin_env
    server_id = await _pending_server(container, bot)

    await dp.feed_update(bot, Update(update_id=8, callback_query=CallbackQuery(
        id='1', from_user=ADMIN, chat_instance='1',
        data=ServerAdmin(action='give', server_id=server_id).pack(),
        message=Message(message_id=13, date=datetime.now(), chat=GROUP,
                        text='карточка', from_user=ADMIN))))

    assert '/squad' in session.last_text


# ── чистая тема: работа идёт правками одной карточки ────────────────────────
#
# Тема с заявками — рабочий список, а не переписка с ботом. Каждое «пришлите
# UUID», «выдан», «не вышло» отдельным сообщением превращало её в ленту, где
# заявки теряются среди ответов.

def give_press(server_id: str, message_id: int = 13) -> Update:
    from app.bot.callbacks import ServerAdmin

    return Update(update_id=8, callback_query=CallbackQuery(
        id='1', from_user=ADMIN, chat_instance='1',
        data=ServerAdmin(action='give', server_id=server_id).pack(),
        message=Message(message_id=message_id, date=datetime.now(), chat=GROUP,
                        text='карточка', from_user=ADMIN)))


async def test_the_prompt_replaces_the_card_instead_of_a_new_message(admin_env):
    dp, bot, session, container = admin_env
    server_id = await _pending_server(container, bot)

    session.calls.clear()
    await dp.feed_update(bot, give_press(server_id))

    assert not [t for name, t in session.calls if name == 'SendMessage'], session.calls
    assert any(name == 'EditMessageText' for name, _ in session.calls), session.calls
    assert 'Жду UUID' in session.last_text

    server = await container.private.servers.get(server_id)
    assert server['card_message_id'] == 13, 'карточку не запомнили'


async def test_the_squad_command_edits_the_card_and_removes_itself(admin_env):
    dp, bot, session, container = admin_env
    server_id = await _pending_server(container, bot)
    await dp.feed_update(bot, give_press(server_id))

    session.calls.clear()
    await dp.feed_update(bot, group_message(
        f'/squad {server_id} aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'))

    assert (await container.private.servers.get(server_id))['status'] == 'active'
    assert any(name == 'DeleteMessage' for name, _ in session.calls), \
        f'команда осталась в теме: {session.calls}'
    assert any('Сервер выдан' in text for name, text in session.calls
               if name == 'EditMessageText'), session.calls
    # владельцу сообщение уходит, а в тему — нет
    assert not [t for name, t in session.calls
                if name == 'SendMessage' and 'выдан' in t], session.calls


async def test_a_failed_handover_writes_into_the_card_too(admin_env):
    dp, bot, session, container = admin_env
    squad = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    taken = await _pending_server(container, bot)
    await container.private.activate(taken, squad)

    await container.users.create({'user_data': {'user_id': 558},
                                  'info': {'balance': 3000},
                                  'vpn': {'uuid': 'u-558', 'shortUuid': 's-558'}})
    second = (await container.private.request(558, 'company', location='tyo')).server
    await dp.feed_update(bot, give_press(second['_id'], message_id=14))

    session.calls.clear()
    await dp.feed_update(bot, group_message(f'/squad {second["_id"]} {squad}'))

    edits = [text for name, text in session.calls if name == 'EditMessageText']
    assert edits and 'Не вышло' in edits[-1], session.calls
    assert taken in edits[-1], 'кто занял машину — не написано'
    assert not [t for name, t in session.calls if name == 'SendMessage'], session.calls


# ── очередь и запас ─────────────────────────────────────────────────────────

async def test_the_queue_screen_says_what_to_raise(admin_env):
    dp, bot, session, container = admin_env
    await _pending_server(container, bot)

    await dp.feed_update(bot, callback(Adm(act='srvq').pack()))

    text = session.last_text
    assert 'Заявок ждёт: 1' in text
    assert 'Мини' in text and 'Амстердам' in text
    assert 'поднять машин: 1' in text


async def test_the_queue_screen_is_calm_when_there_is_nothing_to_do(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, callback(Adm(act='srvq').pack()))

    assert 'Заявок в работе нет' in session.last_text


async def test_a_spare_machine_is_added_and_listed(admin_env):
    dp, bot, session, container = admin_env
    container.attach_bot(bot)
    container.private.vpn = FakePanel()

    await dp.feed_update(bot, message(
        '/pooladd bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee tyo grpc'))
    assert 'в запасе' in session.last_text

    await dp.feed_update(bot, callback(Adm(act='srvq').pack()))
    assert 'Токио' in session.last_text and 'свободна' in session.last_text


async def test_adding_a_machine_serves_the_waiting_request(admin_env):
    """Заявка пришла раньше машины — выдаём в момент, когда машина появилась."""
    dp, bot, session, container = admin_env
    server_id = await _pending_server(container, bot)

    await dp.feed_update(bot, message(
        '/pooladd bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee ams reality'))

    server = await container.private.servers.get(server_id)
    assert server['status'] == 'active'
    assert server['squad_uuid'] == 'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee'
    assert 'Сразу выдана' in session.last_text


async def test_the_queue_screen_says_what_to_buy(admin_env):
    """Первое действие дня — купить машины, и это список по странам."""
    dp, bot, session, container = admin_env
    await _pending_server(container, bot)

    await dp.feed_update(bot, callback(Adm(act='srvq').pack()))

    text = session.last_text
    assert 'Купить машин' in text
    assert 'Амстердам' in text and '1 ТБ' in text
    assert 'TCP Reality' in text


async def test_a_machine_is_added_without_naming_the_country(admin_env):
    """Локацию и протокол бот берёт из панели: набирать их каждый раз —
    лишний повод ошибиться."""
    dp, bot, session, container = admin_env
    container.attach_bot(bot)

    class Panel(FakePanel):
        async def squad_nodes(self, squad_uuid):
            return [{'nodeName': 'RS Tokyo-1', 'countryCode': 'JP'}]

        async def squad(self, squad_uuid):
            return {'name': 'tokyo grpc'}

    container.private.vpn = Panel()

    await dp.feed_update(bot, message(
        '/pooladd bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee'))

    rows = await container.private.pool_rows()
    assert [(row['location'], row['profile']) for row in rows] == [('tyo', 'grpc')]
    assert 'Определено по панели' in session.last_text


async def test_an_ambiguous_country_asks_instead_of_guessing(admin_env):
    dp, bot, session, container = admin_env
    container.attach_bot(bot)

    class Panel(FakePanel):
        async def squad_nodes(self, squad_uuid):
            return [{'nodeName': 'node-de-1', 'countryCode': 'DE'}]

        async def squad(self, squad_uuid):
            return {'name': 'reality'}

    container.private.vpn = Panel()

    await dp.feed_update(bot, message(
        '/pooladd bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee'))

    assert not await container.private.pool_rows(), 'угадал вместо того, чтобы спросить'
    assert 'Не понял площадку' in session.last_text
    assert 'fra' in session.last_text


async def test_a_bad_location_is_refused(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, message(
        '/pooladd bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee луна reality'))

    assert 'локации нет' in session.last_text
    assert not await container.private.pool_rows()


async def test_a_spare_machine_can_be_removed(admin_env):
    dp, bot, session, container = admin_env
    await dp.feed_update(bot, message(
        '/pooladd bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee tyo grpc'))

    await dp.feed_update(bot, message(
        '/pooldel bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee'))

    assert not await container.private.pool_rows()


async def test_the_card_offers_one_tap_handover_when_a_spare_fits(admin_env):
    from app.admin.private_servers import card_markup

    dp, bot, session, container = admin_env
    server_id = await _pending_server(container, bot)
    server = await container.private.servers.get(server_id)

    markup = await card_markup(container, server)
    assert not any('запаса' in b.text for row in markup.inline_keyboard for b in row)

    await container.private.pool_add('bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee',
                                     'ams', 'reality')
    markup = await card_markup(container, server)
    assert any('запаса' in b.text for row in markup.inline_keyboard for b in row)


# ── справочник команд ───────────────────────────────────────────────────────

async def test_command_reference_lists_every_registered_command(admin_env):
    """Команда без строки в справочнике для админа не существует."""
    import re
    from pathlib import Path

    from app.admin.commands import text

    reference = text()
    registered = set()
    for path in Path('app').rglob('*.py'):
        registered |= set(re.findall(r"Command\('([a-z_]+)'\)", path.read_text()))

    # /commands и /help — сам справочник, его в себе перечислять незачем
    missing = {name for name in registered - {'commands', 'help'}
               if f'/{name}' not in reference}
    assert not missing, f'нет в справочнике: {sorted(missing)}'


async def test_command_reference_opens_from_the_panel(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, callback(Adm(act='cmds').pack()))

    assert 'Команды бота' in session.last_text
    assert '/srvdiag' in session.last_text


async def test_command_reference_has_a_command_of_its_own(admin_env):
    dp, bot, session, container = admin_env

    await dp.feed_update(bot, message('/commands'))

    assert '/deluser' in session.last_text


async def test_every_entry_explains_what_it_does(admin_env):
    """Список без объяснений — это тот же /help, за которым всё равно идти в код."""
    from app.admin.commands import SECTIONS

    for section in SECTIONS:
        for cmd in section.items:
            assert cmd.usage.startswith('/'), cmd
            assert len(cmd.what) > 15, cmd
