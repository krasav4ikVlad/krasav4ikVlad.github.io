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

log = logging.getLogger(__name__)


def user_link(user_id: int, username: str | None = None) -> str:
    name = f'@{username}' if username else 'без юзернейма'
    return f'{name} (<code>{user_id}</code>)'


class Notifier:
    def __init__(self, bot, settings, users=None):
        self.bot = bot
        self.settings = settings
        self.users = users

    # ── отправка ────────────────────────────────────────────────────────────
    async def send(self, topic: str, text: str, markup=None) -> bool:
        """Сообщение в тему админ-чата. Возвращает, дошло ли."""
        if not await self.settings.flag('notify.enabled'):
            return False

        chat_id = await self.settings.int('notify.chat_id')
        if not chat_id:
            return False

        try:
            await self.bot.send_message(
                chat_id=chat_id,
                message_thread_id=await self.settings.int(f'notify.topic_{topic}') or None,
                text=text, reply_markup=markup)
            return True
        except Exception as exc:
            log.warning('уведомление «%s» не отправлено: %s', topic, exc)
            return False

    async def _who(self, user_id: int, username: str | None = None) -> str:
        """Подпись пользователя. Юзернейм берём из базы, если не передали."""
        if username is None and self.users is not None:
            user = await self.users.get(user_id, {'user_data.username': 1})
            username = self.users.pick(user or {}, 'user_data.username')
        return user_link(user_id, username)

    # ── события ─────────────────────────────────────────────────────────────
    async def registered(self, user_id: int, username: str | None = None,
                         utm: str = '') -> bool:
        return await self.send('registration',
                               f'👤 <b>Новый пользователь</b>\n{user_link(user_id, username)}'
                               + (f'\n<b>UTM:</b> <code>{utm}</code>' if utm else ''))

    async def topup(self, user_id: int, amount: int, bonus: int, credit: int,
                    provider: str) -> bool:
        text = (f'💰 <b>Пополнение</b>\n{await self._who(user_id)}\n'
                f'<b>Оплачено:</b> <code>{amount}₽</code>\n'
                f'<b>Зачислено:</b> <code>{credit}₽</code>\n'
                f'<b>Способ:</b> <code>{provider}</code>')
        if bonus:
            text += f'\n<b>Бонус:</b> <code>{bonus}₽</code>'
        return await self.send('topup', text)

    async def topup_try(self, user_id: int, amount: int, provider: str) -> bool:
        return await self.send('topup_try',
                               f'🧾 <b>Счёт выставлен</b>\n{await self._who(user_id)}\n'
                               f'<b>Сумма:</b> <code>{amount}₽</code>\n'
                               f'<b>Способ:</b> <code>{provider}</code>')

    async def subscription_created(self, user_id: int, plan: dict,
                                   subscription: dict | None = None) -> bool:
        return await self.send('subscription',
                               f'🛡 <b>Покупка подписки</b>\n{await self._who(user_id)}\n'
                               f'<b>Тариф:</b> <code>{(plan or {}).get("title", "")}</code>\n'
                               f'<b>Действует до:</b> '
                               f'<code>{fmt((subscription or {}).get("expireAt"))}</code>')

    async def renewed(self, user_id: int, plan: dict, price: int, until=None) -> bool:
        return await self.send('subscription',
                               f'🔁 <b>Автопродление</b>\n{await self._who(user_id)}\n'
                               f'<b>Тариф:</b> <code>{(plan or {}).get("title", "")}</code>\n'
                               f'<b>Списано:</b> <code>{price}₽</code>\n'
                               f'<b>Действует до:</b> <code>{fmt(until)}</code>')

    async def devices_charged(self, user_id: int, amount: int, price: int,
                              next_charge=None) -> bool:
        return await self.send('devices',
                               f'📲 <b>Плата за доп. устройства</b>\n{await self._who(user_id)}\n'
                               f'<b>Устройств:</b> <code>{amount}</code>\n'
                               f'<b>Списано:</b> <code>{price}₽</code>\n'
                               f'<b>Следующее списание:</b> <code>{fmt(next_charge)}</code>')

    async def devices_removed(self, user_id: int, amount: int, limit: int) -> bool:
        return await self.send('devices',
                               f'📲 <b>Доп. устройства отключены</b>\n{await self._who(user_id)}\n'
                               f'<b>Снято:</b> <code>{amount}</code>\n'
                               f'<b>Новый лимит:</b> <code>{limit}</code>')

    async def gift_accepted(self, from_user_id: int, to_user_id: int,
                            plan: dict | None = None) -> bool:
        return await self.send('subscription',
                               f'🎁 <b>Подарок принят</b>\n'
                               f'<b>От:</b> {await self._who(from_user_id)}\n'
                               f'<b>Кому:</b> {await self._who(to_user_id)}\n'
                               f'<b>Тариф:</b> <code>{(plan or {}).get("title", "")}</code>')

    async def email_changed(self, user_id: int, email: str) -> bool:
        return await self.send('email',
                               f'✉️ <b>Почта привязана</b>\n{await self._who(user_id)}\n'
                               f'<code>{email}</code>')

    async def promo_used(self, user_id: int, code: str, reward: str) -> bool:
        return await self.send('promo',
                               f'🎟 <b>Промокод</b>\n{await self._who(user_id)}\n'
                               f'<b>Код:</b> <code>{code}</code>\n<b>Награда:</b> {reward}')

    async def campaign_report(self, report) -> bool:
        steps = [s for s in (getattr(report, 'steps', None) or []) if s.sent or s.failed]
        lines = [f'• {s.step}: отправлено {s.sent}, не дошло {s.failed}' for s in steps]
        return await self.send('campaigns',
                               f'📣 <b>Кампании отработали</b>\n'
                               f'<b>Отправлено:</b> <code>{getattr(report, "sent", 0)}</code>\n'
                               f'<b>Начислено:</b> <code>{getattr(report, "credited", 0)}₽</code>'
                               + ('\n\n' + '\n'.join(lines) if lines else ''))

    # ── заявка на вывод: единственное место, где важен ответ ────────────────
    async def payout_requested(self, user_id: int, amount: int, method: str,
                               details: str = '', username: str | None = None) -> bool:
        from app.bot.keyboards.payouts import payout_card_keyboard

        text = (f'📤 <b>Заявка на вывод</b>\n{await self._who(user_id, username)}\n'
                f'<b>Сумма:</b> <code>{amount}₽</code>\n\n'
                f'{details or method}')
        return await self.send('payout', text, markup=payout_card_keyboard(user_id))
