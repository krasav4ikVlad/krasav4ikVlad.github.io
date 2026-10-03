"""Почему бот не отвечает. Спрашиваем у самого Telegram.

    python -m scripts.doctor

`check_setup` проверяет наше окружение: .env, Mongo, панель. Но чаще всего
бот молчит не из-за них, а по одной из трёх причин, о которых знает только
Telegram, и каждую он называет прямым текстом, если спросить:

  * **токен отозван** — getMe отвечает 401, и никакие логи этого не
    объяснят: процесс жив, в логе тишина;
  * **стоит вебхук** — пока он задан, getUpdates не отдаёт ничего вообще.
    Бот работает, polling крутится, сообщения уходят в чужой адрес. Самый
    неочевидный случай из всех: со стороны это «бот не реагирует»;
  * **второй процесс с тем же токеном** — Telegram отвечает 409 Conflict.
    Так бывает после ручного запуска «проверить», рядом с pm2: половина
    нажатий уходит второму процессу, и бот «работает через раз».

Ничего не меняет и ничего не удаляет — только спрашивает.
"""

from __future__ import annotations

import asyncio
import sys

OK = '✅'
WARN = '⚠️ '
FAIL = '❌'

API = 'https://api.telegram.org'


def line(status: str, name: str, detail: str = '') -> None:
    print(f'{status} {name}' + (f' — {detail}' if detail else ''))


async def main() -> int:
    import httpx

    from app.core.config import Config
    from app.version import version_line

    print(f'\nСборка: {version_line()}')
    print('\n── Telegram ─────────────────────────────────────────────────')

    try:
        config = Config.from_env()
    except RuntimeError as exc:
        line(FAIL, 'Конфиг', str(exc))
        return 1

    token = config.bot_token
    problems = 0

    async with httpx.AsyncClient(timeout=15) as http:
        # 1. Токен
        try:
            reply = await http.get(f'{API}/bot{token}/getMe')
            data = reply.json()
        except Exception as exc:
            line(FAIL, 'Связь с Telegram', str(exc)[:120])
            print('     → сервер не видит api.telegram.org: сеть, DNS или блокировка')
            return 1

        if not data.get('ok'):
            line(FAIL, 'Токен', data.get('description', 'отклонён'))
            print('     → токен отозван или перевыпущен: возьмите новый у @BotFather')
            print('     → и положите в .env (API_TOKEN), затем ./scripts/update.sh')
            return 1

        me = data['result']
        line(OK, 'Токен принят', f'@{me.get("username")} ({me.get("id")})')

        # 2. Вебхук: пока он стоит, polling не получает ничего
        hook = (await http.get(f'{API}/bot{token}/getWebhookInfo')).json().get('result', {})
        url = hook.get('url') or ''
        if url:
            problems += 1
            line(FAIL, 'Стоит вебхук', url)
            print('     → пока он задан, бот не получает НИ ОДНОГО сообщения:')
            print('        обновления уходят на этот адрес, а не в polling')
            print(f'     → снять: curl "{API}/bot<ТОКЕН>/deleteWebhook"')
        else:
            line(OK, 'Вебхук не стоит', 'обновления идут в polling, как и должно')

        waiting = int(hook.get('pending_update_count') or 0)
        if waiting:
            line(WARN, 'Необработанных сообщений', f'{waiting}')
            print('     → их накопил Telegram, пока бот молчал; после подъёма '
                  'они придут разом')
        if hook.get('last_error_message'):
            line(WARN, 'Последняя ошибка со стороны Telegram',
                 str(hook['last_error_message'])[:100])

        # 3. Второй процесс с тем же токеном
        try:
            reply = await http.get(f'{API}/bot{token}/getUpdates',
                                   params={'limit': 1, 'timeout': 0})
            answer = reply.json()
        except Exception as exc:
            line(WARN, 'Проверка второго процесса', str(exc)[:80])
            answer = {}

        description = str(answer.get('description') or '')
        if 'Conflict' in description or answer.get('error_code') == 409:
            problems += 1
            line(FAIL, 'Токен занят другим процессом', description[:100])
            print('     → где-то запущен второй бот с этим же токеном:')
            print('        нажатия делятся между ними, и бот «работает через раз»')
            print('     → найти: pm2 list; ps aux | grep main_bot')
        elif answer.get('ok'):
            # Ответ пришёл нам — значит, в эту секунду polling не держал
            # никто. Если бот должен работать, это само по себе диагноз.
            line(WARN, 'В эту секунду никто не опрашивал Telegram',
                 'то есть процесс бота не работает')
            print('     → pm2 describe rsvpn-bot; tail -50 logs/bot.error.log')
            problems += 1

    print()
    if problems:
        print(f'{FAIL} Нашлось проблем: {problems}. Начните с самой верхней.')
    else:
        print(f'{OK} Со стороны Telegram всё в порядке.')
        print('   Если бот всё равно молчит — смотрите логи процесса:')
        print('   pm2 logs rsvpn-bot --lines 100 --nostream')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
