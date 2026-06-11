# ⚡ Script Vault

Self-hosted хостинг скриптов для одного разработчика. Один файл `app.py`:
FastAPI + SQLite, HTML встроен в код. Вход по паролю, каждый скрипт доступен
по неугадываемой ссылке для `curl -fsSL <url> | bash`.

## Запуск

```bash
pip install fastapi uvicorn python-multipart

ADMIN_PASSWORD='ваш-пароль' \
SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
python app.py
```

Приложение слушает `0.0.0.0:8000` (порт — переменная `PORT`).

## Переменные окружения

| Переменная       | Назначение                                              | По умолчанию |
|------------------|---------------------------------------------------------|--------------|
| `ADMIN_PASSWORD` | Пароль для входа                                        | `admin` (с предупреждением в консоль) |
| `SECRET_KEY`     | Секрет для подписи cookie-сессий                        | генерируется на старте (сессии слетают при рестарте) |
| `DB_PATH`        | Путь к файлу SQLite                                     | `scripts.db` |
| `BASE_URL`       | Внешний адрес для отображаемых ссылок                   | берётся из заголовков запроса |
| `PORT`           | Порт HTTP                                               | `8000`       |

## Деплой за Nginx

systemd-юнит (`/etc/systemd/system/script-vault.service`):

```ini
[Unit]
Description=Script Vault
After=network.target

[Service]
WorkingDirectory=/opt/script-vault
Environment=ADMIN_PASSWORD=ваш-пароль
Environment=SECRET_KEY=длинный-случайный-секрет
Environment=BASE_URL=https://scripts.example.com
Environment=PORT=8000
ExecStart=/opt/script-vault/venv/bin/python app.py
Restart=always
User=www-data

[Install]
WantedBy=multi-user.target
```

Конфиг Nginx:

```nginx
server {
    listen 443 ssl;
    server_name scripts.example.com;

    # ssl_certificate / ssl_certificate_key — например, от certbot

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
    }
}
```

Затем:

```bash
systemctl enable --now script-vault
nginx -t && systemctl reload nginx
```

Cookie сессии — HMAC-подписанная, страницы управления закрыты паролем,
публичный только `GET /raw/{slug}` (slug — `secrets.token_urlsafe(8)`,
перебором не угадывается).
