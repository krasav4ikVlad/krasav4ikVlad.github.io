"""Реестр текстов.

Правило: ни одной длинной строки в хендлерах и кампаниях — только ключ.
Тогда тексты можно (а) читать все сразу, (б) править из админки без деплоя,
(в) не дублировать одну формулировку в пяти местах.

Плейсхолдеры: {name_comma} {name} {balance} {credited} {total} {price} {days}
{support_url} {channel_url} — недостающие подставляются пустой строкой,
поэтому опечатка в шаблоне не роняет отправку.

Значки тоже плейсхолдеры: {gift}, {clock}, {ok} и остальные имена из
app/content/emoji.py.Писать символ прямо в шаблон не нужно — тогда он
окажется мимо общего реестра, и поменять его в одном месте не выйдет.
Заодно так их можно ставить и в текстах, которые правятся из админки.
"""

from __future__ import annotations

from dataclasses import dataclass
from string import Formatter

from app.content.emoji import EMOJI, e


@dataclass(frozen=True)
class Text:
    key: str
    title: str          # человеческое название для админки
    default: str
    multi: str = ''     # вариант для тех, кто платил 2+ раза (необязательно)


TEXTS: tuple[Text, ...] = (
    # ── профиль и подписка ──────────────────────────────────────────────────
    Text('screen.profile.caption', 'Профиль — подпись',
         '<b>{user} Профиль</b>\n\n'
         '<b>{id} Идентификатор:</b> <code>{user_id}</code>\n'
         '<b>{money} Баланс:</b> <code>{balance}₽</code>\n'
         '<b>{friends} Друзей:</b> <code>{friends}</code>\n'
         '<b>{email} Почта:</b> <code>{email}</code>\n'),
    Text('screen.subscription.empty', 'Экран выбора тарифа',
         'Выберите длительность подписки. При покупке тарифа со значком {gift} '
         'вы получите подарочную подписку для друга.'),
    Text('screen.subscription.change_period', 'Смена длительности',
         '{calendar} Выберите, на какой срок продлевать подписку в следующий раз. '
         'Деньги сейчас не списываются, текущая дата окончания не меняется — '
         'новый срок применится при ближайшем продлении.'),
    Text('screen.subscription.expired', 'Подписка истекла',
         '{warning}Ваша подписка истекла. Продлите её кнопкой «Продлить подписку».'),
    Text('screen.balance.not_enough', 'Не хватает средств',
         '{warning}На балансе не хватает {missing}₽. Пополните баланс и вернитесь к покупке.'),
    Text('screen.devices.hint', 'Менеджер устройств',
         'Каждое устройство сверх {free_devices} стоит {device_price}₽ в месяц.'),
    Text('error.generic', 'Общая ошибка',
         'Что-то пошло не так. Попробуйте ещё раз или напишите в поддержку.'),

    # ── напоминания об истечении (вебхуки панели) ───────────────────────────
    Text('expiry.3d', 'Истекает через 3 дня',
         '{name_comma}подписка RS VPN заканчивается <b>через 3 дня</b> — {expire}.\n\n'
         'На балансе <b>{balance}₽</b>. Продлите заранее, чтобы доступ не прерывался {down}'),
    Text('expiry.2d', 'Истекает через 2 дня',
         '{name_comma}подписка заканчивается <b>послезавтра</b> — {expire}.\n\n'
         'На балансе <b>{balance}₽</b> {down}'),
    Text('expiry.1d', 'Истекает через сутки',
         '{name_comma}подписка RS VPN заканчивается <b>завтра</b> ({expire}).\n\n'
         'На балансе <b>{balance}₽</b>. Один клик — и всё продолжит работать {down}'),
    Text('expiry.12h', 'Истекает через 12 часов',
         '{name_comma}до конца подписки <b>меньше 12 часов</b>.\n\n'
         'Продлите сейчас, чтобы не остаться без доступа {down}'),
    Text('expiry.6h', 'Истекает через 6 часов',
         '{clock} {name_comma}до конца подписки <b>меньше 6 часов</b>.\n\n'
         'На балансе <b>{balance}₽</b> {down}'),
    Text('expiry.3h', 'Истекает через 3 часа',
         '{clock} {name_comma}подписка отключится <b>через 3 часа</b>.\n\nПродлите в один клик {down}'),
    Text('expiry.1h', 'Истекает через час',
         '{attention} {name_comma}подписка отключится <b>через час</b>.\n\n'
         'После этого сайты снова станут недоступны {down}'),
    Text('expiry.expired', 'Подписка истекла',
         '{name_comma}подписка RS VPN <b>закончилась</b>.\n\n'
         'Продлите — и доступ вернётся сразу, настраивать заново ничего не нужно {down}'),
    Text('expiry.expired_24h', 'Истекла сутки назад',
         '{name_comma}сутки без RS VPN.\n\n'
         'На балансе <b>{balance}₽</b>. Вернуть быстрый интернет — один клик {down}'),
    Text('expiry.expired_72h', 'Истекла три дня назад',
         '{name_comma}три дня без RS VPN.\n\nМы сохранили ваши настройки — '
         'продлите, и всё заработает как раньше {down}'),

    # ── кампания: новички на триале ─────────────────────────────────────────
    Text('campaign.trial.d0', 'Триал D0 — через 2 часа',
         '{name_comma}как вам RS VPN? {rocket}\n\n'
         'Надеемся, всё работает — сайты и сервисы снова доступны без ограничений.\n\n'
         'У вас есть ещё <b>2 дня бесплатного периода</b>. '
         'Подключайте на все свои устройства {down}'),
    Text('campaign.trial.d1', 'Триал D1 — через сутки',
         'Добрый день! {wave}\n\n'
         '{name_comma}вы уже сутки пользуетесь RS VPN — и всё это время '
         'интернет работал без блокировок {globe}\n\n'
         'Осталось <b>2 дня бесплатного периода</b>.'),
    Text('campaign.trial.d2', 'Триал D2 — предпоследний день',
         '{name_comma}у вас остался последний день бесплатного периода.\n\n'
         'Пополните баланс сегодня — начислим бонус сверху {gift}\n\n'
         'Предложение сгорает в полночь {down}'),
    Text('campaign.trial.d2_hot', 'Триал D2 HOT — 6 часов',
         '{clock} <b>Осталось меньше 6 часов</b>\n\n'
         'Бонус к пополнению заканчивается сегодня ночью. '
         'После — стандартные условия.'),
    Text('campaign.trial.d3', 'Триал D3 — последний день',
         '{name_comma}сегодня последний день бесплатного периода.\n\n'
         'Завтра RS VPN отключится, и сайты снова станут недоступны.\n\n'
         'Чтобы ничего не изменилось — пополните баланс {down}'),
    Text('campaign.trial.d3_hot', 'Триал D3 HOT — 2 часа',
         '{attention} <b>Через 2 часа VPN отключится</b>\n\n'
         'Продлите прямо сейчас — это дешевле чашки кофе за день {down}'),

    # ── кампания: истёкшие подписки ─────────────────────────────────────────
    Text('campaign.expired.d1', 'Истекли 1 день',
         '{name_comma}ваша подписка RS VPN закончилась вчера.\n\n'
         'Может, просто забыли продлить? Бывает {smile}\n\nОдин клик — и всё как раньше {down}',
         multi='{name_comma}вы уже не первый раз с нами — и вчера подписка закончилась.\n\n'
               'Просто продлите: один клик и всё работает {down}'),
    Text('campaign.expired.d3', 'Истекли 3 дня',
         '{name_comma}уже 3 дня без RS VPN.\n\n'
         'Мы начислили вам <b>{credited}₽</b> — на балансе теперь <b>{total}₽</b> {gift}\n\n'
         'Нажмите кнопку — подписка включится за секунду {down}'),
    Text('campaign.expired.d7', 'Истекла неделя',
         '{name_comma}прошла неделя без RS VPN.\n\n'
         'Мы начислили вам <b>{credited}₽</b> — на балансе <b>{total}₽</b> {gift}\n\n'
         'Подключитесь прямо сейчас {down}'),
    Text('campaign.expired.d14', 'Истекли 2 недели',
         '{name_comma}прошло две недели без RS VPN.\n\n'
         'Вернуть интернет без ограничений — один клик {down}'),
    Text('campaign.expired.d21', 'Истекли 3 недели',
         '{name_comma}уже 3 недели без RS VPN.\n\n'
         'Часть сайтов и сервисов всё это время остаётся недоступной. '
         'Вернуться — один клик {down}'),
    Text('campaign.expired.d30', 'Истёк месяц',
         '{name_comma}месяц без RS VPN.\n\nВозвращайтесь — один клик {down}'),
    Text('campaign.churned.d45', 'Ушли 45 дней',
         '{name_comma}больше месяца без RS VPN.\n\n'
         'Мы обновили серверы и протоколы — сервис стал заметно быстрее {down}'),
    Text('campaign.churned.d60', 'Ушли 60 дней',
         '{name_comma}почти два месяца без RS VPN.\n\n'
         'Новые протоколы работают даже там, где раньше были проблемы {down}'),
    Text('campaign.churned.d90', 'Ушли 90 дней',
         '{name_comma}прошло почти три месяца.\n\n'
         'RS VPN заметно изменился. Если захотите вернуться — мы здесь {down}'),

    # ── кампания: не заплатившие после триала ───────────────────────────────
    Text('campaign.trial_back.s1', 'Возврат после триала — касание 1',
         '{name_comma}вы пробовали RS VPN — и знаете, как это работает.\n\n'
         'Интернет без блокировок продолжается с любого пополнения {down}'),
    Text('campaign.trial_back.s2', 'Возврат после триала — касание 2',
         '{name_comma}ещё раз напомним: интернет без ограничений '
         'стоит меньше чашки кофе в неделю.\n\nПодключитесь прямо сейчас {down}'),
)

REGISTRY: dict[str, Text] = {t.key: t for t in TEXTS}

# Переопределения из БД (админка). Ставятся при старте и после правки.
_overrides: dict[str, str] = {}


def set_overrides(values: dict[str, str]) -> None:
    global _overrides
    _overrides = {k: v for k, v in values.items() if k in REGISTRY}


class _SafeDict(dict):
    def __missing__(self, key):  # noqa: D105 - неизвестный плейсхолдер не должен ронять отправку
        return ''


def render(key: str, *, multi: bool = False, name: str | None = None, **params) -> str:
    template = REGISTRY.get(key)
    if template is None:
        return _overrides.get(key, key)

    raw = _overrides.get(key) or (template.multi if multi and template.multi else template.default)
    context = _SafeDict({key: e(key) for key in EMOJI})
    context.update(params)
    context['name'] = name or ''
    context['name_comma'] = f'{name}, ' if name else ''
    return raw.format_map(context)


def placeholders(key: str) -> set[str]:
    """Какие плейсхолдеры использует шаблон — для подсказки в админке."""
    template = REGISTRY.get(key)
    if not template:
        return set()
    return {name for _, name, _, _ in Formatter().parse(template.default) if name}
