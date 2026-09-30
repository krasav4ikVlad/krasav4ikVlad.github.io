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

from aiogram import types

from app.content import ids
from app.core.time import fmt
from app.content.emoji import e, plain

log = logging.getLogger(__name__)


def user_link(user_id: int, username: str | None = None) -> str:
    name = f'@{username}' if username else 'без юзернейма'
    return f'{name} (<code>{user_id}</code>)'


def partner_topic(partner: dict, paid: bool) -> int:
    """В какую тему класть событие: регистрации и оплаты живут отдельно.

    Тема оплат не задана — кладём в тему регистраций: одна лента лучше
    потерянного события. Наоборот не работает, поэтому тема регистраций
    и остаётся основной.
    """
    reg = int((partner or {}).get('topic_id') or 0)
    pay = int((partner or {}).get('topic_pay_id') or 0)
    return (pay or reg) if paid else reg


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

    async def partner(self, user_id: int, text: str, paid: bool = False) -> bool:
        """Копия события в чат партнёра. Без чата и без метки — молчим.

        Не ломает ни событие, ни основное уведомление: не ушла копия —
        строка в лог.
        """
        tag, partner = await self._partner_of(user_id)
        chat_id = int((partner or {}).get('chat_id') or 0)
        if not tag or not chat_id:
            return False

        try:
            topic = partner_topic(partner, paid)
            with plain():
                await self.bot.send_message(
                    chat_id=chat_id, message_thread_id=topic or None,
                    text=f'{e("link")} <b>По метке</b> <code>{tag}</code>\n{text}')
            return True
        except Exception as exc:
            log.warning('копия события партнёру (%s) не ушла: %s', user_id, exc)
            return False

    async def about(self, topic: str, user_id: int, text: str,
                    paid: bool = False, copy: bool = True) -> bool:
        """Событие по человеку: в общий админ-чат и, если он пришёл по
        партнёрской метке, копией в чат этого партнёра.

        `copy=False` — событие остаётся только в общем админ-чате. Так
        уходят списания с баланса: см. комментарий у `renewed`.

        Возвращает судьбу основного уведомления: копия партнёру — дело
        второе, и её неудача не делает событие непрошедшим.
        """
        tag, partner = await self._partner_of(user_id)
        if tag:
            # Кому засчитан приведённый — главное, чего не хватало: по
            # одной метке не видно, на чей счёт идут проценты.
            owner = int((partner or {}).get('user_id') or 0)
            text += (f'\n{e("link")} <b>Метка:</b> <code>{tag}</code>'
                     + (f' → засчитан <code>{ids.show(owner)}</code>'
                        if owner else ''))
            if copy and int((partner or {}).get('chat_id') or 0):
                await self.partner(user_id, text, paid=paid)
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
        return await self.about('topup', user_id, text, paid=True)

    async def topup_try(self, user_id: int, amount: int, provider: str) -> bool:
        return await self.send('topup_try',
                               f'{e("receipt")} <b>Счёт выставлен</b>\n{await self._who(user_id)}\n'
                               f'<b>Сумма:</b> <code>{amount}₽</code>\n'
                               f'<b>Способ:</b> <code>{provider}</code>')

    # Покупки и автопродления в чат партнёра не идут — только в общий
    # админ-чат. Причина простая: это списания с баланса, а не пришедшие
    # деньги. Те же рубли партнёр уже видел пополнением, с них ему уже
    # посчитан процент, и второй раз они означают только одно — что в
    # ленте оплат больше строк, чем денег. Автопродление вдобавок идёт
    # каждый месяц само и заполняет тему целиком.

    async def subscription_created(self, user_id: int, plan: dict,
                                   subscription: dict | None = None) -> bool:
        text = (f'{e("shield")} <b>Покупка подписки</b>\n{await self._who(user_id)}\n'
                f'<b>Тариф:</b> <code>{(plan or {}).get("title", "")}</code>\n'
                f'<b>Действует до:</b> '
                f'<code>{fmt((subscription or {}).get("expireAt"))}</code>')
        return await self.about('subscription', user_id, text, copy=False)

    async def renewed(self, user_id: int, plan: dict, price: int, until=None) -> bool:
        text = (f'{e("renew")} <b>Автопродление</b>\n{await self._who(user_id)}\n'
                f'<b>Тариф:</b> <code>{(plan or {}).get("title", "")}</code>\n'
                f'<b>Списано:</b> <code>{price}₽</code>\n'
                f'<b>Действует до:</b> <code>{fmt(until)}</code>')
        return await self.about('subscription', user_id, text, copy=False)

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

    TORRENT_HEADS = {
        'warn': 'Торрент: предупреждение',
        'freeze': 'Торрент: подписка заморожена',
        'block': 'Торрент: подписка отключена',
    }

    async def torrent(self, user_id: int, count: int, node: str = '',
                      ip: str = '', step: str = 'warn') -> bool:
        """Торрент у клиента. В общий чат — потому что из-за этого банят
        сервер, и знать об этом надо раньше, чем придёт письмо от хостера."""
        mark = e('ban') if step == 'block' else e('attention')
        head = self.TORRENT_HEADS.get(step, self.TORRENT_HEADS['warn'])
        return await self.send('torrent',
                               f'{mark} <b>{head}</b>\n{await self._who(user_id)}\n'
                               f'<b>Нарушение:</b> <code>{count}</code>\n'
                               + (f'<b>Нода:</b> <code>{node}</code>\n' if node else '')
                               + (f'<b>IP:</b> <code>{ip}</code>' if ip else ''))

    async def torrent_appeal(self, user_id: int, stats: dict, hint: str,
                             code: str = '', locked: bool = False,
                             ladder: int = 0) -> bool:
        """«Я не качаю торренты». Проверить это нечем — решает человек.

        Поэтому в карточке не вердикт, а то, по чему решают: сколько
        отчётов, за какой срок, с каких нод. Настоящая раздача даёт их
        десятками и с разных серверов; ложное срабатывание — один и тишину.
        """
        from aiogram.utils.keyboard import InlineKeyboardBuilder

        from app.bot.callbacks import Torrent

        nodes = [str(name) for name in (stats.get('nodes') or []) if name]
        lines = [f'{e("question")} <b>Жалоба на ложное срабатывание</b>',
                 await self._who(user_id),
                 f'<b>По лестнице:</b> <code>{ladder}</code>, '
                 f'всего <code>{stats.get("count", 0)}</code>, '
                 f'отчётов <code>{stats.get("reports", 0)}</code>',
                 f'<b>Последний:</b> {fmt(stats.get("last_at"))}'
                 + (f', нода {stats.get("last_node")}' if stats.get('last_node') else ''),
                 (f'<b>Ноды:</b> {", ".join(nodes[:5])}' if len(nodes) > 1 else ''),
                 f'<b>Статус:</b> ' + ('подписка отключена' if locked
                                       else 'доступ работает'),
                 '', f'<blockquote>{hint}</blockquote>']

        if stats.get('forgiven'):
            # Прощали раньше — это важнее любой подсказки: второй заход
            # с той же жалобой выглядит иначе, чем первый.
            lines.insert(-2, f'{e("warning")} <b>Уже прощали:</b> '
                             f'<code>{stats["forgiven"]}</code> раз')

        kb = InlineKeyboardBuilder()
        kb.row(types.InlineKeyboardButton(
            text=f'{e("ok")} Сбросить предупреждения',
            callback_data=Torrent(action='trust', user_id=user_id).pack()))
        kb.row(types.InlineKeyboardButton(
            text=f'{e("cross")} Отказать',
            callback_data=Torrent(action='reject', user_id=user_id).pack()))
        return await self.send('torrent',
                               '\n'.join(line for line in lines if line != ''),
                               markup=kb.as_markup())

    # ── копии базы ──────────────────────────────────────────────────────────
    async def backup_done(self, name: str, size: int, docs: int,
                          seconds: float = 0, removed: int = 0) -> bool:
        from app.services.backup import human_size

        return await self.send(
            'backup',
            f'{e("document")} <b>Копия базы готова</b>\n'
            f'<code>{name}</code>\n'
            f'<b>Размер:</b> {human_size(size)}\n'
            f'<b>Документов:</b> <code>{docs}</code>\n'
            f'<b>Заняло:</b> {seconds:.0f} с'
            + (f'\n<i>Старых удалено: {removed}</i>' if removed else ''))

    async def backup_failed(self, error: str) -> bool:
        """Провал — громко. Молчащий бекап неотличим от работающего ровно
        до того дня, когда он понадобится."""
        return await self.send(
            'backup',
            f'{e("attention")} <b>Копия базы НЕ сделана</b>\n'
            f'<code>{(error or "причина неизвестна")[:300]}</code>\n\n'
            f'Проверьте место на диске: <code>df -h</code>. '
            f'Сделать вручную — <code>/backup</code>.')

    async def backup_file(self, path: str, limit_mb: int = 45,
                          chat_ids=None, backup=None) -> int:
        """Сам файл — в личку админам (или в чат, если адресаты не заданы).

        Возвращает, скольким дошло. Большой файл режется на части: Telegram
        не принимает документы больше 50 МБ, а «слишком большой» означало бы,
        что копии в телефоне нет именно тогда, когда нет и сервера.

        В снимке вся база: адреса почты, платежи, переписка с поддержкой.
        Поэтому адресаты — только админы из .env, и никогда не «кому-то ещё».
        """
        from pathlib import Path

        item = Path(path)
        if not item.exists():
            return 0

        targets = [int(one) for one in (chat_ids or []) if one]
        if not targets:
            chat_id = await self.settings.int('notify.chat_id')
            if not chat_id or not await self.settings.flag('notify.enabled'):
                return 0
            targets = [chat_id]

        limit = max(1, int(limit_mb or 45)) * 1024 * 1024
        parts: list = []
        if item.stat().st_size > limit and backup is not None:
            parts = await backup.split(item, limit)

        try:
            files = parts or [item]
            total = len(files)
            sent = 0
            for target in targets:
                if await self._send_files(target, files, total, item.name):
                    sent += 1
            return sent
        finally:
            if parts and backup is not None:
                backup.drop_parts(parts)

    async def _send_files(self, chat_id: int, files: list, total: int,
                          name: str) -> bool:
        from aiogram.types import FSInputFile

        for number, item in enumerate(files, 1):
            caption = (f'{e("document")} Копия базы\n<code>{name}</code>'
                       if total == 1 else
                       f'{e("document")} Копия базы, часть {number} из {total}\n'
                       f'<code>{item.name}</code>')
            if number == total and total > 1:
                # Инструкция идёт с последней частью: пока части ещё идут,
                # она только мешает, а после — это первое, что нужно.
                caption += (f'\n\nСобрать обратно:\n'
                            f'<code>cat {name}.* > {name}</code>')
            try:
                with plain():
                    await self.bot.send_document(
                        chat_id=chat_id, document=FSInputFile(str(item)),
                        caption=caption)
            except Exception as exc:
                log.warning('копия базы не отправлена в %s: %s', chat_id, exc)
                return False
        return True

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
