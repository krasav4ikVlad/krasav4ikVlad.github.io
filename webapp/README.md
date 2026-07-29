# RS VPN — Telegram Mini App

Главный экран личного кабинета (`webapp/index.html`). Чистая статика: HTML + CSS + JS,
без сборки и зависимостей. Данные забираются одним GET-запросом у бэкенда бота.

```
webapp/
├── index.html        — разметка экрана
├── assets/app.css    — стили (тёмная «приборная панель»)
└── assets/app.js     — Telegram SDK, загрузка данных, рендер
```

## Что на экране

| Блок | Источник данных в боте |
|---|---|
| Круговой индикатор остатка | `vpn.expireAt` + `vpn.period` |
| Дата окончания / тариф / списание | `plan_base_price()`, `devices_monthly_price()` |
| Кнопка «Настроить VPN» | `https://connect.rsvps.tech/{vpn.shortUuid}` |
| Баланс | `info.balance` |
| Устройства | `vpn.hwidDeviceLimit` + `fetch_hwid_devices()` |
| Друзья / к выводу / заработано | `info.ref_stats.*` |
| ByPass | `vpn.bypass_*` |
| Подарки | `info.gifts` |
| Плашка про почту | `info.email == 'Не привязана'` |

Сигнальный цвет всего экрана повторяет логику напоминаний бота:
мятный → **амбер за 3 дня** до конца → **красный**, когда подписка истекла.

## Конфигурация

Правится в `<head>` файла `index.html`:

```js
window.RSVPN_CONFIG = {
  apiBase: '',                  // '' = тот же origin; иначе 'https://api.rscore.app'
  endpoint: '/api/webapp/me',
  bot: 'https://t.me/rsconnect_bot',
  support: 'https://t.me/RSConnectHelp_bot',
  channel: 'https://t.me/rsconnect_vpn',
  connectBase: 'https://connect.rsvps.tech',
  terms: '…', privacy: '…'
};
```

Если `apiBase` на другом домене — на бэкенде нужен CORS:
`Access-Control-Allow-Origin: <домен мини-аппа>`, `Access-Control-Allow-Headers: authorization`.

## Контракт API

`GET {apiBase}{endpoint}`

Заголовок: `Authorization: tma <Telegram.WebApp.initData>`

Ответ `200 application/json`:

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
  Фронт трактует его как локальное время, так же как это делает бот.
- `subscription.short_uuid` пустой или отсутствует ⇒ экран показывает состояние
  «Подписка не оформлена» со списком тарифов.
- `renew_price` — итоговая цена продления **с учётом** `sleeping_discount()`
  (×0.7 для тех, у кого подписка истекла 7+ дней назад). Если поле не передать,
  фронт покажет базовую цену тарифа.
- `devices_used` можно не передавать (запрос к Remnawave небыстрый) — тогда
  в тайле будет только лимит.
- Ошибки: `401`/`403` ⇒ экран «Сессия не подтверждена», любая другая ⇒ «Нет связи».

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

Дальше `user['id']` — единственный источник истины для выборки из Mongo:
`users.find_one({'user_data.user_id': user_id}, {'logs': 0})`.
Никаких user_id из query-строки или тела запроса.

Токен Remnawave, ключи платёжек и токен бота на клиент не попадают — экран
получает только уже посчитанные значения.

## Локальный просмотр

```bash
python3 -m http.server 8080 --directory webapp
# открыть http://localhost:8080
```

Вне Telegram (`initData` пустой) страница рисует демо-данные и подписывает себя
`демо` в подвале — верстку можно смотреть без бэкенда.

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

## Что пока заглушено

Тайлы и кнопки управления (`Продлить`, `Пополнить`, `Устройства`, `ByPass`,
`Пригласить`, `Подарить`) пишут в `location.hash` и показывают тост — это
точки роста под следующие экраны. Полностью рабочие действия уже сейчас:
подключение VPN, копирование ссылки подписки и ID, шеринг реферальной ссылки,
переходы в поддержку/канал/документы, «Привязать почту» (открывает бота).
