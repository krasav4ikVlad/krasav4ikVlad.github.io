"""`/reffix` — вернуть рефералов, потерянных из-за отсутствующей метки."""

from __future__ import annotations

import logging

from aiogram import Router, types
from aiogram.filters import Command

from app.content import ids
from app.content.emoji import e
from app.core.time import fmt
from app.domain import raffle as day
from app.domain import ref_tags as domain
from app.services import ref_fix as service

log = logging.getLogger(__name__)

USAGE = (
    f'{e("link")} <b>Починка потерянных рефералов</b>\n\n'
    f'<code>/reffix republickcheck 08.08.2026</code> — посмотреть\n'
    f'<code>/reffix republickcheck 08.08.2026 fix</code> — починить\n\n'
    f'<blockquote>Находит тех, кто пришёл по ссылке '
    f'<code>?start=ref_метка</code> в то время, когда метки в боте не было: '
    f'ссылка работала, а пригласившего бот не знал, и проценты шли мимо.\n\n'
    f'Чинит связь и доначисляет процент с их прошлых пополнений. Тех, у '
    f'кого пригласивший уже стоит, не трогает; дважды не начисляет — '
    f'отметка остаётся в карточке.\n\n'
    f'Метка должна быть заведена: <code>/reftag метка id</code>.</blockquote>'
)


def report_text(data: dict, rate: float, done: dict | None = None) -> str:
    fresh = data['fresh']
    reward = int(data['paid'] * rate)

    lines = [f'{e("link")} <b>Метка {data["tag"]}</b>',
             f'с {fmt(data["since"], "%d.%m.%Y")}, '
             f'владелец <code>{ids.show(data["owner_id"])}</code>', '']

    if not fresh:
        lines.append('Потерянных не нашлось.')
        if data['already']:
            lines.append(f'<i>У {data["already"]} чел. пригласивший уже '
                         f'стоит — их и не должно быть в списке.</i>')
        return '\n'.join(lines)

    share = round(data['payers'] * 100 / len(fresh)) if fresh else 0
    lines.append(f'{e("referrals")} Потеряно людей: <b>{len(fresh)}</b>')
    lines.append(f'{e("money")} Из них платили: <b>{data["payers"]}</b> '
                 f'({share}%) на <b>{data["paid"]}₽</b>, '
                 f'в среднем {data["average"]}₽')
    lines.append(f'{e("payout")} К доначислению: <b>{reward}₽</b> '
                 f'({round(rate * 100)}%)')
    if data['already']:
        lines.append(f'<i>Ещё у {data["already"]} чел. пригласивший уже '
                     f'стоит — их не трогаем.</i>')
    lines.append('')

    if data.get('months'):
        lines.append('<b>Потери по месяцам</b>')
        for month, count in data['months']:
            lines.append(f'{month} — {count} чел.')
        lines.append('')

    if data.get('before'):
        lines.append(f'{e("calendar")} До {fmt(data["since"], "%d.%m.%Y")} по '
                     f'этой ссылке пришло ещё <b>{data["before"]}</b> чел. — '
                     f'если потери начались раньше, возьмите дату пораньше.')
        lines.append('')

    if data.get('variants'):
        lines.append('<b>Похожие ссылки, которые не чиним</b>')
        for name, count in data['variants'][:8]:
            lines.append(f'<code>{name}</code> — {count} чел.')
        lines.append('<i>Это другие метки. Если какая-то из них тоже его — '
                     'заведите её и почините отдельно.</i>')
        lines.append('')

    if done is None:
        lines.append(f'<blockquote>Это только показ, ничего не изменено. '
                     f'Починить: допишите в команду <code>fix</code>.\n\n'
                     f'Проценты считаются от оплаченного, а не от '
                     f'зачисленного: бонус за пополнение — наш подарок, '
                     f'и платить с него процент не за что.</blockquote>')
        return '\n'.join(lines)

    lines.append(f'{e("ok")} <b>Починено</b>')
    lines.append(f'Связано: <b>{done["linked"]}</b>')
    lines.append(f'Доначислено: <b>{done["reward"]}₽</b> '
                 f'({done["payers"]} чел.)')
    lines.append('')
    lines.append(f'<blockquote>Деньги ушли на реферальный счёт — снять их '
                 f'можно заявкой на вывод, как обычные реферальные. '
                 f'В журнале каждое начисление подписано «починка '
                 f'метки».</blockquote>')
    return '\n'.join(lines)


async def command(message: types.Message, command, c, settings) -> None:
    parts = (command.args or '').split()
    if len(parts) < 2:
        await message.answer(USAGE)
        return

    tag = domain.normalize(parts[0])
    since = day.parse_day(parts[1])
    apply = len(parts) > 2 and parts[2].lower() in ('fix', 'починить')

    if not since:
        await message.answer(f'{e("cross")} Дата непонятна. Нужно '
                             f'<code>08.08.2026</code>.')
        return

    owner_id = await c.ref_tags.owner(tag)
    if not owner_id:
        await message.answer(
            f'{e("cross")} Метки <code>{tag}</code> нет. Сначала заведите её: '
            f'<code>/reftag {tag} id_владельца</code> — иначе непонятно, '
            f'кому начислять.')
        return

    await message.answer(f'{e("refresh")} Ищу по всей базе…')
    data = await service.find(c.users, c.balance_log,
                              tag=tag, since=since, owner_id=owner_id)

    rate = await settings.rate('bonus.ref_rate')
    if not apply or not data['fresh']:
        await message.answer(report_text(data, rate))
        return

    # Починка на четырёх тысячах человек идёт минуту-другую. Молчащий
    # экран в это время неотличим от зависшего — и вторая попытка «а вдруг
    # не сработало» приходит ровно сюда.
    progress = await message.answer(f'{e("refresh")} Чиню: 0 из '
                                    f'{len(data["fresh"])}…')

    async def show(done_count: int, total: int, linked: int) -> None:
        try:
            await progress.edit_text(
                f'{e("refresh")} Чиню: <b>{done_count}</b> из {total}, '
                f'связано {linked}…')
        except Exception:      # noqa: BLE001 — счётчик не важнее починки
            pass

    done = await service.repair(c.users, data, rate=rate,
                                admin_id=message.from_user.id,
                                on_progress=show)
    await message.answer(report_text(data, rate, done))


def register(router: Router) -> None:
    router.message.register(command, Command('reffix'))
