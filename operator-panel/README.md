# Панель оператора техподдержки

Отдельный сервис (FastAPI + Motor + vanilla JS SPA) для операторов: поиск пользователей,
управление балансом/подпиской/устройствами/ByPass с синхронизацией в Remnawave и
обязательным аудит-логом каждого действия.

## Архитектура

```
┌─────────────┐        ┌──────────────────────────────┐        ┌──────────────┐
│  Оператор    │ HTTPS  │  operator-panel (FastAPI)     │  REST  │  Remnawave    │
│  (браузер)   ├───────►│  свой домен/порт, PM2         ├───────►│  panel API    │
└─────────────┘        │                              │        └──────┬───────┘
                       │  static/ — SPA (login, поиск, │               │
                       │  карточка, аудит)             │          Xray-ноды
                       └──────────────┬───────────────┘
                                      │ Motor (async)
                              ┌───────▼────────┐
                              │  MongoDB RS_2  │  users          — та же коллекция, что у бота
                              │                │  operators      — учётки панели
                              │                │  operator_logs  — аудит (append-only)
                              └────────────────┘
```

**Порядок применения изменений подписки/устройств/трафика:** сначала Remnawave, потом MongoDB.
Если Remnawave вернула ошибку — операция отменяется целиком (HTTP 502, Mongo не тронута),
рассинхрона нет. Оператор может явно повторить с `force_local=true` (изменение только в базе,
в аудите помечается `remnawave_synced: false`).

**Баланс** меняется одним атомарным `findOneAndUpdate` с `$inc` (плюс условие
`info.balance >= сумма` при списании без флага «разрешить минус») — гонки с ботом исключены.
Той же операцией в `info.logs_balance` добавляется запись «Оператор X: причина», так что
история в боте остаётся консистентной.

### Структура кода

```
operator-panel/
├── app/
│   ├── main.py            # приложение, lifespan, раздача SPA
│   ├── config.py          # настройки из .env
│   ├── database.py        # Motor-клиент + индексы
│   ├── security.py        # bcrypt, JWT, роли, анти-brute-force
│   ├── schemas.py         # Pydantic-модели запросов
│   ├── audit.py           # запись в operator_logs (append-only)
│   ├── remnawave.py       # клиент Remnawave API
│   ├── user_service.py    # поиск/чтение users
│   └── routers/
│       ├── auth.py        # /api/auth/*
│       ├── users.py       # поиск, карточка, истории (read-only)
│       ├── actions.py     # все изменяющие действия
│       ├── audit_log.py   # /api/audit (owner)
│       └── operators.py   # /api/operators (owner)
├── static/                # SPA: index.html, app.js, styles.css
├── scripts/create_operator.py   # bootstrap первой учётки
├── ecosystem.config.js    # PM2
├── requirements.txt
└── .env.example
```

## Схемы коллекций

### `operator_logs` (аудит, append-only)

```js
{
  _id: ObjectId,
  operator_id: "665f...",            // _id оператора (строкой)
  operator_login: "op1",
  operator_name: "Оператор 1",
  action: "balance_change",          // login | balance_change | subscription_expire_change |
                                     // device_limit_change | bypass_update | device_reset |
                                     // email_change | gift | operator_create | operator_update
  target_user_id: 802421217,         // null для login/operator_*
  old_value: { balance: 287 },       // снимок ДО
  new_value: { balance: 487 },       // снимок ПОСЛЕ
  reason: "Возврат за ошибочное продление, обращение #123",
  ip: "203.0.113.7",
  remnawave_synced: true,            // true/false/null (null = синк не требовался)
  remnawave_error: null,
  extra: { amount: 200 },            // доп. контекст действия (опционально)
  timestamp: ISODate("2026-07-04T12:00:00Z")
}
// Индексы: timestamp, (operator_id, timestamp), (target_user_id, timestamp), (action, timestamp)
```

В приложении **нет ни одного** кода-пути update/delete для этой коллекции.
Для защиты «в глубину» заведите панели отдельного пользователя MongoDB:

```js
use RS_2
db.createUser({
  user: "operator_panel",
  pwd: "<пароль>",
  roles: [
    { role: "readWrite", db: "RS_2", collection: "users" },
    { role: "readWrite", db: "RS_2", collection: "operators" },
  ],
  // отдельной ролью выдаём на operator_logs только insert+find:
})
db.createRole({
  role: "auditAppendOnly",
  privileges: [{
    resource: { db: "RS_2", collection: "operator_logs" },
    actions: ["insert", "find", "createIndex"]
  }],
  roles: []
})
db.grantRolesToUser("operator_panel", ["auditAppendOnly"])
```

Тогда даже скомпрометированная панель физически не сможет изменить или удалить аудит.

### `operators` (учётки)

```js
{
  _id: ObjectId,
  login: "op1",              // unique, lowercase
  password_hash: "$2b$12$...", // bcrypt
  name: "Оператор 1",
  role: "operator",          // "operator" | "owner"
  active: true,              // деактивация вместо удаления (аудит должен оставаться атрибутируемым)
  created_at: ISODate,
  created_by: "665f... | console",
  last_login_at: ISODate | null
}
```

Роли:
- **operator** — действия с пользователями в рамках выданных прав, без лимитов по суммам
  (каждое действие требует причину и пишется в аудит);
- **owner** — всё + просмотр аудит-лога + управление учётками и правами операторов.

Гранулярные права (`permissions`, ключи совпадают с типами действий в аудите):
`balance_change`, `subscription_expire_change`, `device_limit_change`, `bypass_update`,
`device_reset`, `email_change`, `gift`. Значение `permissions: null` (или отсутствие
поля — учётки, созданные до этой версии) = все права. Просмотр (поиск, карточка,
истории) доступен любой активной учётке. Owner игнорирует permissions — у него всё.
Права проверяются на бэкенде (403), фронтенд дополнительно скрывает недоступные кнопки.

## API

| Метод | Путь | Роль | Описание |
|---|---|---|---|
| POST | `/api/auth/login` | — | вход, выдаёт JWT (12 ч) |
| GET | `/api/auth/me` | any | текущая учётка |
| GET | `/api/users/search?q=` | any | поиск по user_id / username / email |
| GET | `/api/users/{id}` | any | карточка пользователя |
| GET | `/api/users/{id}/logs?search=&action_type=&date_from=&date_to=&page=` | any | история действий с фильтрами |
| GET | `/api/users/{id}/transactions?search=&date_from=&date_to=` | any | logs_balance + transactions |
| GET | `/api/users/{id}/bypass-purchases` | any | покупки ByPass |
| GET | `/api/users/{id}/referrals` | any | реф. статистика |
| GET | `/api/users/{id}/devices` | any | устройства из Remnawave |
| POST | `/api/users/{id}/balance` | any | `{amount, reason, allow_negative}` — атомарно |
| POST | `/api/users/{id}/subscription/expire` | any | `{days | expire_at, reason, force_local}` |
| POST | `/api/users/{id}/subscription/device-limit` | any | `{limit, reason, force_local}` |
| POST | `/api/users/{id}/bypass` | any | `{days|expire_at, traffic_limit_gb|add_traffic_gb, reason, force_local}` |
| POST | `/api/users/{id}/devices/reset` | any | `{hwid?, reason}` — одно или все |
| POST | `/api/users/{id}/email` | any | `{email, reason}` |
| POST | `/api/users/{id}/gift` | any | `{days?, bypass_gb?, reason, force_local}` |
| GET | `/api/audit?...` | owner | аудит-лог с фильтрами |
| GET | `/api/operators` | owner | список учёток |
| POST | `/api/operators` | owner | создать |
| PATCH | `/api/operators/{id}` | owner | имя/пароль/роль/активность |

Все изменяющие запросы требуют `reason` (мин. 3 символа).

## Синхронизация с Remnawave

`app/remnawave.py`, эндпоинты сверены с официальным OpenAPI-спеком
**Remnawave API v2.8.0** (https://docs.rw/api):

- `GET /api/users/{uuid}` — чтение
- `PATCH /api/users` — `expireAt`, `hwidDeviceLimit`, `trafficLimitBytes` (0 = безлимит)
- `GET /api/hwid/devices/{userUuid}` — список устройств (`{response: {total, devices}}`)
- `POST /api/hwid/devices/delete` — отвязка одного `{userUuid, hwid}`
- `POST /api/hwid/devices/delete-all` — отвязка всех `{userUuid}`

Авторизация — Bearer JWT (API-токен из панели). Маппинг полей:
`vpn.expireAt → expireAt`, `vpn.hwidDeviceLimit → hwidDeviceLimit`,
`vpn.bypass_trafficLimitBytes → trafficLimitBytes`. `bypass_expireAt` — логика бота,
хранится только в MongoDB. Если пути изменятся в будущих версиях — правится в одном файле.

## Развёртывание

```bash
cd operator-panel
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env
# заполнить: JWT_SECRET (python -c "import secrets; print(secrets.token_urlsafe(48))"),
#            MONGO_URL, REMNAWAVE_BASE_URL, REMNAWAVE_TOKEN

# первая учётка владельца:
venv/bin/python scripts/create_operator.py --login owner --name "Владелец" --role owner

pm2 start ecosystem.config.js
pm2 save
```

Nginx (панель должна жить только за HTTPS; `X-Forwarded-For` обязателен — из него берётся IP для аудита):

```nginx
server {
    listen 443 ssl;
    server_name ops.example.com;
    # ssl_certificate ...;

    location / {
        proxy_pass http://127.0.0.1:8100;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header Host $host;
    }
}
```

Рекомендуется дополнительно ограничить доступ по IP (`allow/deny` в nginx) или закрыть панель VPN-ом.

## Edge-кейсы, которые обработаны

- **Юзер не найден** — 404 с понятным текстом (поиск и все действия).
- **Гонка баланса с ботом** — единственный атомарный `$inc`; списание в минус блокируется условием
  `balance >= сумма`, если оператор явно не включил «разрешить минус».
- **Remnawave недоступна / 4xx / timeout** — операция не применяется вовсе (502 с текстом);
  оператор видит ошибку и может осознанно повторить с `force_local` — тогда факт рассинхрона
  фиксируется в аудите (`remnawave_synced: false`, текст ошибки).
- **У пользователя нет `vpn.uuid`** — изменения подписки блокируются с объяснением (или `force_local`).
- **Продление истёкшей подписки** — отсчёт от «сейчас», а не от протухшей даты; сокращение — от текущей даты подписки.
- **Смешанные форматы дат бота** (`16.09.2024 03:16:04` и ISO) — парсятся оба, фильтры по датам работают везде.
- **Owner не может отключить/понизить сам себя** — нельзя остаться без владельца.
- **Логин-brute-force** — лимит попыток на логин+IP; одинаковое сообщение об ошибке (не палим существующие логины).
- **Деактивация оператора действует мгновенно** — учётка перечитывается из базы на каждом запросе, живой JWT не поможет.
- **Ошибка записи аудита** не откатывает уже применённое действие, но громко пишется в логи процесса
  (настройте алерт на `AUDIT WRITE FAILED`).
- **Необратимые действия** (списание, сокращение срока, отвязка устройств) — двухшаговое подтверждение
  в UI с показом «было → станет».
```
