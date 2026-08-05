# Перенос в PyCharm и настройка

## 1. Получить код

**Из архива:** распакуйте и откройте в PyCharm папку **`rsvpn-bot`** — именно её,
а не родительскую. Внутри лежит `app/`, импорты идут как `from app.core...`,
и при неверном корне PyCharm подчеркнёт полпроекта красным.

**Из git:**

```bash
git clone -b claude/telegram-bot-refactor-admin-mmapc3 \
  https://github.com/krasav4ikVlad/krasav4ikVlad.github.io.git tmp
cp -r tmp/rsvpn-bot ./rsvpn-bot && rm -rf tmp
```

## 2. Интерпретатор и зависимости

`Settings → Project → Python Interpreter → Add → Virtualenv`, Python 3.11+.
Затем в терминале PyCharm:

```bash
pip install -e ".[dev]"
```

## 3. Заполнить .env

`cp .env.example .env` и перенести значения из старого `config.py` — имена
переменных совпадают один в один:

| Старый `config.py` | `.env` |
|---|---|
| `API_TOKEN` | `API_TOKEN` |
| `TOKEN_DB` | `TOKEN_DB` |
| `REMNAWAVE_TOKEN`, `API_URL`, `API_TOKEN_CORE` | те же имена |
| `CARDLINK_ACCESS_TOKEN`, `CARDLINK_SHOP_ID` | те же |
| `WATA_ACCESS_TOKEN`, `WATA_ACCESS_TOKEN_VISA` | те же |
| `HELEKET_API_KEY`, `HELEKET_MERCHANT_ID` | те же |
| `SEVER_API_KEY`, `SEVER_WEB_API_KEY` | те же |
| `CLOUDPAYMENTS_PUBLIC_ID`, `CLOUDPAYMENTS_API_SECRET` | те же |
| `API_URL_STATS`, `API_KEY_STATS` | те же |
| `HAPP_RSA_PUBLIC_KEY` | то же |

Чего в старом конфиге не было:

* `ADMIN_IDS=802421217,1107871653` — раньше список был зашит в `loader.py`;
* `MONGO_DB=RS_2` — имя базы;
* `TRIBUTE_API_KEY` — лежал прямо в `FastApi.py`, **перевыпустите**;
* `REMNAWAVE_WEBHOOK_SECRET` — лежал прямо в `lifeline.py`, **перевыпустите**.

`GIFT_PRICES` и `GIFT_DAYS` не переносятся: подарки берут цены из тарифов,
которые правятся в админке.

## 4. Проверить настройку

```bash
make check
```

Скрипт ничего не меняет: подключается к базе, считает пользователей, показывает
коллекции с пробелом в имени, перечисляет поднявшиеся платёжные системы и
отмечает те, что принимают вебхук без подписи.

## 5. Миграции

```bash
make migrate
```

Что делает: создаёт индексы, заливает текущие тарифы (6/150/375/3000₽),
переносит четыре коллекции с пробелом в имени на нормальные имена (старые
остаются для отката) и приводит транзакции к единому формату.

## 6. Запуск

```bash
make test    # 164 теста, без сети и без базы — проверка, что окружение целое
make run     # бот
make api     # вебхуки платежей и панели, отдельный процесс
```

В PyCharm те же три конфигурации лежат в `.run/` и появляются в списке сами.

## 7. Вебхуки панели

В `/opt/remnawave/.env`:

```bash
WEBHOOK_ENABLED=true
WEBHOOK_URL=https://webhook.rsvps.tech/remnawave/webhook
WEBHOOK_SECRET_HEADER=<тот же секрет, что в REMNAWAVE_WEBHOOK_SECRET>
EXPIRATION_NOTIFICATIONS_ENABLED=true
EXPIRATION_NOTIFICATIONS=-72,-24,-12,-6,-3,-1,24
```

Адреса вебхуков платёжек менять не нужно: старые пути
(`/payment/webhook_cardlink` и остальные) работают как раньше.

## 8. Переключение боевого трафика

Оба бота работают с одной базой, поэтому переключать можно по частям.

1. Поднять новый процесс API на другом порту, перевести на него **один**
   вебхук (например Heleket) — проверить зачисление.
2. Перевести остальные платёжки.
3. Остановить старый планировщик (`process_subscriptions`, кампании) и
   запустить новый — иначе списания пойдут дважды.
4. Переключить бота: остановить старый polling, запустить новый.

Откат на любом шаге — вернуть старый процесс: схема данных совместима,
новые поля старому коду не мешают.

## Если что-то не так

* **Красные импорты `from app...`** — открыт не тот корень. Правый клик по
  папке `rsvpn-bot` → `Mark Directory as → Sources Root`.
* **`Не задана переменная окружения API_TOKEN`** — нет `.env` рядом с
  `pyproject.toml` либо PyCharm запускает из другой рабочей папки
  (в конфигурации запуска: `Working directory` = корень проекта).
* **Бот запускается, но админки нет** — в `ADMIN_IDS` не тот id.
* **Платёжки не поднялись** — `make check` покажет, для каких провайдеров
  ключи пустые; без ключа провайдер не создаётся.
