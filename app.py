#!/usr/bin/env python3
"""
Script Vault — self-hosted хостинг скриптов для команды.

Один файл, FastAPI + MongoDB (Motor, async). Многопользовательский режим:
регистрация и вход по логину/паролю, у каждого пользователя свои скрипты.
Каждый скрипт доступен по неугадываемой ссылке GET /raw/{slug} без авторизации
— удобно для `curl -fsSL <url> | bash`.

ИИ-помощник: загрузите .md с описанием ноды — Claude (Anthropic) сгенерирует
готовый установочный bash-скрипт прямо в редактор. Каждый пользователь указывает
СВОЙ Anthropic API-ключ в настройках (/settings) — общего серверного ключа нет.

Переменные окружения:
  TOKEN_DB    строка подключения к MongoDB (база RS_2)
  SECRET_KEY  секрет для подписи cookie-сессий
  BASE_URL    внешний адрес для ссылок (если пуст — из заголовков)
  HOST, PORT  адрес/порт uvicorn (по умолчанию 0.0.0.0:8000)

Запуск:  TOKEN_DB=... SECRET_KEY=... python app.py
"""

import hashlib
import hmac
import html
import os
import re
import secrets
import sys
import time
from contextlib import asynccontextmanager
from urllib.parse import quote as urlquote
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, File, Form, Path, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from bson.errors import InvalidId

try:  # ИИ-помощник опционален — без ключа приложение работает как обычно
    from anthropic import AsyncAnthropic
except ImportError:
    AsyncAnthropic = None

# ----------------------------------------------------------------------------
# Конфигурация
# ----------------------------------------------------------------------------

TOKEN_DB = os.environ.get("TOKEN_DB", "mongodb://localhost:27017")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
HUB_URL = os.environ.get("HUB_URL", "https://nodewiki.info").rstrip("/")
# SSO: домен cookie (".nodewiki.info" => сессия видна всем поддоменам);
# пусто = host-only cookie (локальная разработка)
COOKIE_DOMAIN = os.environ.get("COOKIE_DOMAIN", "")
# куда отправлять неавторизованных; по умолчанию локальный /login,
# в проде — страница входа на хабе (https://nodewiki.info/login)
LOGIN_URL = os.environ.get("LOGIN_URL", "/login")
PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "0.0.0.0")  # за nginx ставьте 127.0.0.1

AI_MODEL = "claude-opus-4-8"

COOKIE_NAME = "session"
SESSION_TTL = 60 * 60 * 24 * 30  # 30 дней

if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(32)
    print(
        "[!] SECRET_KEY не задан — сгенерирован временный ключ. "
        "Сессии слетят после перезапуска. Задайте SECRET_KEY в окружении.",
        file=sys.stderr,
    )

if not os.environ.get("TOKEN_DB"):
    print(
        "[!] TOKEN_DB не задан — используется mongodb://localhost:27017. "
        "Укажите строку подключения к вашему MongoDB в TOKEN_DB.",
        file=sys.stderr,
    )

# ----------------------------------------------------------------------------
# MongoDB (Motor, async)
# ----------------------------------------------------------------------------

cluster = AsyncIOMotorClient(TOKEN_DB)
db = cluster["RS_2"]
users_col = db["users"]
scripts_col = db["scripts"]
versions_col = db["versions"]      # история версий скриптов
access_col = db["access_log"]      # лог обращений к /raw/{slug}

VERSIONS_KEEP = 50        # хранить не больше N версий на скрипт
ACCESS_LOG_TTL_DAYS = 90  # автоудаление записей лога старше N дней (TTL-индекс)

# ----------------------------------------------------------------------------
# Шифрование секретов в БД (Fernet, ключ выводится из SECRET_KEY).
# Claude-ключи пользователей лежат в Mongo только в зашифрованном виде —
# дамп базы без SECRET_KEY их не раскрывает.
# ----------------------------------------------------------------------------

import base64

try:
    from cryptography.fernet import Fernet, InvalidToken

    _fernet = Fernet(
        base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode()).digest())
    )
except ImportError:
    _fernet = None
    InvalidToken = Exception
    print(
        "[!] Пакет cryptography не установлен — секреты в БД хранятся открытым "
        "текстом. pip install cryptography, чтобы включить шифрование.",
        file=sys.stderr,
    )

ENC_PREFIX = "enc$"


def encrypt_secret(value: str) -> str:
    if not value or _fernet is None:
        return value
    return ENC_PREFIX + _fernet.encrypt(value.encode()).decode()


def decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith(ENC_PREFIX):
        if _fernet is None:
            return ""
        try:
            return _fernet.decrypt(stored[len(ENC_PREFIX):].encode()).decode()
        except InvalidToken:  # сменился SECRET_KEY — ключ больше не прочитать
            return ""
    return stored  # legacy: значение, сохранённое до включения шифрования


# ----------------------------------------------------------------------------
# Rate-limit входа/регистрации (защита от перебора паролей)
# ----------------------------------------------------------------------------

AUTH_RATE_MAX = 10        # попыток
AUTH_RATE_WINDOW = 300    # за 5 минут
_auth_attempts: dict[str, list[float]] = {}


def auth_rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _auth_attempts.get(ip, []) if now - t < AUTH_RATE_WINDOW]
    if len(attempts) >= AUTH_RATE_MAX:
        _auth_attempts[ip] = attempts
        return True
    attempts.append(now)
    _auth_attempts[ip] = attempts
    if len(_auth_attempts) > 10_000:  # не даём словарю расти бесконечно
        _auth_attempts.clear()
    return False


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"

# ----------------------------------------------------------------------------
# Anthropic (ИИ-помощник) — каждый пользователь указывает СВОЙ API-ключ
# в настройках (/settings); общего серверного ключа нет.
# ----------------------------------------------------------------------------

if AsyncAnthropic is None:
    print(
        "[i] Пакет anthropic не установлен — ИИ-помощник выключен. "
        "pip install anthropic, чтобы включить.",
        file=sys.stderr,
    )


def user_ai_key(user: dict | None) -> str:
    return decrypt_secret((user or {}).get("anthropic_key", "") or "")


def mask_key(key: str) -> str:
    if len(key) <= 12:
        return "•" * len(key)
    return f"{key[:7]}…{key[-4:]}"

AI_SYSTEM = (
    "Ты — опытный DevOps-инженер. По предоставленной документации (Markdown) о "
    "ноде или сервисе сгенерируй полностью готовый bash-скрипт для установки и "
    "запуска ноды на чистом Linux-сервере (Ubuntu/Debian). Требования к скрипту: "
    "начинается с #!/usr/bin/env bash и set -euo pipefail; устанавливает все "
    "зависимости; идемпотентен (повторный запуск безопасен); снабжён краткими "
    "комментариями на русском. ВЫВОДИ ТОЛЬКО тело скрипта — без ограждений ```"
    " и без каких-либо пояснений до или после кода."
)


def build_ai_prompt(doc_text: str, instructions: str, interpreter: str = "bash") -> str:
    parts = []
    label = INTERPRETERS[norm_interpreter(interpreter)]["label"]
    if interpreter not in ("bash", "sh"):
        parts.append(
            f"ВАЖНО: целевой интерпретатор — {label}. Сгенерируй код именно для него "
            "(требования к bash из системного промпта адаптируй под этот язык)."
        )
    if instructions.strip():
        parts.append("Дополнительные требования:\n" + instructions.strip())
    if doc_text.strip():
        parts.append("Документация ноды (Markdown):\n\n" + doc_text.strip())
    parts.append("Сгенерируй установочный скрипт для этой ноды.")
    return "\n\n".join(parts)


# ----------------------------------------------------------------------------
# Вспомогательное
# ----------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fmt_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return str(iso)


def normalize_newlines(text: str) -> str:
    # textarea по спецификации HTML отправляет CRLF; bash спотыкается о \r
    return text.replace("\r\n", "\n").replace("\r", "\n")


# ---- глобальные переменные пользователя: {{NAME}} в теле скрипта -------------

VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
VAR_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]{0,63})\}\}")


def user_variables(user: dict | None) -> dict[str, str]:
    """Переменные пользователя с расшифрованными значениями."""
    raw_vars = (user or {}).get("variables", {}) or {}
    return {name: decrypt_secret(value) for name, value in raw_vars.items()}


def substitute_vars(content: str, variables: dict[str, str]) -> str:
    """Заменить {{NAME}} на значения; неизвестные плейсхолдеры не трогаем."""
    if not variables or "{{" not in content:
        return content
    return VAR_PLACEHOLDER_RE.sub(
        lambda m: variables.get(m.group(1), m.group(0)), content
    )


# ---- интерпретаторы: команда запуска + язык подсветки -------------------------

INTERPRETERS: dict[str, dict] = {
    "bash":   {"label": "Bash",        "pipe": "bash",    "hl": "bash"},
    "sh":     {"label": "Sh (POSIX)",  "pipe": "sh",      "hl": "bash"},
    "python": {"label": "Python 3",    "pipe": "python3", "hl": "python"},
    "node":   {"label": "Node.js",     "pipe": "node",    "hl": "javascript"},
    "ruby":   {"label": "Ruby",        "pipe": "ruby",    "hl": "ruby"},
    "perl":   {"label": "Perl",        "pipe": "perl",    "hl": "perl"},
    "yaml":   {"label": "YAML",        "pipe": "",        "hl": "yaml"},
    "json":   {"label": "JSON",        "pipe": "",        "hl": "json"},
    "ini":    {"label": "INI / conf",  "pipe": "",        "hl": "ini"},
    "plain":  {"label": "Текст",       "pipe": "",        "hl": "plaintext"},
}
DEFAULT_INTERPRETER = "bash"


def norm_interpreter(value: str) -> str:
    return value if value in INTERPRETERS else DEFAULT_INTERPRETER


def script_interp(row: dict) -> dict:
    return INTERPRETERS[norm_interpreter(row.get("interpreter", DEFAULT_INTERPRETER))]


def run_command(url: str, row: dict) -> str:
    """Команда запуска/скачивания в зависимости от интерпретатора."""
    interp = script_interp(row)
    if interp["pipe"]:
        return f"curl -fsSL {url} | {interp['pipe']}"
    fname = re.sub(r"[^A-Za-z0-9._-]", "_", row.get("name", "script")) or "script"
    return f"curl -fsSL {url} -o {fname}"


# ---- теги и папки ------------------------------------------------------------

MAX_TAGS = 20


def parse_tags(raw: str) -> list[str]:
    tags = []
    for part in raw.split(","):
        tag = part.strip().lower()[:32]
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:MAX_TAGS]


async def new_slug() -> str:
    while True:
        slug = secrets.token_urlsafe(8)
        if await scripts_col.find_one({"slug": slug}) is None:
            return slug


def external_base(request: Request) -> str:
    if BASE_URL:
        return BASE_URL
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get(
        "x-forwarded-host", request.headers.get("host", request.url.netloc)
    )
    return f"{proto}://{host}"


def raw_url(request: Request, slug: str) -> str:
    return f"{external_base(request)}/raw/{slug}"


# ----------------------------------------------------------------------------
# Пароли (PBKDF2-HMAC-SHA256, только стандартная библиотека)
# ----------------------------------------------------------------------------

PBKDF2_ROUNDS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return hmac.compare_digest(dk.hex(), dk_hex)


# ----------------------------------------------------------------------------
# Сессии: HMAC-подписанная cookie "<user_id>.<timestamp>.<signature>"
# ----------------------------------------------------------------------------


def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session_cookie(user_id: str) -> str:
    payload = f"{user_id}.{int(time.time())}"
    return f"{payload}.{_sign(payload)}"


def set_session(resp: RedirectResponse, user_id: str) -> None:
    resp.set_cookie(
        COOKIE_NAME,
        make_session_cookie(user_id),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=BASE_URL.startswith("https"),
        path="/",
        domain=COOKIE_DOMAIN or None,  # .nodewiki.info => SSO на поддоменах
    )


def login_redirect(request: Request) -> RedirectResponse:
    """Неавторизованный -> страница входа (локальная или на хабе, с возвратом)."""
    if LOGIN_URL != "/login":
        return RedirectResponse(
            f"{LOGIN_URL}?next={urlquote(str(request.url), safe='')}", status_code=303
        )
    return login_redirect(request)


def session_user_id(request: Request) -> str | None:
    cookie = request.cookies.get(COOKIE_NAME, "")
    parts = cookie.split(".")
    if len(parts) != 3:
        return None
    user_id, ts, sig = parts
    if not hmac.compare_digest(_sign(f"{user_id}.{ts}"), sig):
        return None
    try:
        if time.time() - int(ts) > SESSION_TTL:
            return None
    except ValueError:
        return None
    return user_id


async def current_user(request: Request) -> dict | None:
    uid = session_user_id(request)
    if not uid:
        return None
    try:
        oid = ObjectId(uid)
    except (InvalidId, TypeError):
        return None
    return await users_col.find_one({"_id": oid})


# ----------------------------------------------------------------------------
# HTML: общий каркас, CSS и JS встроены строками
# ----------------------------------------------------------------------------

CSS = """
:root {
  --bg: #0b0b0c;
  --panel: #121214;
  --panel-2: #171719;
  --line: #25252b;
  --line-bright: #36363f;
  --ink: #ece9e0;
  --muted: #817e75;
  --lime: #c6f23f;
  --lime-soft: #d6ff52;
  --lime-dim: #9cbf33;
  --coral: #ff5d4e;
  --mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --display: "Syne", "JetBrains Mono", sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { color-scheme: dark; -webkit-text-size-adjust: 100%; }
::selection { background: var(--lime); color: #0c0d07; }
body {
  font: 15px/1.6 var(--mono);
  background: var(--bg);
  color: var(--ink);
  min-height: 100vh;
  position: relative;
  overflow-x: hidden;
}
/* blueprint grid + glow, faded toward the bottom */
body::before {
  content: "";
  position: fixed; inset: 0; z-index: -2;
  background:
    linear-gradient(var(--line) 1px, transparent 1px) 0 0 / 100% 64px,
    linear-gradient(90deg, var(--line) 1px, transparent 1px) 0 0 / 64px 100%,
    radial-gradient(120% 75% at 82% -8%, rgba(198,242,63,.12), transparent 58%),
    radial-gradient(120% 80% at -12% 112%, rgba(255,93,78,.08), transparent 55%),
    var(--bg);
  -webkit-mask-image: radial-gradient(150% 120% at 50% 0%, #000 55%, transparent 100%);
          mask-image: radial-gradient(150% 120% at 50% 0%, #000 55%, transparent 100%);
}
/* CRT scanlines */
body::after {
  content: "";
  position: fixed; inset: 0; z-index: -1; pointer-events: none;
  background: repeating-linear-gradient(0deg, rgba(0,0,0,.16) 0 1px, transparent 1px 3px);
  opacity: .4; mix-blend-mode: multiply;
}

.container { max-width: 880px; margin: 0 auto; padding: 0 22px 80px; }

/* header */
header {
  position: sticky; top: 0; z-index: 20;
  backdrop-filter: blur(11px) saturate(1.3);
  -webkit-backdrop-filter: blur(11px) saturate(1.3);
  background: rgba(11, 11, 12, .8);
  border-bottom: 1px solid var(--line);
  margin-bottom: 42px;
}
.header-inner {
  max-width: 880px; margin: 0 auto; padding: 15px 22px;
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
}
.logo {
  font-family: var(--display);
  font-weight: 800; font-size: 19px; letter-spacing: -.6px;
  color: var(--ink); text-decoration: none;
  display: inline-flex; align-items: center;
}
.logo b { color: var(--lime); font-weight: 800; }
.logo a { color: inherit; text-decoration: none; transition: color .15s; }
.logo a.logo-hub:hover { color: var(--lime); }
.logo a.logo-app { color: var(--lime-dim); }
.logo a.logo-app:hover { color: var(--lime); }
.logo::after {
  content: "_"; color: var(--lime); margin-left: 2px;
  animation: blink 1.1s steps(1) infinite;
}
@keyframes blink { 50% { opacity: 0; } }
.header-actions { display: flex; gap: 10px; align-items: center; }
.header-actions form { display: inline; }
.user-chip {
  font-family: var(--mono); font-size: 12px; color: var(--lime-dim);
  border: 1px solid var(--line-bright); border-radius: 2px; padding: 6px 11px;
  text-decoration: none; cursor: pointer;
  transition: border-color .15s, color .15s;
}
.user-chip:hover { border-color: var(--lime); color: var(--lime); }
.user-chip::before { content: "@"; color: var(--muted); }
.user-chip::after { content: " ⚙"; color: var(--muted); font-size: 11px; }

/* headings */
h1 {
  font-family: var(--display);
  font-size: 31px; font-weight: 800; letter-spacing: -1.2px; line-height: 1.04;
}
.kicker {
  display: block; font-size: 11px; letter-spacing: 3px; text-transform: uppercase;
  color: var(--lime-dim); margin-bottom: 9px;
}
.kicker::before { content: "> "; }
.page-head {
  display: flex; align-items: flex-end; justify-content: space-between;
  gap: 14px; flex-wrap: wrap; margin-bottom: 30px;
  animation: rise .55s cubic-bezier(.2, .7, .2, 1) both;
}
.muted { color: var(--muted); font-size: 12.5px; letter-spacing: .3px; }

/* buttons — hard-edged, brutalist */
.btn {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 10px 16px; border-radius: 2px;
  border: 1px solid var(--line-bright);
  background: var(--panel-2);
  color: var(--ink);
  font-family: var(--mono); font-size: 13px; font-weight: 600;
  letter-spacing: .3px; text-transform: lowercase;
  text-decoration: none; cursor: pointer; white-space: nowrap;
  transition: transform .12s ease, box-shadow .12s ease, background .15s, border-color .15s, color .15s;
}
.btn:hover { border-color: var(--lime); color: var(--lime); transform: translate(-2px, -2px); box-shadow: 4px 4px 0 #000; }
.btn:active { transform: translate(0, 0); box-shadow: 0 0 0 #000; }
.btn:disabled { opacity: .5; cursor: progress; transform: none; box-shadow: none; }
.btn-primary {
  background: var(--lime); color: #11130a; border-color: var(--lime);
  font-weight: 700; box-shadow: 4px 4px 0 #000;
}
.btn-primary:hover { background: var(--lime-soft); color: #11130a; border-color: var(--lime-soft); box-shadow: 6px 6px 0 #000; }
.btn-danger:hover { border-color: var(--coral); color: var(--coral); box-shadow: 4px 4px 0 #000; }
.btn-sm { padding: 7px 12px; font-size: 12px; }

/* script cards */
.card {
  position: relative;
  background: var(--panel);
  border: 1px solid var(--line);
  border-left: 2px solid var(--line-bright);
  border-radius: 3px;
  padding: 20px 22px;
  margin-bottom: 16px;
  animation: rise .55s cubic-bezier(.2, .7, .2, 1) both;
  transition: transform .16s ease, border-color .16s ease, box-shadow .16s ease;
}
.card:hover {
  transform: translate(-3px, -3px);
  border-color: var(--line-bright);
  border-left-color: var(--lime);
  box-shadow: 7px 7px 0 #000;
}
.card-top {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 12px; flex-wrap: wrap;
}
.card-name {
  font-family: var(--display); font-size: 18px; font-weight: 700;
  letter-spacing: -.3px; word-break: break-word;
}
.card-url {
  font-family: var(--mono); font-size: 12.5px; color: var(--lime);
  text-decoration: none; word-break: break-all;
  display: inline-block; margin: 11px 0 15px;
  border-bottom: 1px dashed rgba(198, 242, 63, .35); padding-bottom: 1px;
}
.card-url::before { content: "\\21B3  "; color: var(--muted); }
.card-url:hover { color: var(--lime-soft); border-bottom-color: var(--lime-soft); }
.card-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.card-actions form { display: inline; }

/* forms */
form.editor { animation: rise .55s cubic-bezier(.2, .7, .2, 1) both; }
label {
  display: block; font-size: 11px; font-weight: 600; letter-spacing: 2px;
  text-transform: uppercase; color: var(--muted); margin: 22px 0 8px;
}
label::before { content: "// "; color: var(--lime-dim); }
input[type=text], input[type=password], textarea {
  width: 100%;
  padding: 12px 14px;
  border-radius: 2px;
  border: 1px solid var(--line-bright);
  background: #0d0d0f;
  color: var(--ink);
  font-family: var(--mono); font-size: 14px;
  transition: border-color .15s ease, box-shadow .15s ease;
}
textarea {
  font-size: 13px; line-height: 1.65;
  min-height: 400px; resize: vertical;
  white-space: pre; overflow-wrap: normal; overflow-x: auto;
  tab-size: 4;
}
input::placeholder, textarea::placeholder { color: #494842; }
input:focus, textarea:focus {
  outline: none;
  border-color: var(--lime);
  box-shadow: 0 0 0 1px var(--lime), 0 0 24px rgba(198, 242, 63, .13);
}
.form-actions { display: flex; gap: 12px; margin-top: 26px; flex-wrap: wrap; }

/* ИИ-помощник */
.ai-box {
  border: 1px solid var(--line-bright); border-left: 2px solid var(--lime);
  border-radius: 3px; padding: 16px 18px; margin: 24px 0 4px;
  background: rgba(198, 242, 63, .03);
  animation: rise .55s cubic-bezier(.2, .7, .2, 1) both;
}
.ai-box p { margin: 6px 0 14px; }
.ai-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.ai-row input[type=text] { flex: 1; min-width: 220px; }
.ai-file {
  font-family: var(--mono); font-size: 12px; color: var(--muted);
  background: #0d0d0f; border: 1px solid var(--line-bright); border-radius: 2px;
  padding: 9px 10px; max-width: 260px;
}
.ai-file::file-selector-button {
  font-family: var(--mono); font-size: 12px; margin-right: 10px;
  background: var(--panel-2); color: var(--ink); border: 1px solid var(--line-bright);
  border-radius: 2px; padding: 5px 11px; cursor: pointer;
}
.ai-file::file-selector-button:hover { border-color: var(--lime); color: var(--lime); }

/* share / curl block on the edit page */
.share {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 3px;
  padding: 18px 20px;
  margin-bottom: 28px;
  animation: rise .55s cubic-bezier(.2, .7, .2, 1) both;
}
.share .kicker { margin: 0 0 6px; }
.share .kicker + .kicker { margin-top: 16px; }
.share code {
  display: block;
  font-family: var(--mono); font-size: 12.5px; color: var(--lime);
  background: #0d0d0f;
  border: 1px solid var(--line-bright);
  border-radius: 2px;
  padding: 12px 14px;
  margin: 8px 0 16px;
  word-break: break-all;
}
.share .btn { margin-right: 8px; }

/* метаданные скрипта: скачивания, лог, версии */
.meta-row {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  margin-bottom: 26px;
  animation: rise .55s cubic-bezier(.2, .7, .2, 1) both;
}
.meta-stat {
  font-family: var(--mono); font-size: 12.5px; color: var(--muted);
  border: 1px dashed var(--line-bright); border-radius: 2px; padding: 8px 12px;
}
.meta-stat b { color: var(--lime); font-weight: 700; }

/* лог обращений */
.log-table {
  border: 1px solid var(--line); border-radius: 3px;
  background: var(--panel);
  animation: rise .55s cubic-bezier(.2, .7, .2, 1) both;
  overflow: hidden;
}
.log-row {
  display: flex; gap: 16px; align-items: baseline;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
  font-family: var(--mono); font-size: 12.5px;
}
.log-row:last-child { border-bottom: none; }
.log-row:hover { background: var(--panel-2); }
.log-ts { color: var(--lime-dim); white-space: nowrap; }
.log-ip { color: var(--ink); white-space: nowrap; min-width: 110px; }
.log-ua { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 560px) {
  .log-row { flex-wrap: wrap; gap: 4px 12px; }
  .log-ua { flex-basis: 100%; }
}

textarea[readonly] { opacity: .8; min-height: 320px; }

/* тулбар: поиск + фильтры */
.toolbar { margin-bottom: 26px; animation: rise .55s cubic-bezier(.2, .7, .2, 1) both; }
.search-form { display: flex; gap: 10px; margin-bottom: 14px; }
.search-form input[type=search] { flex: 1; }
input[type=search] {
  padding: 10px 14px; border-radius: 2px;
  border: 1px solid var(--line-bright);
  background: #0d0d0f; color: var(--ink);
  font-family: var(--mono); font-size: 13px;
  transition: border-color .15s ease, box-shadow .15s ease;
}
input[type=search]:focus {
  outline: none; border-color: var(--lime);
  box-shadow: 0 0 0 1px var(--lime), 0 0 24px rgba(198, 242, 63, .13);
}

/* чипы папок и тегов */
.chips-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.chip {
  font-family: var(--mono); font-size: 11.5px; letter-spacing: .4px;
  color: var(--muted); text-decoration: none;
  border: 1px solid var(--line-bright); border-radius: 2px;
  padding: 5px 10px; white-space: nowrap;
  transition: border-color .13s, color .13s, transform .13s, box-shadow .13s;
}
.chip:hover { border-color: var(--lime); color: var(--lime); transform: translate(-1px, -1px); box-shadow: 2px 2px 0 #000; }
.chip-on { background: var(--lime); color: #11130a !important; border-color: var(--lime); font-weight: 700; }
.chip-tag { border-style: dashed; }
.card-chips { margin: 10px 0 0; }
.card-chips .chip { font-size: 11px; padding: 3px 8px; }

/* двухколоночные поля редактора и форма переменных */
.field-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; }
.vars-hint { margin-top: 10px; }
.vars-hint code {
  font-family: var(--mono); font-size: 12px; color: var(--lime);
  background: #0d0d0f; border: 1px solid var(--line-bright);
  border-radius: 2px; padding: 1px 6px;
}

/* таблица переменных в настройках */
.var-table { border: 1px solid var(--line); border-radius: 3px; overflow: hidden; }
.var-row {
  display: flex; align-items: center; gap: 14px;
  padding: 10px 14px; border-bottom: 1px solid var(--line);
  background: #0d0d0f;
}
.var-row:last-child { border-bottom: none; }
.share code.var-name {
  display: inline-block; margin: 0; padding: 2px 8px;
  color: var(--lime); font-size: 13px; white-space: nowrap;
  border: none; background: transparent;
}
.var-value { color: var(--muted); font-family: var(--mono); font-size: 12.5px; flex: 1; overflow: hidden; text-overflow: ellipsis; }
.var-row form { display: inline; }

/* select интерпретатора */
select {
  width: 100%;
  padding: 12px 14px;
  border-radius: 2px;
  border: 1px solid var(--line-bright);
  background: #0d0d0f;
  color: var(--ink);
  font-family: var(--mono); font-size: 14px;
  cursor: pointer;
  appearance: none; -webkit-appearance: none;
  background-image: linear-gradient(45deg, transparent 50%, var(--lime-dim) 50%),
                    linear-gradient(135deg, var(--lime-dim) 50%, transparent 50%);
  background-position: calc(100% - 18px) 50%, calc(100% - 13px) 50%;
  background-size: 5px 5px;
  background-repeat: no-repeat;
  transition: border-color .15s ease, box-shadow .15s ease;
}
select:focus {
  outline: none; border-color: var(--lime);
  box-shadow: 0 0 0 1px var(--lime), 0 0 24px rgba(198, 242, 63, .13);
}
.chip-lang {
  border-style: solid; border-color: rgba(198, 242, 63, .4);
  color: var(--lime-dim); cursor: default;
}
.chip-lang:hover { transform: none; box-shadow: none; color: var(--lime-dim); border-color: rgba(198,242,63,.4); }

/* ====== редактор кода: подсветка под прозрачным textarea ====== */
.code-editor { position: relative; }
.code-editor .hl-pre {
  position: absolute; inset: 0; z-index: 0;
  margin: 0; padding: 12px 14px;
  border: 1px solid transparent; /* те же метрики, что у textarea */
  overflow: auto;
  background: #0d0d0f;
  border-radius: 2px;
  pointer-events: none;
}
.code-editor .hl-pre code {
  font-family: var(--mono); font-size: 13px; line-height: 1.65;
  white-space: pre; tab-size: 4;
  background: transparent; padding: 0; border: none;
  display: block; min-width: max-content;
}
.code-editor textarea {
  position: relative; z-index: 1;
  background: transparent;
  caret-color: var(--lime);
}
.code-editor.hl-on textarea {
  color: transparent;             /* текст рисует подсветка под ним */
  -webkit-text-fill-color: transparent;
}
.code-editor.hl-on textarea::selection {
  background: rgba(198, 242, 63, .28);
  color: transparent; -webkit-text-fill-color: transparent;
}
.code-editor textarea::placeholder { color: #494842; -webkit-text-fill-color: #494842; }

/* просмотр версии с подсветкой */
.code-view {
  margin: 0; padding: 12px 14px;
  background: #0d0d0f;
  border: 1px solid var(--line-bright); border-radius: 2px;
  overflow: auto; max-height: 70vh;
  animation: rise .55s cubic-bezier(.2, .7, .2, 1) both;
}
.code-view code {
  font-family: var(--mono); font-size: 13px; line-height: 1.65;
  white-space: pre; tab-size: 4;
  background: transparent; display: block;
}

/* ====== тема подсветки: фосфорный терминал ====== */
.hljs { color: var(--ink); background: transparent; }
.hljs-comment, .hljs-quote { color: #6b6960; font-style: italic; }
.hljs-keyword, .hljs-selector-tag, .hljs-literal, .hljs-name { color: var(--lime); }
.hljs-built_in, .hljs-type, .hljs-symbol { color: #38e1c2; }
.hljs-string, .hljs-regexp, .hljs-addition { color: #ffd28a; }
.hljs-number, .hljs-link { color: #7ee8fa; }
.hljs-title, .hljs-section, .hljs-attribute, .hljs-attr { color: #9aa8ff; }
.hljs-variable, .hljs-template-variable, .hljs-subst, .hljs-params { color: #ff9d8f; }
.hljs-meta, .hljs-doctag { color: #817e75; }
.hljs-meta .hljs-keyword, .hljs-meta-keyword { color: #9cbf33; }
.hljs-deletion { color: var(--coral); }
.hljs-emphasis { font-style: italic; }
.hljs-strong { font-weight: 700; }
.hljs-bullet, .hljs-selector-id, .hljs-selector-class { color: #38e1c2; }
.hljs-formula, .hljs-code { color: #ffd28a; }

@media (max-width: 560px) {
  .field-row { grid-template-columns: 1fr; }
  .search-form { flex-wrap: wrap; }
  .var-row { flex-wrap: wrap; }
}

/* empty state */
.empty {
  text-align: center; padding: 80px 24px;
  border: 1px dashed var(--line-bright); border-radius: 4px;
  background: repeating-linear-gradient(135deg, transparent 0 14px, rgba(255, 255, 255, .012) 14px 28px);
  animation: rise .55s cubic-bezier(.2, .7, .2, 1) both;
}
.empty .glyph {
  font-family: var(--display); font-size: 38px; font-weight: 800;
  color: var(--lime); letter-spacing: -1px;
}
.empty .glyph::after { content: "_"; animation: blink 1.1s steps(1) infinite; }
.empty p { color: var(--muted); margin: 16px auto 24px; max-width: 440px; }

/* login / register — terminal window */
.login-wrap { min-height: 82vh; display: flex; align-items: center; justify-content: center; }
.login-card {
  width: 100%; max-width: 400px;
  background: var(--panel);
  border: 1px solid var(--line-bright);
  border-radius: 4px;
  overflow: hidden;
  animation: rise .6s cubic-bezier(.2, .7, .2, 1) both;
  box-shadow: 10px 10px 0 #000;
}
.login-bar {
  display: flex; align-items: center; gap: 7px;
  padding: 12px 15px;
  border-bottom: 1px solid var(--line);
  background: var(--panel-2);
  font-size: 11px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted);
}
.login-bar i { width: 9px; height: 9px; border-radius: 50%; background: var(--line-bright); }
.login-bar i:nth-child(1) { background: var(--coral); }
.login-bar i:nth-child(3) { background: var(--lime); }
.login-bar span { margin-left: auto; }
.login-body { padding: 32px 30px 36px; }
.login-body h1 { font-size: 25px; }
.login-body .muted { display: block; margin-top: 6px; }
.login-foot { margin-top: 20px; font-size: 12.5px; color: var(--muted); }
.login-foot a { color: var(--lime); text-decoration: none; border-bottom: 1px dashed rgba(198,242,63,.4); }
.login-foot a:hover { color: var(--lime-soft); }

.error {
  background: rgba(255, 93, 78, .1);
  border: 1px solid rgba(255, 93, 78, .45);
  border-left: 2px solid var(--coral);
  color: var(--coral);
  border-radius: 2px;
  padding: 11px 14px;
  font-size: 13px;
  margin-top: 18px;
  animation: shake .4s ease;
}
.error::before { content: "\\2717  "; }
@keyframes shake {
  0%, 100% { transform: translateX(0); }
  20% { transform: translateX(-7px); } 40% { transform: translateX(6px); }
  60% { transform: translateX(-4px); } 80% { transform: translateX(3px); }
}

/* toast */
#toast {
  position: fixed; left: 50%; bottom: 30px; z-index: 100;
  transform: translate(-50%, 90px);
  background: #0d0d0f;
  border: 1px solid var(--lime);
  border-left: 3px solid var(--lime);
  color: var(--ink);
  padding: 12px 20px;
  border-radius: 2px;
  font-family: var(--mono); font-size: 13px; font-weight: 600;
  box-shadow: 6px 6px 0 #000;
  opacity: 0;
  pointer-events: none;
  transition: transform .3s cubic-bezier(.2, .9, .3, 1.3), opacity .25s ease;
}
#toast::before { content: "\\2713  "; color: var(--lime); }
#toast.show { transform: translate(-50%, 0); opacity: 1; }

@keyframes rise {
  from { opacity: 0; transform: translateY(16px); }
  to   { opacity: 1; transform: translateY(0); }
}

@media (max-width: 560px) {
  body { font-size: 14px; }
  .container { padding: 0 15px 56px; }
  h1 { font-size: 25px; }
  header { margin-bottom: 30px; }
  .card { padding: 16px 17px; }
  .card-actions .btn { flex: 1; justify-content: center; }
  .form-actions .btn { flex: 1; justify-content: center; }
  .ai-row input[type=text], .ai-file, .ai-row .btn { flex: 1 1 100%; max-width: none; }
  textarea { min-height: 320px; }
  .user-chip { display: none; }
}
"""

JS = """
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2400);
}
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text)
      .then(() => toast('Скопировано'))
      .catch(() => toast('Не удалось скопировать'));
  } else {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); toast('Скопировано'); }
    catch (e) { toast('Не удалось скопировать'); }
    document.body.removeChild(ta);
  }
}
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-copy]');
  if (btn) copyText(btn.dataset.copy);
});

async function aiGenerate(btn) {
  const file = document.getElementById('ai-file');
  const instr = document.getElementById('ai-instructions');
  const target = document.getElementById('content');
  if ((!file.files || !file.files[0]) && !instr.value.trim()) {
    toast('Прикрепите .md или опишите задачу');
    return;
  }
  const fd = new FormData();
  if (file.files && file.files[0]) fd.append('md', file.files[0]);
  fd.append('instructions', instr.value || '');
  const interp = document.getElementById('interpreter');
  if (interp) fd.append('interpreter', interp.value);
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Генерирую…';
  target.value = '';
  try {
    const resp = await fetch('/ai/generate', { method: 'POST', body: fd });
    if (!resp.ok) {
      const msg = await resp.text();
      toast(msg || 'Ошибка генерации');
    } else {
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        target.value += dec.decode(value, { stream: true });
        target.scrollTop = target.scrollHeight;
      }
      toast('Скрипт сгенерирован');
    }
  } catch (e) {
    toast('Ошибка сети');
  }
  target.dispatchEvent(new Event('input'));  // перерисовать подсветку
  btn.disabled = false;
  btn.textContent = label;
}

/* живой редактор с подсветкой: pre/code под прозрачным textarea */
function initCodeEditor() {
  const ta = document.getElementById('content');
  const codeEl = document.getElementById('hl-code');
  if (!ta || !codeEl || typeof hljs === 'undefined') return;
  const wrap = ta.closest('.code-editor');
  const pre = codeEl.parentElement;
  const sel = document.getElementById('interpreter');
  const lang = () =>
    (sel && sel.selectedOptions[0] && sel.selectedOptions[0].dataset.hl) ||
    codeEl.dataset.hl || 'bash';
  function render() {
    let out;
    try { out = hljs.highlight(ta.value, { language: lang(), ignoreIllegals: true }).value; }
    catch (e) { out = ta.value.replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
    codeEl.innerHTML = out + '\\n';
    sync();
  }
  function sync() { pre.scrollTop = ta.scrollTop; pre.scrollLeft = ta.scrollLeft; }
  ta.addEventListener('input', render);
  ta.addEventListener('scroll', sync);
  if (sel) sel.addEventListener('change', render);
  ta.addEventListener('keydown', (e) => {   // Tab вставляет отступ, не уводит фокус
    if (e.key === 'Tab' && !e.shiftKey) {
      e.preventDefault();
      const s = ta.selectionStart, en = ta.selectionEnd;
      ta.value = ta.value.slice(0, s) + '    ' + ta.value.slice(en);
      ta.selectionStart = ta.selectionEnd = s + 4;
      render();
    }
  });
  new ResizeObserver(sync).observe(ta);     // ручной resize textarea
  wrap.classList.add('hl-on');              // текст textarea станет прозрачным
  render();
}
document.addEventListener('DOMContentLoaded', () => {
  initCodeEditor();
  if (typeof hljs !== 'undefined')
    document.querySelectorAll('.code-view code').forEach((el) => hljs.highlightElement(el));
});
"""


def ai_panel(user: dict) -> str:
    if AsyncAnthropic is None:
        return ""
    if not user_ai_key(user):
        return """
<div class="ai-box">
  <span class="kicker">ии-помощник</span>
  <p class="muted">Claude может сгенерировать установочный скрипт по вашему .md — для этого
  добавьте свой Anthropic API-ключ в <a href="/settings" style="color:var(--lime)">настройках</a>.</p>
</div>"""
    return """
<div class="ai-box">
  <span class="kicker">ии-помощник</span>
  <p class="muted">Загрузите .md с описанием ноды — Claude сгенерирует установочный bash-скрипт прямо в поле «Содержимое» ниже.</p>
  <div class="ai-row">
    <input class="ai-file" type="file" id="ai-file" accept=".md,.markdown,.txt">
    <input type="text" id="ai-instructions" placeholder="доп. указания (необязательно): порт, версия, окружение…">
    <button class="btn btn-primary" type="button" onclick="aiGenerate(this)">Сгенерировать</button>
  </div>
</div>"""


HLJS_URL = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"
HLJS_SRI = "sha384-F/bZzf7p3Joyp5psL90p/p89AZJsndkSoGwRpXcZhleCWhd8SnRuoYo4d0yirjJp"


def page(
    title: str, body: str, *, user: str | None = None, code: bool = False
) -> HTMLResponse:
    header = ""
    if user:
        header = f"""
<header>
  <div class="header-inner">
    <span class="logo"><a class="logo-hub" href="{HUB_URL}" title="На главную nodewiki">nodewiki</a><b>/</b><a class="logo-app" href="/">scripts</a></span>
    <div class="header-actions">
      <a class="user-chip" href="/settings" title="Настройки">{html.escape(user)}</a>
      <a class="btn btn-sm btn-primary" href="/new">+ Новый скрипт</a>
      <form method="post" action="/logout">
        <button class="btn btn-sm" type="submit">Выйти</button>
      </form>
    </div>
  </div>
</header>"""
    doc = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex">
  <meta name="theme-color" content="#0b0b0c">
  <title>{html.escape(title)} — Script Vault</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Syne:wght@700;800&display=swap">
  <style>{CSS}</style>
</head>
<body>
{header}
<div class="container">
{body}
</div>
<div id="toast"></div>
{f'<script src="{HLJS_URL}" integrity="{HLJS_SRI}" crossorigin="anonymous"></script>' if code else ""}
<script>{JS}</script>
</body>
</html>"""
    return HTMLResponse(doc)


# ----------------------------------------------------------------------------
# Приложение и роуты
# ----------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await users_col.create_index("username", unique=True)
        await scripts_col.create_index("slug", unique=True)
        await scripts_col.create_index("owner")
        await versions_col.create_index([("script_id", 1), ("saved_at", -1)])
        await access_col.create_index([("script_id", 1), ("ts", -1)])
        # TTL: Mongo сам удаляет записи лога старше ACCESS_LOG_TTL_DAYS
        await access_col.create_index(
            "ts_dt", expireAfterSeconds=ACCESS_LOG_TTL_DAYS * 24 * 3600
        )
    except Exception as e:  # не валим старт, если БД временно недоступна
        print(f"[!] Не удалось создать индексы MongoDB: {e}", file=sys.stderr)
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if not request.url.path.startswith("/raw/"):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
            "img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'"
        )
    return resp


# ---- регистрация / вход ----------------------------------------------------


def auth_card(mode: str, error: str = "") -> str:
    err_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    if mode == "register":
        title, action, btn = "Регистрация", "/register", "Создать аккаунт →"
        foot = 'Уже есть аккаунт? <a href="/login">Войти</a>'
        sub = "Создайте аккаунт для своих скриптов"
        bar = "register · script/vault"
    else:
        title, action, btn = "Вход", "/login", "Войти →"
        foot = 'Нет аккаунта? <a href="/register">Зарегистрироваться</a>'
        sub = "Введите логин и пароль"
        bar = "auth · script/vault"
    return f"""
<div class="login-wrap">
  <div class="login-card">
    <div class="login-bar"><i></i><i></i><i></i><span>{bar}</span></div>
    <div class="login-body">
      <h1>script<span style="color:var(--lime)">/</span>vault</h1>
      <span class="muted">{sub}</span>
      {err_html}
      <form method="post" action="{action}">
        <label for="username">Логин</label>
        <input type="text" id="username" name="username" required autofocus
               autocomplete="username" maxlength="32">
        <label for="password">Пароль</label>
        <input type="password" id="password" name="password" required
               autocomplete="{'new-password' if mode == 'register' else 'current-password'}">
        <div class="form-actions">
          <button class="btn btn-primary" type="submit">{btn}</button>
        </div>
      </form>
      <div class="login-foot">{foot}</div>
    </div>
  </div>
</div>"""


@app.get("/login")
async def login_form(request: Request, error: int = 0):
    if await current_user(request):
        return RedirectResponse("/", status_code=303)
    if LOGIN_URL != "/login" and not error:  # вход живёт на хабе
        return login_redirect(request)
    msg = "Неверный логин или пароль" if error else ""
    return page("Вход", auth_card("login", msg))


@app.post("/login")
async def login(
    request: Request, username: str = Form(...), password: str = Form(...)
):
    if auth_rate_limited(client_ip(request)):
        return page(
            "Вход", auth_card("login", "Слишком много попыток — подождите 5 минут")
        )
    user = await users_col.find_one({"username": username.strip().lower()})
    if user is None or not verify_password(password, user.get("password", "")):
        return RedirectResponse("/login?error=1", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    set_session(resp, str(user["_id"]))
    return resp


@app.get("/register")
async def register_form(request: Request):
    if await current_user(request):
        return RedirectResponse("/", status_code=303)
    if LOGIN_URL != "/login":  # регистрация тоже на хабе
        return RedirectResponse(
            LOGIN_URL.rsplit("/", 1)[0] + "/register", status_code=303
        )
    return page("Регистрация", auth_card("register"))


@app.post("/register")
async def register(
    request: Request, username: str = Form(...), password: str = Form(...)
):
    if auth_rate_limited(client_ip(request)):
        return page(
            "Регистрация",
            auth_card("register", "Слишком много попыток — подождите 5 минут"),
        )
    uname = username.strip().lower()
    if not (3 <= len(uname) <= 32) or not all(c.isalnum() or c in "_-." for c in uname):
        return page(
            "Регистрация",
            auth_card("register", "Логин: 3–32 символа, буквы/цифры/._-"),
        )
    if len(password) < 6:
        return page("Регистрация", auth_card("register", "Пароль — минимум 6 символов"))
    if await users_col.find_one({"username": uname}):
        return page("Регистрация", auth_card("register", "Такой логин уже занят"))
    result = await users_col.insert_one(
        {"username": uname, "password": hash_password(password), "created_at": now_iso()}
    )
    resp = RedirectResponse("/", status_code=303)
    set_session(resp, str(result.inserted_id))
    return resp


@app.post("/logout")
async def logout():
    resp = RedirectResponse(LOGIN_URL, status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/", domain=COOKIE_DOMAIN or None)
    return resp


# ---- список / редактор -----------------------------------------------------


@app.get("/")
async def index(request: Request, q: str = "", folder: str = "", tag: str = ""):
    user = await current_user(request)
    if not user:
        return login_redirect(request)
    uname = user["username"]
    owner = str(user["_id"])
    q, folder, tag = q.strip()[:100], folder.strip()[:64], tag.strip()[:32]

    query: dict = {"owner": owner}
    if folder:
        query["folder"] = folder
    if tag:
        query["tags"] = tag
    if q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        query["$or"] = [{"name": rx}, {"content": rx}, {"tags": rx}]

    rows = await scripts_col.find(query).sort("updated_at", -1).to_list(length=1000)
    total = await scripts_col.count_documents({"owner": owner})

    # папки и теги пользователя — для панели фильтров
    folders = sorted(
        f for f in await scripts_col.distinct("folder", {"owner": owner}) if f
    )
    all_tags = sorted(
        t for t in await scripts_col.distinct("tags", {"owner": owner}) if t
    )

    def filt_url(**overrides) -> str:
        params = {"q": q, "folder": folder, "tag": tag} | overrides
        qs = "&".join(
            f"{k}={html.escape(v, quote=True)}" for k, v in params.items() if v
        )
        return f"/?{qs}" if qs else "/"

    toolbar = ""
    if total:
        folder_chips = f'<a class="chip{" chip-on" if not folder else ""}" href="{filt_url(folder="")}">все</a>' + "".join(
            f'<a class="chip{" chip-on" if folder == f else ""}" href="{filt_url(folder=f)}">▸ {html.escape(f)}</a>'
            for f in folders
        )
        tag_chips = "".join(
            f'<a class="chip chip-tag{" chip-on" if tag == t else ""}" href="{filt_url(tag="" if tag == t else t)}">#{html.escape(t)}</a>'
            for t in all_tags
        )
        toolbar = f"""
<div class="toolbar">
  <form class="search-form" method="get" action="/">
    <input type="search" name="q" value="{html.escape(q, quote=True)}" placeholder="поиск по названию, содержимому, тегам…">
    <input type="hidden" name="folder" value="{html.escape(folder, quote=True)}">
    <input type="hidden" name="tag" value="{html.escape(tag, quote=True)}">
    <button class="btn" type="submit">Найти</button>
  </form>
  <div class="chips-row">{folder_chips}</div>
  {f'<div class="chips-row">{tag_chips}</div>' if tag_chips else ""}
</div>"""

    if not rows:
        if total == 0:
            empty = """
<div class="empty">
  <div class="glyph">~/scripts</div>
  <p>Пока ни одного скрипта. Создайте первый — и раздавайте его одной командой curl.</p>
  <a class="btn btn-primary" href="/new">+ Новый скрипт</a>
</div>"""
        else:
            empty = f"""
<div class="empty">
  <div class="glyph">not found</div>
  <p>Ничего не нашлось по заданным фильтрам.</p>
  <a class="btn" href="/">Сбросить фильтры</a>
</div>"""
        body = f"""
<div class="page-head"><div><span class="kicker">репозиторий</span><h1>Мои скрипты</h1></div></div>
{toolbar}
{empty}"""
        return page("Скрипты", body, user=uname)

    cards = []
    for i, row in enumerate(rows):
        url = raw_url(request, row["slug"])
        esc_url = html.escape(url, quote=True)
        sid = str(row["_id"])
        row_folder = row.get("folder", "")
        row_tags = row.get("tags", [])
        chips = f'<span class="chip chip-lang">{html.escape(script_interp(row)["label"].lower())}</span>'
        if row_folder:
            chips += f'<a class="chip" href="{filt_url(folder=row_folder)}">▸ {html.escape(row_folder)}</a>'
        chips += "".join(
            f'<a class="chip chip-tag" href="{filt_url(tag=t)}">#{html.escape(t)}</a>'
            for t in row_tags
        )
        meta_chips = f'<div class="chips-row card-chips">{chips}</div>'
        cards.append(f"""
<div class="card" style="animation-delay: {min(i * 60, 480)}ms">
  <div class="card-top">
    <span class="card-name">{html.escape(row["name"])}</span>
    <span class="muted">↓ {row.get("downloads", 0)} · обновлён {html.escape(fmt_dt(row.get("updated_at")))}</span>
  </div>
  {meta_chips}
  <a class="card-url" href="{esc_url}" target="_blank" rel="noopener">{esc_url}</a>
  <div class="card-actions">
    <button class="btn btn-sm" type="button" data-copy="{esc_url}">Копировать ссылку</button>
    <a class="btn btn-sm" href="/scripts/{sid}/edit">Изменить</a>
    <form method="post" action="/scripts/{sid}/duplicate">
      <button class="btn btn-sm" type="submit">Дублировать</button>
    </form>
    <form method="post" action="/scripts/{sid}/delete"
          onsubmit="return confirm('Удалить скрипт «{html.escape(row["name"], quote=True)}»? Это действие необратимо.')">
      <button class="btn btn-sm btn-danger" type="submit">Удалить</button>
    </form>
  </div>
</div>""")

    shown = (
        f"показано: {len(rows)} из {total}" if len(rows) != total else f"в хранилище: {total}"
    )
    body = f"""
<div class="page-head">
  <div><span class="kicker">репозиторий</span><h1>Мои скрипты</h1></div>
  <span class="muted">{shown}</span>
</div>
{toolbar}
{"".join(cards)}"""
    return page("Скрипты", body, user=uname)


async def editor_meta_fields(
    user: dict,
    *,
    folder: str = "",
    tags: list | None = None,
    interpreter: str = DEFAULT_INTERPRETER,
) -> str:
    """Папка, теги, интерпретатор + datalist папок + подсказка о переменных."""
    folders = sorted(
        f
        for f in await scripts_col.distinct("folder", {"owner": str(user["_id"])})
        if f
    )
    options = "".join(
        f'<option value="{html.escape(f, quote=True)}">' for f in folders
    )
    interpreter = norm_interpreter(interpreter)
    interp_options = "".join(
        f'<option value="{key}" data-hl="{meta["hl"]}"{" selected" if key == interpreter else ""}>{meta["label"]}</option>'
        for key, meta in INTERPRETERS.items()
    )
    var_names = sorted(user_variables(user))
    if var_names:
        vars_hint = (
            "Доступны переменные (подставляются при выдаче по ссылке): "
            + ", ".join(f"<code>{{{{{html.escape(n)}}}}}</code>" for n in var_names)
        )
    else:
        vars_hint = (
            'Можно использовать <code>{{ИМЯ}}</code>-переменные (токены, домены…) — '
            'добавьте их в <a href="/settings" style="color:var(--lime)">настройках</a>.'
        )
    return f"""
  <div class="field-row">
    <div>
      <label for="folder">Папка</label>
      <input type="text" id="folder" name="folder" maxlength="64" list="folders-list"
             value="{html.escape(folder, quote=True)}" placeholder="например: eth-nodes">
      <datalist id="folders-list">{options}</datalist>
    </div>
    <div>
      <label for="tags">Теги (через запятую)</label>
      <input type="text" id="tags" name="tags" maxlength="400"
             value="{html.escape(", ".join(tags or []), quote=True)}" placeholder="mainnet, docker, v2">
    </div>
    <div>
      <label for="interpreter">Интерпретатор</label>
      <select id="interpreter" name="interpreter">{interp_options}</select>
    </div>
  </div>
  <p class="muted vars-hint">{vars_hint}</p>"""


@app.get("/new")
async def new_form(request: Request):
    user = await current_user(request)
    if not user:
        return login_redirect(request)
    body = f"""
<div class="page-head"><div><span class="kicker">создание</span><h1>Новый скрипт</h1></div></div>
<form class="editor" method="post" action="/scripts">
  <label for="name">Название</label>
  <input type="text" id="name" name="name" required maxlength="200" placeholder="install-node.sh" autofocus>
  {await editor_meta_fields(user)}
  {ai_panel(user)}
  <label for="content">Содержимое</label>
  <div class="code-editor">
    <pre class="hl-pre" aria-hidden="true"><code id="hl-code" class="hljs" data-hl="bash"></code></pre>
    <textarea id="content" name="content" spellcheck="false" autocomplete="off"
              placeholder="#!/usr/bin/env bash&#10;set -euo pipefail&#10;..."></textarea>
  </div>
  <div class="form-actions">
    <button class="btn btn-primary" type="submit">Сохранить</button>
    <a class="btn" href="/">Отмена</a>
  </div>
</form>"""
    return page("Новый скрипт", body, user=user["username"], code=True)


@app.post("/scripts")
async def create_script(
    request: Request,
    name: str = Form(...),
    content: str = Form(""),
    folder: str = Form(""),
    tags: str = Form(""),
    interpreter: str = Form(DEFAULT_INTERPRETER),
):
    user = await current_user(request)
    if not user:
        return login_redirect(request)
    name = name.strip() or "Без названия"
    ts = now_iso()
    slug = await new_slug()
    result = await scripts_col.insert_one(
        {
            "slug": slug,
            "name": name,
            "content": normalize_newlines(content),
            "folder": folder.strip()[:64],
            "tags": parse_tags(tags),
            "interpreter": norm_interpreter(interpreter),
            "owner": str(user["_id"]),
            "created_at": ts,
            "updated_at": ts,
        }
    )
    return RedirectResponse(f"/scripts/{result.inserted_id}/edit", status_code=303)


async def _owned_script(request: Request, script_id: str):
    """Вернуть (user, script) или (user, None) — гарантирует владельца."""
    user = await current_user(request)
    if not user:
        return None, None
    try:
        oid = ObjectId(script_id)
    except (InvalidId, TypeError):
        return user, None
    script = await scripts_col.find_one({"_id": oid, "owner": str(user["_id"])})
    return user, script


@app.get("/scripts/{script_id}/edit")
async def edit_form(request: Request, script_id: str = Path(...)):
    user, row = await _owned_script(request, script_id)
    if not user:
        return login_redirect(request)
    if row is None:
        return page("Не найдено", "<h1>Скрипт не найден</h1>", user=user["username"])

    url = raw_url(request, row["slug"])
    esc_url = html.escape(url, quote=True)
    curl_cmd = run_command(url, row)
    interp = script_interp(row)
    downloads = row.get("downloads", 0)
    versions_count = await versions_col.count_documents({"script_id": script_id})
    body = f"""
<div class="page-head"><div><span class="kicker">редактор</span><h1>Редактирование</h1></div></div>
<div class="share">
  <span class="kicker">прямая ссылка</span>
  <code>{esc_url}</code>
  <span class="kicker">{"запуск одной командой" if interp["pipe"] else "скачивание"}</span>
  <code>{html.escape(curl_cmd)}</code>
  <button class="btn btn-sm" type="button" data-copy="{esc_url}">Копировать ссылку</button>
  <button class="btn btn-sm" type="button" data-copy="{html.escape(curl_cmd, quote=True)}">Копировать curl</button>
</div>
<div class="meta-row">
  <span class="meta-stat">скачиваний: <b>{downloads}</b></span>
  <a class="btn btn-sm" href="/scripts/{script_id}/log">Лог обращений</a>
  <a class="btn btn-sm" href="/scripts/{script_id}/versions">История версий ({versions_count})</a>
</div>
<form class="editor" method="post" action="/scripts/{script_id}">
  <label for="name">Название</label>
  <input type="text" id="name" name="name" required maxlength="200" value="{html.escape(row["name"], quote=True)}">
  {await editor_meta_fields(user, folder=row.get("folder", ""), tags=row.get("tags", []), interpreter=row.get("interpreter", DEFAULT_INTERPRETER))}
  {ai_panel(user)}
  <label for="content">Содержимое</label>
  <div class="code-editor">
    <pre class="hl-pre" aria-hidden="true"><code id="hl-code" class="hljs" data-hl="{interp["hl"]}"></code></pre>
    <textarea id="content" name="content" spellcheck="false" autocomplete="off">{html.escape(row["content"])}</textarea>
  </div>
  <div class="form-actions">
    <button class="btn btn-primary" type="submit">Сохранить</button>
    <a class="btn" href="/">К списку</a>
  </div>
</form>"""
    return page(f"Редактирование — {row['name']}", body, user=user["username"], code=True)


async def snapshot_version(row: dict) -> None:
    """Сохранить текущее состояние скрипта в историю версий (с обрезкой хвоста)."""
    sid = str(row["_id"])
    await versions_col.insert_one(
        {
            "script_id": sid,
            "name": row["name"],
            "content": row["content"],
            "saved_at": now_iso(),
        }
    )
    extra = (
        await versions_col.find({"script_id": sid}, {"_id": 1})
        .sort("_id", -1)  # ObjectId монотонен — стабильный порядок версий
        .skip(VERSIONS_KEEP)
        .to_list(length=200)
    )
    if extra:
        await versions_col.delete_many({"_id": {"$in": [v["_id"] for v in extra]}})


@app.post("/scripts/{script_id}")
async def update_script(
    request: Request,
    script_id: str = Path(...),
    name: str = Form(...),
    content: str = Form(""),
    folder: str = Form(""),
    tags: str = Form(""),
    interpreter: str = Form(DEFAULT_INTERPRETER),
):
    user, row = await _owned_script(request, script_id)
    if not user:
        return login_redirect(request)
    if row is None:
        return RedirectResponse("/", status_code=303)
    new_name = name.strip() or "Без названия"
    new_content = normalize_newlines(content)
    new_folder = folder.strip()[:64]
    new_tags = parse_tags(tags)
    if new_name != row["name"] or new_content != row["content"]:
        await snapshot_version(row)  # прошлое состояние уходит в историю
    await scripts_col.update_one(
        {"_id": row["_id"]},
        {
            "$set": {
                "name": new_name,
                "content": new_content,
                "folder": new_folder,
                "tags": new_tags,
                "interpreter": norm_interpreter(interpreter),
                "updated_at": now_iso(),
            }
        },
    )
    return RedirectResponse(f"/scripts/{script_id}/edit", status_code=303)


@app.post("/scripts/{script_id}/delete")
async def delete_script(request: Request, script_id: str = Path(...)):
    user, row = await _owned_script(request, script_id)
    if not user:
        return login_redirect(request)
    if row is not None:
        sid = str(row["_id"])
        await scripts_col.delete_one({"_id": row["_id"]})
        await versions_col.delete_many({"script_id": sid})
        await access_col.delete_many({"script_id": sid})
    return RedirectResponse("/", status_code=303)


@app.post("/scripts/{script_id}/duplicate")
async def duplicate_script(request: Request, script_id: str = Path(...)):
    """Копия скрипта: новый slug, чистые счётчики и история."""
    user, row = await _owned_script(request, script_id)
    if not user:
        return login_redirect(request)
    if row is None:
        return RedirectResponse("/", status_code=303)
    ts = now_iso()
    result = await scripts_col.insert_one(
        {
            "slug": await new_slug(),
            "name": f"{row['name']} (копия)"[:200],
            "content": row["content"],
            "folder": row.get("folder", ""),
            "tags": row.get("tags", []),
            "interpreter": row.get("interpreter", DEFAULT_INTERPRETER),
            "owner": str(user["_id"]),
            "created_at": ts,
            "updated_at": ts,
        }
    )
    return RedirectResponse(f"/scripts/{result.inserted_id}/edit", status_code=303)


# ---- лог обращений к ссылке --------------------------------------------------


@app.get("/scripts/{script_id}/log")
async def access_log_page(request: Request, script_id: str = Path(...)):
    user, row = await _owned_script(request, script_id)
    if not user:
        return login_redirect(request)
    if row is None:
        return page("Не найдено", "<h1>Скрипт не найден</h1>", user=user["username"])

    entries = (
        await access_col.find({"script_id": script_id})
        .sort("_id", -1)
        .to_list(length=200)
    )
    total = await access_col.count_documents({"script_id": script_id})
    downloads = row.get("downloads", 0)

    if entries:
        rows_html = "".join(
            f"""
  <div class="log-row">
    <span class="log-ts">{html.escape(fmt_dt(e.get("ts", "")))}</span>
    <span class="log-ip">{html.escape(e.get("ip", "?"))}</span>
    <span class="log-ua" title="{html.escape(e.get("ua", ""), quote=True)}">{html.escape(e.get("ua", "")[:80] or "—")}</span>
  </div>"""
            for e in entries
        )
        listing = f'<div class="log-table">{rows_html}</div>'
        if total > len(entries):
            listing += f'<p class="muted" style="margin-top:12px">Показаны последние {len(entries)} из {total}; записи старше {ACCESS_LOG_TTL_DAYS} дней удаляются автоматически.</p>'
    else:
        listing = """
<div class="empty">
  <div class="glyph">0 hits</div>
  <p>По этой ссылке ещё никто не приходил. Раздайте её — обращения появятся здесь.</p>
</div>"""

    body = f"""
<div class="page-head">
  <div><span class="kicker">лог обращений</span><h1>{html.escape(row["name"])}</h1></div>
  <span class="muted">всего скачиваний: {downloads}</span>
</div>
{listing}
<div class="form-actions">
  <a class="btn" href="/scripts/{script_id}/edit">← К редактору</a>
</div>"""
    return page(f"Лог — {row['name']}", body, user=user["username"])


# ---- история версий ----------------------------------------------------------


async def _owned_version(user: dict, script_id: str, version_id: str):
    try:
        void = ObjectId(version_id)
    except (InvalidId, TypeError):
        return None
    return await versions_col.find_one({"_id": void, "script_id": script_id})


@app.get("/scripts/{script_id}/versions")
async def versions_page(request: Request, script_id: str = Path(...)):
    user, row = await _owned_script(request, script_id)
    if not user:
        return login_redirect(request)
    if row is None:
        return page("Не найдено", "<h1>Скрипт не найден</h1>", user=user["username"])

    versions = (
        await versions_col.find({"script_id": script_id})
        .sort("_id", -1)  # ObjectId монотонен — стабильный порядок версий
        .to_list(length=VERSIONS_KEEP)
    )

    if versions:
        total = len(versions)
        items = []
        for i, v in enumerate(versions):
            vid = str(v["_id"])
            size = len(v.get("content", "").encode())
            items.append(f"""
<div class="card" style="animation-delay: {min(i * 50, 400)}ms">
  <div class="card-top">
    <span class="card-name">v{total - i} — {html.escape(v.get("name", ""))}</span>
    <span class="muted">{html.escape(fmt_dt(v.get("saved_at", "")))} · {size} байт</span>
  </div>
  <div class="card-actions" style="margin-top:12px">
    <a class="btn btn-sm" href="/scripts/{script_id}/versions/{vid}">Просмотр</a>
    <form method="post" action="/scripts/{script_id}/versions/{vid}/restore"
          onsubmit="return confirm('Откатить скрипт к версии v{total - i}? Текущее состояние сохранится в истории.')">
      <button class="btn btn-sm" type="submit">Восстановить</button>
    </form>
  </div>
</div>""")
        listing = "".join(items)
    else:
        listing = """
<div class="empty">
  <div class="glyph">v0</div>
  <p>История пуста — версии появляются после первого изменения скрипта.</p>
</div>"""

    body = f"""
<div class="page-head">
  <div><span class="kicker">история версий</span><h1>{html.escape(row["name"])}</h1></div>
  <span class="muted">хранится до {VERSIONS_KEEP} версий</span>
</div>
{listing}
<div class="form-actions">
  <a class="btn" href="/scripts/{script_id}/edit">← К редактору</a>
</div>"""
    return page(f"Версии — {row['name']}", body, user=user["username"])


@app.get("/scripts/{script_id}/versions/{version_id}")
async def version_view(
    request: Request, script_id: str = Path(...), version_id: str = Path(...)
):
    user, row = await _owned_script(request, script_id)
    if not user:
        return login_redirect(request)
    if row is None:
        return page("Не найдено", "<h1>Скрипт не найден</h1>", user=user["username"])
    version = await _owned_version(user, script_id, version_id)
    if version is None:
        return RedirectResponse(f"/scripts/{script_id}/versions", status_code=303)

    hl_lang = script_interp(row)["hl"]
    body = f"""
<div class="page-head">
  <div><span class="kicker">просмотр версии</span><h1>{html.escape(version.get("name", ""))}</h1></div>
  <span class="muted">сохранена {html.escape(fmt_dt(version.get("saved_at", "")))}</span>
</div>
<pre class="code-view"><code class="language-{hl_lang}">{html.escape(version.get("content", ""))}</code></pre>
<div class="form-actions">
  <form method="post" action="/scripts/{script_id}/versions/{version_id}/restore"
        onsubmit="return confirm('Откатить скрипт к этой версии? Текущее состояние сохранится в истории.')">
    <button class="btn btn-primary" type="submit">Восстановить эту версию</button>
  </form>
  <a class="btn" href="/scripts/{script_id}/versions">← К списку версий</a>
</div>"""
    return page(f"Версия — {row['name']}", body, user=user["username"], code=True)


@app.post("/scripts/{script_id}/versions/{version_id}/restore")
async def version_restore(
    request: Request, script_id: str = Path(...), version_id: str = Path(...)
):
    user, row = await _owned_script(request, script_id)
    if not user:
        return login_redirect(request)
    if row is None:
        return RedirectResponse("/", status_code=303)
    version = await _owned_version(user, script_id, version_id)
    if version is None:
        return RedirectResponse(f"/scripts/{script_id}/versions", status_code=303)
    # текущее состояние тоже уходит в историю — откат работает в обе стороны
    await snapshot_version(row)
    await scripts_col.update_one(
        {"_id": row["_id"]},
        {
            "$set": {
                "name": version.get("name", row["name"]),
                "content": version.get("content", ""),
                "updated_at": now_iso(),
            }
        },
    )
    return RedirectResponse(f"/scripts/{script_id}/edit", status_code=303)


# ---- настройки: персональный Claude API-ключ --------------------------------


def settings_body(
    user: dict, *, saved: bool = False, error: str = "", var_error: str = ""
) -> str:
    key = user_ai_key(user)
    err_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    if key:
        status = f"""
  <span class="kicker">текущий ключ</span>
  <code>{html.escape(mask_key(key))}</code>
  <form method="post" action="/settings/token/delete" style="display:inline"
        onsubmit="return confirm('Удалить сохранённый API-ключ?')">
    <button class="btn btn-sm btn-danger" type="submit">Удалить ключ</button>
  </form>"""
    else:
        status = """
  <span class="kicker">текущий ключ</span>
  <p class="muted">Ключ не задан — ИИ-помощник в редакторе выключен.</p>"""
    saved_html = (
        '<div class="ai-box"><span class="kicker">готово</span>'
        '<p class="muted">Ключ сохранён — ИИ-помощник включён в редакторе.</p></div>'
        if saved
        else ""
    )
    return f"""
<div class="page-head"><div><span class="kicker">настройки</span><h1>Claude API</h1></div></div>
{saved_html}
<div class="share">
{status}
</div>
<form class="editor" method="post" action="/settings/token">
  {err_html}
  <label for="anthropic_key">Ваш Anthropic API-ключ</label>
  <input type="password" id="anthropic_key" name="anthropic_key" required
         placeholder="sk-ant-..." autocomplete="off">
  <p class="muted" style="margin-top:10px">Ключ используется только для генерации ваших
  скриптов и хранится на сервере. Получить ключ: console.anthropic.com → API Keys.</p>
  <div class="form-actions">
    <button class="btn btn-primary" type="submit">Сохранить ключ</button>
    <a class="btn" href="/">К списку</a>
  </div>
</form>
{variables_section(user, var_error)}"""


def variables_section(user: dict, error: str = "") -> str:
    variables = user_variables(user)
    err_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    if variables:
        rows = "".join(
            f"""
  <div class="var-row">
    <code class="var-name">{{{{{html.escape(name)}}}}}</code>
    <span class="var-value" title="значение скрыто">{html.escape(mask_key(value))}</span>
    <form method="post" action="/settings/vars/delete"
          onsubmit="return confirm('Удалить переменную {{{{{html.escape(name, quote=True)}}}}}?')">
      <input type="hidden" name="var_name" value="{html.escape(name, quote=True)}">
      <button class="btn btn-sm btn-danger" type="submit">Удалить</button>
    </form>
  </div>"""
            for name, value in sorted(variables.items())
        )
        listing = f'<div class="var-table">{rows}</div>'
    else:
        listing = '<p class="muted">Переменных пока нет.</p>'
    return f"""
<div class="page-head" style="margin-top:48px"><div><span class="kicker">глобальные переменные</span><h1>Переменные</h1></div></div>
<div class="share">
  <p class="muted" style="margin-bottom:14px">Пишите в скриптах <code style="display:inline;padding:2px 6px;margin:0">{{{{TOKEN}}}}</code>,
  <code style="display:inline;padding:2px 6px;margin:0">{{{{DOMAIN}}}}</code> и т.д. — при выдаче по ссылке
  плейсхолдеры заменяются на ваши значения. Значения шифруются в БД. Повторное
  сохранение имени перезаписывает значение.</p>
  {listing}
</div>
<form class="editor" method="post" action="/settings/vars">
  {err_html}
  <div class="field-row">
    <div>
      <label for="var_name">Имя (A-Z, 0-9, _)</label>
      <input type="text" id="var_name" name="var_name" required maxlength="64"
             placeholder="TOKEN" style="text-transform:uppercase">
    </div>
    <div>
      <label for="var_value">Значение</label>
      <input type="text" id="var_value" name="var_value" required maxlength="2000"
             placeholder="например: abc123 или node.example.com" autocomplete="off">
    </div>
  </div>
  <div class="form-actions">
    <button class="btn btn-primary" type="submit">Сохранить переменную</button>
  </div>
</form>"""


@app.get("/settings")
async def settings_page(request: Request, saved: int = 0):
    user = await current_user(request)
    if not user:
        return login_redirect(request)
    return page("Настройки", settings_body(user, saved=bool(saved)), user=user["username"])


@app.post("/settings/token")
async def settings_save_token(request: Request, anthropic_key: str = Form(...)):
    user = await current_user(request)
    if not user:
        return login_redirect(request)
    key = anthropic_key.strip()
    if len(key) < 20 or any(c.isspace() for c in key):
        return page(
            "Настройки",
            settings_body(user, error="Это не похоже на API-ключ (sk-ant-…)"),
            user=user["username"],
        )
    await users_col.update_one(
        {"_id": user["_id"]}, {"$set": {"anthropic_key": encrypt_secret(key)}}
    )
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/settings/token/delete")
async def settings_delete_token(request: Request):
    user = await current_user(request)
    if not user:
        return login_redirect(request)
    await users_col.update_one({"_id": user["_id"]}, {"$unset": {"anthropic_key": ""}})
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/vars")
async def settings_save_var(
    request: Request, var_name: str = Form(...), var_value: str = Form(...)
):
    user = await current_user(request)
    if not user:
        return login_redirect(request)
    name = var_name.strip().upper()
    value = var_value.strip()
    if not VAR_NAME_RE.match(name):
        return page(
            "Настройки",
            settings_body(user, var_error="Имя: латинские A-Z, цифры и _, начинается с буквы"),
            user=user["username"],
        )
    if not value:
        return page(
            "Настройки",
            settings_body(user, var_error="Значение не может быть пустым"),
            user=user["username"],
        )
    await users_col.update_one(
        {"_id": user["_id"]},
        {"$set": {f"variables.{name}": encrypt_secret(value)}},
    )
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/vars/delete")
async def settings_delete_var(request: Request, var_name: str = Form(...)):
    user = await current_user(request)
    if not user:
        return login_redirect(request)
    name = var_name.strip().upper()
    if VAR_NAME_RE.match(name):
        await users_col.update_one(
            {"_id": user["_id"]}, {"$unset": {f"variables.{name}": ""}}
        )
    return RedirectResponse("/settings", status_code=303)


# ---- ИИ-помощник -----------------------------------------------------------


@app.post("/ai/generate")
async def ai_generate(
    request: Request,
    md: UploadFile = File(None),
    instructions: str = Form(""),
    interpreter: str = Form(DEFAULT_INTERPRETER),
):
    user = await current_user(request)
    if not user:
        return PlainTextResponse("Требуется вход", status_code=401)
    if AsyncAnthropic is None:
        return PlainTextResponse(
            "ИИ недоступен: на сервере не установлен пакет anthropic", status_code=503
        )
    key = user_ai_key(user)
    if not key:
        return PlainTextResponse(
            "Добавьте свой Claude API-ключ в настройках", status_code=403
        )

    doc_text = ""
    if md is not None:
        raw = await md.read()
        doc_text = raw.decode("utf-8", errors="replace")
    if not doc_text.strip() and not instructions.strip():
        return PlainTextResponse("Прикрепите .md или опишите задачу", status_code=400)

    prompt = build_ai_prompt(doc_text, instructions, interpreter)
    aclient = AsyncAnthropic(api_key=key)

    async def gen():
        try:
            async with aclient.messages.stream(
                model=AI_MODEL,
                max_tokens=8000,
                system=AI_SYSTEM,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:  # ошибки Claude отдаём в поток как комментарий
            if type(e).__name__ == "AuthenticationError":
                yield "\n# Ключ не принят Anthropic — проверьте его в настройках.\n"
            else:
                yield f"\n# Ошибка генерации: {e}\n"

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


# ---- публичная выдача ------------------------------------------------------


@app.get("/raw/{slug}")
async def raw(request: Request, slug: str):
    """Публичная выдача скрипта — без авторизации, защищено неугадываемым slug."""
    row = await scripts_col.find_one({"slug": slug})
    if row is None:
        return PlainTextResponse("Not found\n", status_code=404)
    try:  # лог не должен ломать выдачу скрипта
        await access_col.insert_one(
            {
                "script_id": str(row["_id"]),
                "slug": slug,
                "ts": now_iso(),
                "ts_dt": datetime.now(timezone.utc),  # для TTL-индекса
                "ip": client_ip(request),
                "ua": request.headers.get("user-agent", "")[:300],
            }
        )
        await scripts_col.update_one({"_id": row["_id"]}, {"$inc": {"downloads": 1}})
    except Exception as e:
        print(f"[!] Не удалось записать лог обращения: {e}", file=sys.stderr)

    content = row["content"]
    if "{{" in content:  # подстановка глобальных переменных владельца
        try:
            owner = await users_col.find_one({"_id": ObjectId(row["owner"])})
            content = substitute_vars(content, user_variables(owner))
        except Exception as e:
            print(f"[!] Не удалось подставить переменные: {e}", file=sys.stderr)

    return PlainTextResponse(
        content,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
