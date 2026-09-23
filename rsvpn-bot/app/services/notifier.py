"""Уведомления в админ-чат.

Сервисы уже вызывали `self.notifier.<событие>()` — но самого класса в проекте
не было, а `container.notifier` оставался None. Из-за этого молча не работали
ВСЕ админ-уведомления: регистрации, пополнения, покупки, доп. устройства,
подарки, почта и — главное — заявки на вывод (админ просто не узнавал о них).

Одно место сборки текста и один способ отправки: раньше `bot.send_message`
с зашитым chat_id и номером темы был скопирован по файлам примерно
двадцать раз, и любая правка номера темы означала обход всех копий.

Правило: уведомление не должно ломать основной сценарий. Не ушло — пишем
в лог и возвращаем False; исключение наружу не выпускаем. Единственное
место, где ответ важен, — заявка на вывод: там бот снимает метку заявки,
если админы её не получили.
"""

from __future__ import annotations

import logging

from app.core.time import fmt
from app.content.emoji import e, plain

log = logging.getLogger(__name__)


def user_link(user_id: int, username: str | None = None) -> str:
    name = f'@{username}' if username else 'без юзернейма'
    return f'{name} (<code>{user_id}</code>)'


class Notifier:
    def __init__(self, bot, settings, users=None, ref_tags=None):
        self.bot = bot
        self.settings = settings
        self.users = users
        # Метки партнёров: у каждой может быть свой чат (и тема), куда идёт
        # копия событий по её людям. Партнёров несколько, и смешивать их в
        # одной ленте — значит не видеть ни одного.
        self.ref_tags = ref_tags

    # ── отправка ────────────────────────────────────────────────────────────
    async def send(self, topic: str, text: str, markup=None) -> bool:
        """Сообщение в тему админ-чата. Возвращает, дошло ли."""
        return await self.post(topic, text, markup) is not None

    async def post(self, topic: str, text: str, markup=None):
        """То же, но возвращает само сообщение — карточки правятся на месте.

        Заявку на сервер потом дополняют («выдан», «ошибка»), а для правки
        нужны chat_id и message_id: без них в теме копятся ответы бота вместо
        одной живой карточки.
        """
        if not await self.settings.flag('notify.enabled'):
            return None

        chat_id = await self.settings.int('notify.chat_id')
        if not chat_id:
            return None

        try:
            # адресат — админ-чат, а он живёт на обычных значках: см.
            # PlainEmojiMiddleware. Заявку на вывод нельзя терять из-за
            # оформления, а кастомные эмодзи Telegram иногда отклоняет
            with plain():
                return await self.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=await self.settings.int(f'notify.topic_{topic}') or None,
                    text=text, reply_markup=markup)
        except Exception as exc:
            log.warning('уведомление «%s» не отправлено: %s', topic, exc)
            return None

    # ── события по людям партнёра ───────────────────────────────────────────
    #
    # Уходят в ДВА места, а не в одно. В общий админ-чат — потому что это
    # обычное событие бота и в общей ленте ему место; метка там дописывается
    # строкой, иначе партнёрскую покупку не отличить от любой другой. В чат
    # партнёра — потому что в общем потоке его полтора десятка человек в
    # день теряются среди полутора тысяч чужих.

    async def _partner_of(self, user_id: int) -> tuple[str, dict]:
        """Метка человека и настройки её партнёра. ('', {}) — метки нет."""
        if self.ref_tags is None or self.users is None or not user_id:
            return '', {}
        try:
            user = await self.users.get(int(user_id), {'user_data.ref_tag': 1})
            tag = str(self.users.pick(user or {}, 'user_data.ref_tag') or '')
            if not tag:
                return '', {}
            return tag, (await self.ref_tags.get(tag)) or {}
        except Exception as exc:
            log.warning('метка пользователя %s не прочитана: %s', user_id, exc)
            return '', {}

    async def partner(self, user_id: int, text: str) -> bool:
        """Копия события в чат партнёра. Без чата и без метки — молчим.

        Не ломает ни событие, ни основное уведомление: не ушла копия —
        строка в лог.
        """
        tag, partner = await self._partner_of(user_id)
        chat_id = int((partner or {}).get('chat_id') or 0)
        if not tag or not chat_id:
            return False

        try:
            topic = int((partner or {}).get('topic_id') or 0)
            with plain():
                await self.bot.send_message(
                    chat_id=chat_id, message_thread_id=topic or None,
                    text=f'{e("link")} <b>По метке</b> <code>{tag}</code>\n{text}')
            return True
        except Exception as exc:
            log.warning('копия события партнёру (%s) не ушла: %s', user_id, exc)
            return False

    async def about(self, topic: str, user_id: int, text: str) -> bool:
        """Событие по человеку: в общий админ-чат и, если он пришёл по
        партнёрской метке, копией в чат этого партнёра.

        Возвращает судьбу основного уведомления: копия партнёру — дело
        второе, и её неудача не делает событие непрошедшим.
        """
        tag, partner = await self._partner_of(user_id)
        if tag:
            text += f'\n{e("link")} <b>Метка:</b> <code>{tag}</code>'
            if int((partner or {}).get('chat_id') or 0):
                await self.partner(user_id, text)
        return await self.send(topic, text)

    async def _who(self, user_id: int, username: str | None = None) -> str:
        """Подпись пользователя. Юзернейм берём из базы, если не передали."""
        if username is None and self.users is not None:
            user = await self.users.get(user_id, {'user_data.username': 1})
            username = self.users.pick(user or {}, 'user_data.username')
        return user_link(user_id, username)

    # ── события ─────────────────────────────────────────────────────────────
    async def registered(self, user_id: int, username: str | None = None,
                         utm: str = '') -> bool:
        text = (f'{e("user")} <b>Новый пользователь</b>\n'
                f'{user_link(user_id, username)}'
                + (f'\n<b>UTM:</b> <code>{utm}</code>' if utm else ''))
        return await self.about('registration', user_id, text)

    async def topup(self, user_id: int, amount: int, bonus: int, credit: int,
                    provider: str) -> bool:
        text = (f'{e("money")} <b>Пополнение</b>\n{await self._who(user_id)}\n'
                f'<b>Оплачено:</b> <code>{amount}₽</code>\n'
                f'<b>Зачислено:</b> <code>{credit}₽</code>\n'
                f'<b>Способ:</b> <code>{provider}</code>')
        if bonus:
            text += f'\n<b>Бонус:</b> <code>{bonus}₽</code>'
        return await self.about('topup', user_id, text)

    async def topup_try(self, user_id: int, amount: int, provider: str) -> bool:
        return await self.send('topup_try',
                               f'{e("receipt")} <b>Счёт выставлен</b>\n{await self._who(user_id)}\n'
                               f'<b>Сумма:</b> <code>{amount}₽</code>\n'
                               f'<b>Способ:</b> <code>{provider}</code>')

    async def subscription_created(self, user_id: int, plan: dict,
                                   subscription: dict | None = None) -> bool:
        text = (f'{e("shield")} <b>Покупка подписки</b>\n{await self._who(user_id)}\n'
                f'<b>Тариф:</b> <code>{(plan or {}).get("title", "")}</code>\n'
                f'<b>Действует до:</b> '
                f'<code>{fmt((subscription or {}).get("expireAt"))}</code>')
        return await self.about('subscription', user_id, text)

    async def renewed(self, user_id: int, plan: dict, price: int, until=None) -> bool:
        text = (f'{e("renew")} <b>Автопродление</b>\n{await self._who(user_id)}\n'
                f'<b>Тариф:</b> <code>{(plan or {}).get("title", "")}</code>\n'
                f'<b>Списано:</b> <code>{price}₽</code>\n'
                f'<b>Действует до:</b> <code>{fmt(until)}</code>')
        return await self.about('subscription', user_id, text)

    async def devices_charged(self, user_id: int, amount: int, price: int,
                              next_charge=None) -> bool:
        return await self.send('devices',
                               f'{e("devices")} <b>Плата за доп. устройства</b>\n{await self._who(user_id)}\n'
                               f'<b>Устройств:</b> <code>{amount}</code>\n'
                               f'<b>Списано:</b> <code>{price}₽</code>\n'
                               f'<b>Следующее списание:</b> <code>{fmt(next_charge)}</code>')

    async def devices_removed(self, user_id: int, amount: int, limit: int) -> bool:
        return await self.send('devices',
                               f'{e("devices")} <b>Доп. устройства отключены</b>\n{await self._who(user_id)}\n'
                               f'<b>Снято:</b> <code>{amount}</code>\n'
                               f'<b>Новый лимит:</b> <code>{limit}</code>')

    async def gift_accepted(self, from_user_id: int, to_user_id: int,
                            plan: dict | None = None) -> bool:
        return await self.send('subscription',
                               f'{e("gift")} <b>Подарок принят</b>\n'
                               f'<b>От:</b> {await self._who(from_user_id)}\n'
                               f'<b>Кому:</b> {await self._who(to_user_id)}\n'
                               f'<b>Тариф:</b> <code>{(plan or {}).get("title", "")}</code>')

    async def email_changed(self, user_id: int, email: str) -> bool:
        return await self.send('email',
                               f'{e("email")} <b>Почта привязана</b>\n{await self._who(user_id)}\n'
                               f'<code>{email}</code>')

    async def promo_used(self, user_id: int, code: str, reward: str) -> bool:
        return await self.send('promo',
                               f'{e("promo")} <b>Промокод</b>\n{await self._who(user_id)}\n'
                               f'<b>Код:</b> <code>{code}</code>\n<b>Награда:</b> {reward}')

    async def campaign_report(self, report) -> bool:
        steps = [s for s in (getattr(report, 'steps', None) or []) if s.sent or s.failed]
        lines = [f'• {s.step}: отправлено {s.sent}, не дошло {s.failed}' for s in steps]
        return await self.send('campaigns',
                               f'{e("megaphone")} <b>Кампании отработали</b>\n'
                               f'<b>Отправлено:</b> <code>{getattr(report, "sent", 0)}</code>\n'
                               f'<b>Начислено:</b> <code>{getattr(report, "credited", 0)}₽</code>'
                               + ('\n\n' + '\n'.join(lines) if lines else ''))

    # ── заявка на вывод: единственное место, где важен ответ ────────────────
    async def payout_requested(self, text: str, markup=None) -> bool:
        """Карточку собирает admin/payouts.py и передаёт готовой.

        Здесь ей не место: карточка знает про баланс, реквизиты и кнопки
        решения — это админский экран, а не уведомление. Notifier остаётся
        тем, чем был: одним способом положить сообщение в тему админ-чата.
        """
        return await self.send('payout', text, markup=markup)
