# RS VPN — Telegram Mini App

Главный экран личного кабинета. Собран на **TelegramUI** — библиотеке компонентов
самого Telegram (`@telegram-apps/telegram-ui`, MIT): экран выглядит как нативный
интерфейс мессенджера, темы iOS и Material и цветовая схема приходят из Telegram.

## Стек

| Что | Чем |
|---|---|
| Компоненты | [`@telegram-apps/telegram-ui`](https://github.com/Telegram-Mini-Apps/TelegramUI) — Section, Cell, Banner, Button, Placeholder, Progress, Snackbar, InlineButtons |
| Иконки | [Solar](https://icon-sets.iconify.design/solar/) через `unplugin-icons` + `@iconify-json/solar` — каждая иконка компилируется в JSX на этапе сборки, в бандл попадают только используемые, рантайма и запросов к API нет |
| Сборка | Vite 5 + React 18 |
| Telegram API | напрямую `window.Telegram.WebApp` (`src/telegram.js`), без дополнительного SDK |

Свои стили — только `src/app.css`: сброс для кликабельных строк и утилиты цветов,
всё через переменные `--tgui--*`, чтобы тема Telegram работала без правок.

```
webapp/
├── index.html            точка входа Vite
├── vite.config.js
├── src/
│   ├── main.jsx          AppRoot, платформа и тема
│   ├── App.jsx           загрузка данных, состояния, навигация
│   ├── api.js            запрос к бэкенду + демо-данные
│   ├── config.js         адреса и цифры, совпадающие с ботом
│   ├── format.js         деньги, даты, остаток, сводка по подписке
│   ├── telegram.js       обёртка над Telegram.WebApp
│   ├── app.css
│   └── components/       ProfileCell, StatusBanner, NoSubscription,
│                         QuickActions, SubscriptionSection, BalanceSection,
│                         ReferralSection, MoreSection, ClickCell
└── README.md
```

## Разработка и сборка

```bash
cd webapp
npm install
npm run dev      # http://localhost:5173
npm run build    # webapp/dist — статика для раздачи
npm run preview  # посмотреть собранное
```

Вне Telegram (`initData` пустой) экран рисует демо-данные и помечает себя
`демо-данные` в подвале. Состояния переключаются query-параметром:

| URL | Что видно |
|---|---|
| `/` | активная подписка |
| `/?state=soon` | меньше трёх дней до конца, баланса не хватает |
| `/?state=expired` | подписка истекла |
| `/?state=none` | подписки нет, список тарифов |

## Что на экране

| Блок | Источник данных в боте |
|---|---|
| Строка профиля, статус подписки | `user_data`, `vpn.expireAt` |
| Баннер состояния | `vpn.expireAt` + `info.balance`, пороги как у `process_subscriptions()` |
| Кнопка «Настроить VPN» | `https://connect.rsvps.tech/{vpn.shortUuid}` |
| Тариф и списание | `plan_base_price()`, `devices_monthly_price()` |
| Устройства | `vpn.hwidDeviceLimit` + `fetch_hwid_devices()` |
| ByPass | `vpn.bypass_*` |
| Баланс, промокод | `info.balance`, `redeem_promo_for_user()` |
| Приглашения, вывод | `info.ref_stats.*`, порог 500 ₽ |
| Подарки, почта | `info.gifts`, `info.email` |

Цифры, которые обязаны совпадать с ботом (цены тарифов, 75 ₽ за устройство,
порог вывода, доля реферала), лежат в `src/config.js` — правятся в одном месте.

## Контракт API

`GET {apiBase}{endpoint}` (по умолчанию `/api/webapp/me`)

Заголовок: `Authorization: tma <Telegram.WebApp.initData>`

```json
{
  "user": {
    "id": 152341887,
    "first_name": "Влад",
    "last_name": null,
    "username": "krasav4ik",
    "email": "Не привязана"
  },
  "balance": 340,
  "subscription": {
    "active": true,
    "period_days": 30,
    "expire_at": "2026-08-15T12:30:00",
    "short_uuid": "k3f9qzt2mx",
    "device_limit": 3,
    "devices_used": 2,
    "renew_price": 100
  },
  "bypass": {
    "enabled": true,
    "expire_at": "2026-08-15T12:30:00",
    "traffic_limit_gb": 15,
    "traffic_used_gb": 4.2
  },
  "referrals": {
    "invited": 14,
    "active": 6,
    "withdrawable": 360,
    "earned_total": 1980,
    "link": "https://t.me/rsconnect_bot?start=ref_152341887"
  },
  "gifts": { "1day": 1, "1month": 2 }
}
```

Замечания по полям:

- `expire_at` — naive ISO без таймзоны, как лежит в Mongo (`datetime.isoformat()`).
  Фронт читает его как локальное время, так же как это делает бот.
- `subscription.short_uuid` пустой или отсутствует ⇒ состояние «подписки нет».
- `renew_price` — цена продления **с учётом** `sleeping_discount()` (×0.7 для тех,
  у кого подписка истекла 7+ дней назад). Без этого поля покажем базовую цену тарифа.
- `devices_used` можно не передавать (запрос к Remnawave небыстрый) — тогда
  в строке будет только лимит.
- Ошибки: `401`/`403` ⇒ «Сессия не подтверждена», остальное ⇒ «Нет связи».

Если бэкенд на другом домене, задайте `apiBase` в `src/config.js` и включите CORS:
`Access-Control-Allow-Origin: <домен мини-аппа>`, `Access-Control-Allow-Headers: authorization`.

### Проверка initData на бэкенде

`initData` подписан токеном бота. Проверять **обязательно** — иначе любой сможет
запросить кабинет чужого `user_id`.

```python
import hashlib, hmac, json, time
from urllib.parse import parse_qsl

def parse_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict:
    pairs = dict(parse_qsl(init_data, strict_parsing=True))
    received = pairs.pop('hash', '')

    check_string = '\n'.join(f'{k}={pairs[k]}' for k in sorted(pairs))
    secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calc, received):
        raise PermissionError('bad initData signature')

    if time.time() - int(pairs.get('auth_date', 0)) > max_age:
        raise PermissionError('initData expired')

    return json.loads(pairs['user'])   # {'id': ..., 'first_name': ..., ...}
```

`user['id']` — единственный источник истины для выборки из Mongo:
`users.find_one({'user_data.user_id': user_id}, {'logs': 0})`. Никаких user_id
из query-строки или тела запроса. Токен Remnawave, ключи платёжек и токен бота
на клиент не попадают.

## Подключение к боту

Кнопку мини-аппа выдаёт `WebAppInfo` вместо текущей url-кнопки на
`console.rscore.app` (см. `on_web_button` в `start.py`):

```python
keyboard.row(types.InlineKeyboardButton(
    text='🌐 Открыть личный кабинет',
    web_app=types.WebAppInfo(url='https://console.rscore.app/'),
))
```

Reply-кнопку «RS VPN Web» тоже можно сделать мини-аппом:
`types.KeyboardButton(text='RS VPN Web', web_app=types.WebAppInfo(url=...))`.

## Деплой

`npm run build` собирает статику в `webapp/dist` — её и раздаём с
`console.rscore.app` (nginx, `try_files $uri /index.html`). Каталоги
`webapp/node_modules` и `webapp/dist` в git не попадают.

## Что пока заглушено

Строки `Продлить`, `Пополнить`, `Устройства`, `ByPass`, `Промокод`, `Заказ выплаты`,
`Смена длительности` пишут в `location.hash` и показывают тост — это точки под
следующие экраны (`ROUTES` в `src/App.jsx`).

Разделы, которые в боте делаются сообщением в чат, открывают бота: почта,
промокод, подарки (`IN_BOT` в `src/App.jsx`).

Полностью работают: подключение VPN, копирование ссылки подписки и ID, шеринг
реферальной ссылки, поддержка, канал и правовые документы.
