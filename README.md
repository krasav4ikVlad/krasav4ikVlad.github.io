# ⚡ Script Vault

Self-hosted хостинг скриптов для команды. Один файл `app.py`: FastAPI + MongoDB
(Motor, async), HTML встроен в код. Регистрация и вход по логину/паролю — у
каждого пользователя свои скрипты. Каждый скрипт доступен по неугадываемой
ссылке для `curl -fsSL <url> | bash`.

Дополнительно: **ИИ-помощник** — загрузите `.md` с описанием ноды, и Claude
(Anthropic) сгенерирует готовый установочный bash-скрипт прямо в редакторе.
Каждый пользователь добавляет **свой** Anthropic API-ключ в настройках
(`/settings`) — общего серверного ключа нет.

## Запуск

```bash
pip install fastapi uvicorn python-multipart motor anthropic

TOKEN_DB='mongodb+srv://user:pass@cluster/...' \
SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
python app.py
```

Приложение слушает `0.0.0.0:8000` (порт — переменная `PORT`). Откройте
`/register`, создайте аккаунт — и можно добавлять скрипты.

## Переменные окружения

| Переменная          | Назначение                                              | По умолчанию |
|---------------------|---------------------------------------------------------|--------------|
| `TOKEN_DB`          | Строка подключения к MongoDB (база `RS_2`)              | `mongodb://localhost:27017` (с предупреждением) |
| `SECRET_KEY`        | Секрет для подписи cookie-сессий                        | генерируется на старте (сессии слетают при рестарте) |
| `BASE_URL`          | Внешний адрес для отображаемых ссылок                   | берётся из заголовков запроса |
| `HOST` / `PORT`     | Адрес и порт uvicorn                                    | `0.0.0.0` / `8000` |

Хранилище — MongoDB, база `RS_2`, коллекции `users` и `scripts`. Подключение
создаётся как `AsyncIOMotorClient(TOKEN_DB)`; индексы (уникальные `username` и
`slug`) создаются при старте.

ИИ-помощник использует модель `claude-opus-4-8` через официальный SDK
`anthropic` (`AsyncAnthropic`) с потоковой генерацией — скрипт печатается в поле
по мере генерации.

## Деплой за Nginx (одной командой)

На сервере под root:

```bash
TOKEN_DB='mongodb+srv://...' bash <(curl -fsSL \
  https://raw.githubusercontent.com/krasav4ikVlad/krasav4ikVlad.github.io/refs/heads/claude/script-hosting-app-msq5fe/deploy.sh)
```

Скрипт `deploy.sh` ставит зависимости, создаёт venv и systemd-сервис на
`127.0.0.1:8000`, настраивает nginx-vhost для `scripts.nodewiki.info` и получает
TLS-сертификат Let's Encrypt. `TOKEN_DB` обязателен. Ключи Claude — у каждого
пользователя свои, добавляются в настройках после входа. Если порты 80/443 уже
заняты apache — перезапустите с `REPLACE_APACHE=1`.

После деплоя зарегистрируйте первый аккаунт на `https://scripts.nodewiki.info/register`.

Безопасность: cookie сессии HMAC-подписана, страницы управления закрыты
авторизацией, пароли хранятся как PBKDF2-HMAC-SHA256. Публичный только
`GET /raw/{slug}` (slug — `secrets.token_urlsafe(8)`, перебором не угадывается).
