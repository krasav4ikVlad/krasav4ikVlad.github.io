# Переезд на сервер: pm2, старый бот → новый

Порядок выбран так, чтобы на каждом шаге был откат, а деньги не списались
дважды. Единственное жёсткое правило во всём переезде — **планировщик
должен работать ровно в одном боте**. Всё остальное обратимо.

Различие с текущей установкой: процессов теперь два. Бот (polling +
планировщик) и API (вебхуки платёжек и панели) запускаются отдельно —
раньше это тоже было два процесса pm2, так что схема не меняется.

## 0. Перед началом

```bash
pm2 list                       # запомните имена старых процессов
mongodump --uri="$TOKEN_DB" --db RS_2 --out ~/backup-$(date +%F)
```

Бэкап нужен ровно один раз и стоит пять минут. Новый бот схему не ломает,
но откатывать проще с копией на руках.

## 1. Положить код рядом со старым

Не поверх: старый каталог остаётся нетронутым, это и есть план отката.

Бот лежит в подкаталоге репозитория, поэтому клонируем репозиторий целиком,
а работаем в `rsvpn-bot` внутри него:

```bash
cd /home
git clone -b claude/telegram-bot-refactor-admin-mmapc3 \
  https://github.com/krasav4ikVlad/krasav4ikVlad.github.io.git rsvpn-src
ln -s /home/rsvpn-src/rsvpn-bot /home/rsvpn-bot     # привычный короткий путь
cd /home/rsvpn-bot
```

**Не переносите подкаталог наружу** (`mv rsvpn-src/rsvpn-bot /home/rsvpn-bot`):
вместе с ним не уедет `.git`, лежащий уровнем выше, и `git pull` в таком
каталоге работать не будет — обновления станут молча не доходить.

Или распакуйте архив — важно, чтобы `pyproject.toml` лежал в корне
`/home/rsvpn-bot`. Тогда обновляться придётся тоже архивом.

## 2. Окружение

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
mkdir -p logs
```

Питон нужен 3.11 или новее (`python3 -V`).

## 3. .env

```bash
cp .env.example .env
nano .env
```

Значения переносятся из старого `config.py` один в один — таблица
соответствия в `SETUP.md`, раздел 3. Отдельно проверьте четыре строки:

```bash
ADMIN_IDS=802421217,1107871653   # раньше было зашито в loader.py
MONGO_DB=RS_2                    # та же боевая база, что у старого бота
TRIBUTE_API_KEY=...              # лежал в FastApi.py — перевыпустите
REMNAWAVE_WEBHOOK_SECRET=...     # лежал в lifeline.py — перевыпустите
LEGACY_COLLECTIONS=1             # работать на старых именах коллекций
SCHEDULER_ENABLED=0              # пока старый бот жив — только 0
```

Оба секрета лежали в исходниках и попали в git старого проекта. Пока они не
перевыпущены, ими может воспользоваться любой, у кого есть копия репозитория.

## 4. Картинки

```bash
cp /home/rs_vpn_bot/img/* /home/rsvpn-bot/media/
```

Переименовывать не нужно — старые имена (`new_profile.png` и прочие)
распознаются как есть.

## 5. Проверка до запуска

```bash
.venv/bin/python -m scripts.check_setup
```

Скрипт ничего не меняет: подключается к базе, считает пользователей,
показывает найденные картинки и говорит, какие платёжки поднялись.
Здесь же станет видно, если в `.env` опечатка.

## 6. Миграции

```bash
.venv/bin/python -m migrations.runner --only m0001
```

`m0001` создаёт индексы и заполняет коллекцию `plans` — без неё не из чего
строить экран тарифов. На работающей базе безопасна. Остальные миграции не
обязательны, подробности в `SETUP.md` §6–7.

Если `m0001` остановится на уникальном индексе — в базе дубли пользователей,
их чистит `python -m scripts.dedupe` (тот же `SETUP.md`, §5).

## 7. Остановить старое

```bash
pm2 stop <старый-бот> <старый-api>
```

Именно `stop`, не `delete`: остановленный процесс поднимается одной командой,
и это ваш откат на ближайшие полчаса.

С этой секунды бот не отвечает пользователям — дальше идут две минуты, за
которые нужно поднять новый.

## 8. Запустить новое

```bash
cd /home/rsvpn-bot
pm2 start ecosystem.config.js
pm2 logs rsvpn-bot --lines 50
```

В логе должна быть сводка режима — по ней сразу видно, то ли запустилось:

```
База:         RS_2  (старые имена коллекций)
Панель:       ИЗМЕНЯЕТ ДАННЫЕ
Планировщик:  ВКЛЮЧЁН
Админы:       802421217, 1107871653
бот запущен
```

`SCHEDULER_ENABLED` в `ecosystem.config.js` стоит `1` и перекрывает `.env`:
значения из pm2 сильнее файла. Если старый бот ещё не остановлен полностью —
поменяйте на `0` в ecosystem и перезапустите.

Проверьте руками: `/start`, `/admin`, экран тарифов, менеджер устройств.

## 9. Порт и nginx

Новый API слушает `127.0.0.1:8000`. Если старый занимал тот же порт, к этому
моменту он уже остановлен и порт свободен — nginx менять не нужно, адреса
вебхуков платёжек остались прежними (`/payment/webhook_cardlink` и остальные).

Занят порт кем-то ещё — поменяйте в `ecosystem.config.js` (`--port 8010`) и
в конфиге nginx, затем `nginx -t && systemctl reload nginx`.

Проверка снаружи:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://webhook.rsvps.tech/health
```

## 10. Вебхуки панели

В `/opt/remnawave/.env`:

```bash
WEBHOOK_ENABLED=true
WEBHOOK_URL=https://webhook.rsvps.tech/remnawave/webhook
WEBHOOK_SECRET_HEADER=<тот же, что REMNAWAVE_WEBHOOK_SECRET>
EXPIRATION_NOTIFICATIONS_ENABLED=true
EXPIRATION_NOTIFICATIONS=-72,-24,-12,-6,-3,-1,24
```

Пустой `REMNAWAVE_WEBHOOK_SECRET` в `.env` бота **отключает проверку подписи
целиком** — тогда вебхук панели может прислать кто угодно. Задайте его в
обоих местах.

## 11. Автозапуск

Когда всё работает:

```bash
pm2 save
pm2 startup          # выполните команду, которую он напечатает
```

`pm2 save` запоминает текущий список процессов. Старые остановленные тоже
попадут в него — если они больше не нужны, сначала `pm2 delete <имя>`.

## Откат

```bash
pm2 stop rsvpn-bot rsvpn-api
pm2 start <старый-бот> <старый-api>
```

Данные общие и совместимы: новые поля (`bot_settings`, `plans`, `growth.*`)
старому коду не мешают, он их просто не читает. Единственное, что не
откатывается автоматически, — настройки, которые вы успели поменять в новой
админке: старый бот их не видит и работает по своим значениям из `config.py`.

## Ежедневное

```bash
pm2 logs rsvpn-bot            # живой лог
pm2 restart rsvpn-bot         # после правки кода
pm2 restart rsvpn-bot --update-env   # после правки .env
pm2 monit                     # память и загрузка
```

Настройки из админки перезапуска не требуют — они читаются из базы с кэшем
в десять секунд.

## Обновление на сервере

Что обновление **не трогает никогда**: `.env`, `media/`, `emoji_ids.json`,
`logs/`. Все четыре вне git, и в архиве их тоже нет.

Один раз, до первого обновления, выгрузите свои id кастомных эмодзи из кода
в отдельный файл — иначе они перезапишутся вместе с `app/content/emoji.py`:

```bash
cd /home/rsvpn-bot
.venv/bin/python -m scripts.emoji_ids --export     # → emoji_ids.json
```

Дальше id живут там, а `emoji.py` можно обновлять свободно: файл читается
при старте и перекрывает то, что в коде. Проверить, что бот их видит:
`python -m scripts.emoji_ids` (без аргументов ничего не меняет).

### Сначала — убедиться, что обновление вообще дойдёт

```bash
cd /home/rsvpn-bot && git status
```

`fatal: not a git repository` — значит каталог не связан с репозиторием, и
`git pull` в нём молча ничего не делает (точнее, печатает ошибку, которую
легко пропустить между `pip install` и `pm2 restart`). Так получается, если
подкаталог `rsvpn-bot` вынесли из клона наружу: `.git` лежит уровнем выше и
наружу не уезжает. Лечится один раз:

```bash
cd /home
git clone -b claude/telegram-bot-refactor-admin-mmapc3 \
  https://github.com/krasav4ikVlad/krasav4ikVlad.github.io.git rsvpn-src

cp /home/rsvpn-bot/.env            /home/rsvpn-src/rsvpn-bot/
cp /home/rsvpn-bot/emoji_ids.json  /home/rsvpn-src/rsvpn-bot/   # если есть
cp -r /home/rsvpn-bot/media/.      /home/rsvpn-src/rsvpn-bot/media/

cd /home/rsvpn-src/rsvpn-bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

pm2 stop rsvpn-bot rsvpn-api
mv /home/rsvpn-bot /home/rsvpn-bot-old
ln -s /home/rsvpn-src/rsvpn-bot /home/rsvpn-bot
cd /home/rsvpn-bot && pm2 restart rsvpn-bot rsvpn-api --update-env
```

### Если ставили из git

```bash
cd /home/rsvpn-bot
git pull
.venv/bin/pip install -r requirements.txt      # если менялись зависимости
pm2 restart rsvpn-bot rsvpn-api
```

`git pull` не тронет `.env`, `media/` и `emoji_ids.json` — они в
`.gitignore`. Если он ругается на локальные правки в отслеживаемых файлах,
значит что-то менялось прямо в коде: посмотрите `git diff`, и либо
`git stash`, либо перенесите правку в настройки.

### Проверить, что обновление доехало

```bash
.venv/bin/python -m scripts.check_setup | head -2
pm2 logs rsvpn-bot --lines 30 | grep Сборка
```

Обе команды печатают номер сборки. Он же виден в стартовой сводке бота.
Номер прежний — обновились не туда или перезапустили не тот процесс.

### Если ставили из архива

Распаковывать поверх нельзя: удалённые файлы останутся, а `emoji.py`
перезапишется. Правильно — рядом, с переносом своего:

```bash
cd /home
unzip -q rsvpn-bot-NN.zip -d new            # получится new/rsvpn-bot

cp rsvpn-bot/.env            new/rsvpn-bot/
cp rsvpn-bot/emoji_ids.json  new/rsvpn-bot/
cp -r rsvpn-bot/media/.      new/rsvpn-bot/media/

cd new/rsvpn-bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

pm2 stop rsvpn-bot rsvpn-api
cd /home && mv rsvpn-bot rsvpn-bot-old && mv new/rsvpn-bot rsvpn-bot
cd rsvpn-bot && pm2 restart rsvpn-bot rsvpn-api --update-env
```

Откат — вернуть каталог обратно: `mv rsvpn-bot-old rsvpn-bot`.

Миграции при обновлении обычно не нужны; если в CHANGES.md сказано иначе —
`.venv/bin/python -m migrations.runner`.

## Сменили токен бота

Ничего делать не нужно: в ключе кэша картинок стоит номер бота, поэтому
новый токен просто заливает их заново — по одному разу на экран.

Так было не всегда. `file_id` выдаётся конкретному боту, и раньше после
смены токена все запомненные идентификаторы становились чужими: Telegram
отвечал «wrong file identifier», а экран уходил текстом. Если увидите такое
на старой сборке — очистите кэш и перезапустите:

```javascript
db.media_cache.deleteMany({})
```

Записи от прежнего токена можно удалить и сейчас, той же командой: они
больше никогда не прочитаются, но и места занимать незачем.
