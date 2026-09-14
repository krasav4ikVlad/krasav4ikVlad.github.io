"""Партнёрский бот: подключить, ответить, отключить.

Такой бот делает ровно одно — здоровается и даёт кнопку в основного бота с
меткой партнёра. Поэтому здесь нет ни aiogram, ни Dispatcher: два HTTP-вызова
к Telegram напрямую проще и переживают что угодно, включая недоступную базу
подписок.

Почему вебхук, а не опрос: опрос — это отдельное живое соединение на каждого
партнёра и отдельная задача в процессе. Вебхук же приходит в тот же API, что
уже принимает платежи, и ничего не стоит, пока по нему не пишут.
"""

from __future__ import annotations

import logging
import secrets

from app.domain import ref_tags

log = logging.getLogger(__name__)

API = 'https://api.telegram.org/bot{token}/{method}'

# Путь вебхука. Секрет в адресе, а не токен: адрес видно в логах прокси, а
# по токену можно управлять чужим ботом.
WEBHOOK_PATH = '/partner/{secret}'
SECRET_HEADER = 'X-Telegram-Bot-Api-Secret-Token'


def new_secret() -> str:
    return secrets.token_urlsafe(24)


def webhook_url(base: str, secret: str) -> str:
    return base.rstrip('/') + WEBHOOK_PATH.format(secret=secret)


class PartnerBotService:
    def __init__(self, bots, http, settings):
        self.bots = bots
        self.http = http
        self.settings = settings

    # ── разговор с Telegram ────────────────────────────────────────────────
    async def _call(self, token: str, method: str, **payload) -> dict:
        response = await self.http.post(API.format(token=token, method=method),
                                        json=payload)
        try:
            data = response.json()
        except Exception:
            return {'ok': False, 'description': 'ответ не JSON'}
        return data if isinstance(data, dict) else {'ok': False}

    # ── подключение ────────────────────────────────────────────────────────
    async def connect(self, token: str, tag: str, user_id: int) -> dict:
        """Завести партнёрского бота под метку. Возвращает результат словарём.

        Сначала спрашиваем у Telegram, чей это токен: набранный с опечаткой
        токен иначе тихо лёг бы в базу, а партнёр узнал бы об этом от своих
        подписчиков.
        """
        token = (token or '').strip()
        if ':' not in token:
            return {'ok': False, 'reason': 'bad_token'}

        me = await self._call(token, 'getMe')
        if not me.get('ok'):
            return {'ok': False, 'reason': 'telegram',
                    'note': me.get('description') or 'Telegram не принял токен'}

        username = (me.get('result') or {}).get('username') or ''
        title = (me.get('result') or {}).get('first_name') or ''
        secret = new_secret()

        base = str(await self.settings.get('pay.callback_base') or '').strip()
        if not base:
            return {'ok': False, 'reason': 'no_base'}

        if not await self.bots.add(token, secret, ref_tags.normalize(tag),
                                   user_id, username, title):
            return {'ok': False, 'reason': 'exists', 'note': username}

        hooked = await self._call(
            token, 'setWebhook',
            url=webhook_url(base, secret),
            secret_token=secret,
            allowed_updates=['message'],
            drop_pending_updates=True,
        )
        if not hooked.get('ok'):
            # Запись без вебхука — бот, который молчит. Убираем, чтобы в
            # списке не было мёртвых строк.
            await self.bots.remove(username)
            return {'ok': False, 'reason': 'webhook',
                    'note': hooked.get('description') or 'вебхук не поставился'}

        return {'ok': True, 'username': username, 'title': title,
                'secret': secret, 'tag': ref_tags.normalize(tag)}

    async def disconnect(self, username_or_tag: str) -> dict | None:
        """Убрать бота и снять вебхук, чтобы он перестал отвечать."""
        found = await self.bots.remove(username_or_tag)
        if not found:
            return None
        await self._call(found['token'], 'deleteWebhook', drop_pending_updates=True)
        return found

    # ── ответ человеку ─────────────────────────────────────────────────────
    async def greeting(self, tag: str) -> tuple[str, dict]:
        """Что показывает партнёрский бот: текст и кнопка в основного."""
        text = str(await self.settings.get('partner.greeting') or '').strip()
        button = str(await self.settings.get('partner.button') or 'Открыть').strip()
        main_bot = str(await self.settings.get('link.bot_username') or '').strip()

        markup = {'inline_keyboard': [[{
            'text': button, 'url': ref_tags.link(main_bot, tag)}]]}
        return text, markup

    async def handle(self, secret: str, update: dict) -> bool:
        """Обработать обновление партнёрского бота. False — бот неизвестен.

        Отвечаем на любое сообщение, а не только на /start: человек, попавший
        в такой бот, часто пишет «привет» или жмёт меню — и молчание в ответ
        он считает поломкой, а партнёр теряет приведённого.
        """
        bot = await self.bots.by_secret(secret)
        if not bot:
            return False

        message = (update or {}).get('message') or {}
        chat_id = ((message.get('chat') or {}).get('id'))
        if not chat_id:
            return True                 # не сообщение — нам нечего отвечать

        text, markup = await self.greeting(bot.get('tag') or '')
        try:
            await self._call(bot['token'], 'sendMessage', chat_id=chat_id,
                             text=text, parse_mode='HTML', reply_markup=markup)
        except Exception as exc:
            log.warning('партнёрский бот %s не ответил: %s',
                        bot.get('username'), exc)

        if str(message.get('text') or '').startswith('/start'):
            await self.bots.count_start(secret)
        return True
