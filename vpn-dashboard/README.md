# VPN Analytics Dashboard

Внутренний аналитический дашборд для VPN-сервиса на Telegram-боте:
FastAPI + MongoDB (плоские аналитические коллекции поверх сырой `users`),
Next.js 14 + Tailwind + Recharts, realtime через WebSocket + Mongo Change
Streams, алерты в Telegram.

## Архитектура

```
users (сырая, 3 формата транзакций)
        │  ETL (APScheduler, раз в 5 мин, идемпотентно)
        ▼
transactions_flat  ──►  MongoDB aggregation pipelines ──►  REST /api/*
users_flat              (кэш Redis / in-memory TTL)
activity_stats
        │
change streams ──► WebSocket /api/ws/events ──► live-тикер
alert engine  ──► Telegram + лента
```

- **`backend/app/normalizer.py`** — единственное место, где живёт знание о
  трёх исторических форматах `info.transactions` и `logs_balance`. Любая
  транзакция приводится к Pydantic-модели
  `{amount, dt, kind, source, bonus, promo_code, ref_meta, raw}`;
  мусор не роняет разбор, а считается и логируется.
- **`backend/app/etl.py`** — разворачивает всех юзеров в `transactions_flat`
  (детерминированные `_id` → повторные прогоны идемпотентны, устаревшие
  строки удаляются) и лёгкую проекцию `users_flat` для когорт/сегментов/
  воронки. Плюс heatmap активности из `logs`.
- **`docs/CONTRACT.md`** — контракт всех эндпоинтов (бэкенд и фронтенд
  написаны строго по нему).

## Быстрый старт (Docker Compose)

```bash
cd vpn-dashboard
cp .env.example .env        # заполнить MONGO_URI, JWT_SECRET, ADMIN_PASSWORD_HASH,
                            # REMNAWAVE_*, TG_BOT_TOKEN, TG_ADMIN_CHAT_ID
docker compose up -d --build
# дашборд: http://localhost:8080  (порт меняется DASHBOARD_PORT)
```

nginx в компоузе — единая точка входа: `/` → Next.js, `/api` → FastAPI,
`/api/ws` → WebSocket с upgrade. Благодаря одному origin httpOnly-cookie
работает без CORS-настроек.

Генерация секретов:

```bash
openssl rand -hex 32                 # JWT_SECRET

# ADMIN_PASSWORD_HASH (пароль запрашивается интерактивно, не попадает в history):
docker run --rm -it python:3.11-slim sh -c \
  'pip install -q bcrypt && python -c "import bcrypt,getpass; print(bcrypt.hashpw(getpass.getpass().encode(), bcrypt.gensalt()).decode())"'
```

> **Важно:** bcrypt-хэш содержит `$` — в `.env` записывайте его строго в
> одинарных кавычках (`ADMIN_PASSWORD_HASH='$2b$12$...'`), иначе docker
> compose интерпретирует куски хэша как переменные и логин не заработает.

## Разработка без Docker

```bash
# бэкенд
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp ../.env.example .env   # COOKIE_SECURE=false для http://localhost
.venv/bin/uvicorn app.main:app --reload --port 8000

# фронтенд (проксирует /api на :8000 через next.config.mjs)
cd frontend
npm install
npm run dev               # http://localhost:3000
```

Для live-ленты в dev укажите `NEXT_PUBLIC_WS_URL=ws://localhost:8000/api/ws/events`
(cookie на `localhost` шарится между портами). Без этого лента работает в
режиме опроса раз в 20 секунд.

## Тесты

```bash
cd backend && .venv/bin/python -m pytest   # 121+ тестов: нормализатор, ETL, маскирование
```

## Деплой под PM2 (альтернатива Docker)

```bash
pm2 start ".venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log --proxy-headers" --name vpn-dash-api
cd frontend && npm run build
# standalone-сборке нужны статика и public рядом с server.js:
cp -r .next/static .next/standalone/.next/static
cp -r public .next/standalone/public 2>/dev/null || true
pm2 start "node .next/standalone/server.js" --name vpn-dash-web
# nginx: скопируйте deploy/nginx.conf, заменив upstream'ы на 127.0.0.1
```

Важно: бэкенд запускается **в один процесс** (планировщик ETL и
change-stream-вотчер не должны дублироваться).

## Замечания по данным

- Выручка везде считается «чистыми» деньгами: `amount − bonus` по
  пополнениям. Бонусы и промокоды показываются отдельно («сколько раздаём»).
- Change streams требуют replica set. На standalone-mongod дашборд
  автоматически переходит на поллинг-фолбэк (лента с задержкой ETL).
  Replica set из одного узла включается парой строк в mongod.conf +
  `rs.initiate()` — рекомендуется.
- ПДн: реквизиты выплат (карты/телефоны/email/ФИО) маскируются на уровне
  API (`app/masking.py`) и не покидают бэкенд в открытом виде.
- Никаких секретов в коде: всё через `.env` (см. `.env.example`).

## Структура

```
vpn-dashboard/
├── backend/            # FastAPI, ETL, алерты, 30+ эндпоинтов агрегаций
│   ├── app/
│   │   ├── normalizer.py   # ← нормализация 3 форматов транзакций
│   │   ├── etl.py          # ← transactions_flat / users_flat
│   │   ├── alerts.py       # Telegram-алерты (тишина провайдера, 2σ, абьюз)
│   │   ├── remnawave.py    # клиент панели (ноды/онлайн/трафик)
│   │   └── routers/        # агрегации по разделам
│   └── tests/
├── frontend/           # Next.js 14, тёмная тема, live-тикер, 9 разделов
├── deploy/nginx.conf   # единый origin: /, /api, /api/ws
├── docs/CONTRACT.md    # контракт API
└── docker-compose.yml  # nginx + frontend + backend + redis
```
