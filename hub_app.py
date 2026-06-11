#!/usr/bin/env python3
"""
nodewiki hub — главная страница платформы с единым входом (SSO).

Логин/регистрация живут здесь; сессия — HMAC-cookie на домене .nodewiki.info,
поэтому она валидна на всех поддоменах (scripts, checker, …). Использует ту же
MongoDB (коллекция users) и тот же SECRET_KEY, что и Script Vault — поэтому
оба сервиса принимают одну и ту же сессию.

Переменные окружения:
  TOKEN_DB       строка подключения к MongoDB (та же база RS_2)
  SECRET_KEY     тот же секрет, что у Script Vault (иначе SSO не сработает)
  COOKIE_DOMAIN  домен cookie (в проде ".nodewiki.info"; пусто = host-only)
  BASE_URL       внешний адрес хаба (https://nodewiki.info)
  HOST, PORT     адрес/порт uvicorn (по умолчанию 127.0.0.1:8001)

Запуск:  TOKEN_DB=... SECRET_KEY=... COOKIE_DOMAIN=.nodewiki.info python hub_app.py
"""

import hashlib
import hmac
import html
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from bson.errors import InvalidId

# ----------------------------------------------------------------------------
# Конфигурация (та же БД и тот же SECRET_KEY, что у Script Vault)
# ----------------------------------------------------------------------------

TOKEN_DB = os.environ.get("TOKEN_DB", "mongodb://localhost:27017")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
COOKIE_DOMAIN = os.environ.get("COOKIE_DOMAIN", "")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", "8001"))
HOST = os.environ.get("HOST", "127.0.0.1")

SCRIPTS_URL = os.environ.get("SCRIPTS_URL", "https://scripts.nodewiki.info")

COOKIE_NAME = "session"
SESSION_TTL = 60 * 60 * 24 * 30  # 30 дней

if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(32)
    print(
        "[!] SECRET_KEY не задан — сгенерирован временный. Для SSO он обязан "
        "совпадать с SECRET_KEY Script Vault!",
        file=sys.stderr,
    )

cluster = AsyncIOMotorClient(TOKEN_DB)
db = cluster["RS_2"]
users_col = db["users"]

# ----------------------------------------------------------------------------
# Пароли и сессии — формат идентичен Script Vault (это и есть SSO)
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
        domain=COOKIE_DOMAIN or None,
    )


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


def safe_next(next_url: str) -> str:
    """Разрешаем возврат только на свои поддомены (анти open-redirect)."""
    if not next_url:
        return "/"
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    try:
        host = urlsplit(next_url).hostname or ""
    except ValueError:
        return "/"
    if host == "nodewiki.info" or host.endswith(".nodewiki.info"):
        return next_url
    return "/"


# ---- rate-limit входа (как в Script Vault) -----------------------------------

AUTH_RATE_MAX = 10
AUTH_RATE_WINDOW = 300
_auth_attempts: dict[str, list[float]] = {}


def auth_rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _auth_attempts.get(ip, []) if now - t < AUTH_RATE_WINDOW]
    if len(attempts) >= AUTH_RATE_MAX:
        _auth_attempts[ip] = attempts
        return True
    attempts.append(now)
    _auth_attempts[ip] = attempts
    if len(_auth_attempts) > 10_000:
        _auth_attempts.clear()
    return False


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------

CSS = """
:root {
  --bg: #0b0b0c; --panel: #121214; --panel-2: #171719;
  --line: #25252b; --line-bright: #36363f;
  --ink: #ece9e0; --muted: #817e75;
  --lime: #c6f23f; --lime-soft: #d6ff52; --lime-dim: #9cbf33;
  --cyan: #38e1c2; --coral: #ff5d4e;
  --mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --display: "Syne", "JetBrains Mono", sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { color-scheme: dark; -webkit-text-size-adjust: 100%; }
::selection { background: var(--lime); color: #0c0d07; }
body {
  font: 15px/1.6 var(--mono); background: var(--bg); color: var(--ink);
  min-height: 100vh; position: relative; overflow-x: hidden;
}
body::before {
  content: ""; position: fixed; inset: 0; z-index: -2;
  background:
    linear-gradient(var(--line) 1px, transparent 1px) 0 0 / 100% 64px,
    linear-gradient(90deg, var(--line) 1px, transparent 1px) 0 0 / 64px 100%,
    radial-gradient(120% 70% at 80% -10%, rgba(198,242,63,.13), transparent 56%),
    radial-gradient(120% 80% at -10% 110%, rgba(56,225,194,.08), transparent 55%),
    var(--bg);
  -webkit-mask-image: radial-gradient(150% 110% at 50% 0%, #000 50%, transparent 100%);
          mask-image: radial-gradient(150% 110% at 50% 0%, #000 50%, transparent 100%);
}
body::after {
  content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none;
  background: repeating-linear-gradient(0deg, rgba(0,0,0,.16) 0 1px, transparent 1px 3px);
  opacity: .4; mix-blend-mode: multiply;
}
.wrap { max-width: 920px; margin: 0 auto; padding: 0 22px; }
header {
  position: sticky; top: 0; z-index: 20;
  backdrop-filter: blur(11px) saturate(1.3);
  -webkit-backdrop-filter: blur(11px) saturate(1.3);
  background: rgba(11,11,12,.78); border-bottom: 1px solid var(--line);
}
.header-inner {
  max-width: 920px; margin: 0 auto; padding: 15px 22px;
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
}
.logo {
  font-family: var(--display); font-weight: 800; font-size: 19px;
  letter-spacing: -.6px; color: var(--ink); text-decoration: none;
  display: inline-flex; align-items: center;
}
.logo b { color: var(--lime); }
.logo::after { content: "_"; color: var(--lime); margin-left: 2px; animation: blink 1.1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
.header-actions { display: flex; gap: 10px; align-items: center; }
.header-actions form { display: inline; }
.user-chip {
  font-family: var(--mono); font-size: 12px; color: var(--lime-dim);
  border: 1px solid var(--line-bright); border-radius: 2px; padding: 6px 11px;
}
.user-chip::before { content: "@"; color: var(--muted); }
.btn {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 9px 15px; border-radius: 2px;
  border: 1px solid var(--line-bright); background: var(--panel-2);
  color: var(--ink); font-family: var(--mono); font-size: 13px; font-weight: 600;
  letter-spacing: .3px; text-transform: lowercase;
  text-decoration: none; cursor: pointer; white-space: nowrap;
  transition: transform .12s, box-shadow .12s, border-color .15s, color .15s, background .15s;
}
.btn:hover { border-color: var(--lime); color: var(--lime); transform: translate(-2px,-2px); box-shadow: 4px 4px 0 #000; }
.btn:active { transform: translate(0,0); box-shadow: 0 0 0 #000; }
.btn-primary { background: var(--lime); color: #11130a; border-color: var(--lime); font-weight: 700; box-shadow: 4px 4px 0 #000; }
.btn-primary:hover { background: var(--lime-soft); color: #11130a; border-color: var(--lime-soft); box-shadow: 6px 6px 0 #000; }
.btn-sm { padding: 7px 12px; font-size: 12px; }

.hero { padding: 64px 0 28px; animation: rise .6s cubic-bezier(.2,.7,.2,1) both; }
.kicker {
  display: inline-block; font-family: var(--mono); font-size: 11px;
  letter-spacing: 3px; text-transform: uppercase; color: var(--lime-dim); margin-bottom: 16px;
}
.kicker::before { content: "> "; }
h1 {
  font-family: var(--display); font-weight: 800;
  font-size: clamp(34px, 7vw, 62px); line-height: .98; letter-spacing: -2px;
}
h1 .accent {
  background: linear-gradient(100deg, var(--lime), var(--cyan));
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent; color: transparent;
}
.lede { color: var(--muted); font-size: 15.5px; max-width: 560px; margin-top: 18px; line-height: 1.7; }

.tools {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 16px; padding: 16px 0 30px;
}
.tool {
  position: relative; display: flex; flex-direction: column;
  background: var(--panel); border: 1px solid var(--line);
  border-left: 2px solid var(--line-bright); border-radius: 3px;
  padding: 20px 20px 18px; text-decoration: none; color: var(--ink);
  min-height: 168px;
  animation: rise .55s cubic-bezier(.2,.7,.2,1) both;
  transition: transform .16s, border-color .16s, box-shadow .16s;
}
.tool.live:hover { transform: translate(-4px,-4px); border-color: var(--line-bright); border-left-color: var(--lime); box-shadow: 8px 8px 0 #000; }
.tool.soon { cursor: default; }
.tool.soon:hover { border-left-color: var(--cyan); }
.tool-num { font-family: var(--mono); font-size: 12px; color: var(--muted); }
.tool-name { font-family: var(--display); font-size: 21px; font-weight: 800; letter-spacing: -.5px; margin: 10px 0 8px; }
.tool-desc { color: var(--muted); font-size: 13px; line-height: 1.55; flex: 1; }
.tool-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 14px; }
.tool-host { font-family: var(--mono); font-size: 11.5px; color: var(--lime-dim); word-break: break-all; }
.tool.soon .tool-host { color: var(--muted); }
.badge {
  font-family: var(--mono); font-size: 10.5px; font-weight: 700;
  letter-spacing: 1.5px; text-transform: uppercase; border-radius: 2px; padding: 4px 8px; white-space: nowrap;
}
.badge-live { color: #11130a; background: var(--lime); }
.badge-soon { color: var(--cyan); border: 1px dashed rgba(56,225,194,.5); }
.tool .arrow { position: absolute; top: 18px; right: 18px; color: var(--lime); font-size: 17px; opacity: 0; transform: translate(-4px,4px); transition: .16s; }
.tool.live:hover .arrow { opacity: 1; transform: translate(0,0); }

/* вход / регистрация — терминальное окно */
.auth-wrap { display: flex; justify-content: center; padding: 26px 0 40px; }
.auth-card {
  width: 100%; max-width: 400px; background: var(--panel);
  border: 1px solid var(--line-bright); border-radius: 4px; overflow: hidden;
  animation: rise .6s cubic-bezier(.2,.7,.2,1) both; box-shadow: 10px 10px 0 #000;
}
.auth-bar {
  display: flex; align-items: center; gap: 7px; padding: 12px 15px;
  border-bottom: 1px solid var(--line); background: var(--panel-2);
  font-size: 11px; letter-spacing: 2px; text-transform: uppercase; color: var(--muted);
}
.auth-bar i { width: 9px; height: 9px; border-radius: 50%; background: var(--line-bright); }
.auth-bar i:nth-child(1) { background: var(--coral); }
.auth-bar i:nth-child(3) { background: var(--lime); }
.auth-bar span { margin-left: auto; }
.auth-body { padding: 28px 28px 32px; }
.auth-body h2 { font-family: var(--display); font-size: 23px; font-weight: 800; letter-spacing: -.6px; }
.auth-sub { display: block; color: var(--muted); font-size: 12.5px; margin-top: 5px; }
label {
  display: block; font-size: 11px; font-weight: 600; letter-spacing: 2px;
  text-transform: uppercase; color: var(--muted); margin: 20px 0 8px;
}
label::before { content: "// "; color: var(--lime-dim); }
input[type=text], input[type=password] {
  width: 100%; padding: 12px 14px; border-radius: 2px;
  border: 1px solid var(--line-bright); background: #0d0d0f; color: var(--ink);
  font-family: var(--mono); font-size: 14px;
  transition: border-color .15s, box-shadow .15s;
}
input:focus { outline: none; border-color: var(--lime); box-shadow: 0 0 0 1px var(--lime), 0 0 24px rgba(198,242,63,.13); }
.form-actions { display: flex; gap: 12px; margin-top: 24px; }
.auth-foot { margin-top: 18px; font-size: 12.5px; color: var(--muted); }
.auth-foot a { color: var(--lime); text-decoration: none; border-bottom: 1px dashed rgba(198,242,63,.4); }
.error {
  background: rgba(255,93,78,.1); border: 1px solid rgba(255,93,78,.45);
  border-left: 2px solid var(--coral); color: var(--coral);
  border-radius: 2px; padding: 11px 14px; font-size: 13px; margin-top: 16px;
  animation: shake .4s ease;
}
.error::before { content: "\\2717  "; }
@keyframes shake {
  0%,100% { transform: translateX(0); } 20% { transform: translateX(-7px); }
  40% { transform: translateX(6px); } 60% { transform: translateX(-4px); } 80% { transform: translateX(3px); }
}
footer {
  border-top: 1px solid var(--line); margin-top: 14px;
  padding: 24px 0 56px; color: var(--muted); font-size: 12.5px;
  display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap;
}
@keyframes rise { from { opacity: 0; transform: translateY(18px); } to { opacity: 1; transform: translateY(0); } }
@media (max-width: 560px) { .hero { padding: 40px 0 20px; } .tools { grid-template-columns: 1fr; } }
"""


def page(title: str, body: str, *, user: dict | None = None) -> HTMLResponse:
    if user:
        actions = f"""
      <span class="user-chip">{html.escape(user["username"])}</span>
      <a class="btn btn-sm btn-primary" href="{SCRIPTS_URL}">скрипты</a>
      <form method="post" action="/logout"><button class="btn btn-sm" type="submit">выйти</button></form>"""
    else:
        actions = """
      <a class="btn btn-sm" href="/register">регистрация</a>
      <a class="btn btn-sm btn-primary" href="/login">войти</a>"""
    doc = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#0b0b0c">
  <title>{html.escape(title)} — nodewiki</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Syne:wght@700;800&display=swap">
  <style>{CSS}</style>
</head>
<body>
  <header>
    <div class="header-inner">
      <a class="logo" href="/">node<b>wiki</b></a>
      <div class="header-actions">{actions}</div>
    </div>
  </header>
  <div class="wrap">
{body}
  </div>
</body>
</html>"""
    return HTMLResponse(doc)


def tools_grid(authed: bool) -> str:
    scripts_attrs = (
        f'class="tool live" href="{SCRIPTS_URL}"'
        if authed
        else 'class="tool live" href="/login"'
    )
    return f"""
    <section class="tools">
      <a {scripts_attrs} style="animation-delay:.05s">
        <span class="arrow">↗</span>
        <span class="tool-num">01</span>
        <div class="tool-name">Script Vault</div>
        <div class="tool-desc">Хостинг установочных скриптов: раздача одной командой curl,
        версии, лог обращений, переменные, ИИ-генерация по .md.</div>
        <div class="tool-foot">
          <span class="tool-host">scripts.nodewiki.info</span>
          <span class="badge badge-live">online</span>
        </div>
      </a>
      <div class="tool soon" style="animation-delay:.12s">
        <span class="tool-num">02</span>
        <div class="tool-name">VPN Checker</div>
        <div class="tool-desc">Проверка VLESS-ссылок и JSON-конфигов: парсинг, валидация
        и реальная проверка через туннель — латенси, выход в сеть, гео.</div>
        <div class="tool-foot">
          <span class="tool-host">checker.nodewiki.info</span>
          <span class="badge badge-soon">скоро</span>
        </div>
      </div>
      <div class="tool soon" style="animation-delay:.19s">
        <span class="tool-num">03</span>
        <div class="tool-name">Node Checker</div>
        <div class="tool-desc">Мониторинг доступности нод: статус хоста и портов,
        время отклика, уведомления о падениях.</div>
        <div class="tool-foot">
          <span class="tool-host">nodes.nodewiki.info</span>
          <span class="badge badge-soon">скоро</span>
        </div>
      </div>
    </section>"""


def auth_card(mode: str, error: str = "", next_url: str = "/") -> str:
    err_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    esc_next = html.escape(next_url, quote=True)
    if mode == "register":
        title, action, btn = "Регистрация", "/register", "Создать аккаунт →"
        foot = f'Уже есть аккаунт? <a href="/login?next={esc_next}">Войти</a>'
        sub, bar, ac = "Один аккаунт для всех инструментов", "register · nodewiki", "new-password"
    else:
        title, action, btn = "Вход", "/login", "Войти →"
        foot = f'Нет аккаунта? <a href="/register?next={esc_next}">Зарегистрироваться</a>'
        sub, bar, ac = "Единый вход для всех инструментов", "auth · nodewiki", "current-password"
    return f"""
    <div class="auth-wrap">
      <div class="auth-card">
        <div class="auth-bar"><i></i><i></i><i></i><span>{bar}</span></div>
        <div class="auth-body">
          <h2>{title}</h2>
          <span class="auth-sub">{sub}</span>
          {err_html}
          <form method="post" action="{action}">
            <input type="hidden" name="next" value="{esc_next}">
            <label for="username">Логин</label>
            <input type="text" id="username" name="username" required autofocus
                   autocomplete="username" maxlength="32">
            <label for="password">Пароль</label>
            <input type="password" id="password" name="password" required autocomplete="{ac}">
            <div class="form-actions">
              <button class="btn btn-primary" type="submit">{btn}</button>
            </div>
          </form>
          <div class="auth-foot">{foot}</div>
        </div>
      </div>
    </div>"""


# ----------------------------------------------------------------------------
# Приложение
# ----------------------------------------------------------------------------

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "script-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'"
    )
    return resp


@app.get("/")
async def index(request: Request):
    user = await current_user(request)
    if user:
        hero = f"""
    <section class="hero">
      <span class="kicker">панель инструментов</span>
      <h1>Привет, <span class="accent">{html.escape(user["username"])}</span></h1>
      <p class="lede">Выбирай инструмент. Сессия общая для всех поддоменов —
      повторно входить не нужно.</p>
    </section>"""
        body = hero + tools_grid(True)
    else:
        hero = """
    <section class="hero">
      <span class="kicker">панель инструментов</span>
      <h1>Инструменты для<br><span class="accent">операторов нод</span></h1>
      <p class="lede">Хостинг установочных скриптов, проверка VPN-конфигов и мониторинг
      доступности нод. Один аккаунт — все инструменты.</p>
    </section>"""
        body = hero + auth_card("login") + tools_grid(False)
    body += """
    <footer>
      <span>© nodewiki — self-hosted toolkit</span>
      <span>scripts — online · checker, nodes — в разработке</span>
    </footer>"""
    return page("Главная", body, user=user)


@app.get("/login")
async def login_form(request: Request, error: int = 0, next: str = "/"):
    user = await current_user(request)
    nxt = safe_next(next)
    if user:
        return RedirectResponse(nxt, status_code=303)
    msg = "Неверный логин или пароль" if error else ""
    return page("Вход", auth_card("login", msg, nxt) + tools_grid(False))


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    nxt = safe_next(next)
    if auth_rate_limited(client_ip(request)):
        return page(
            "Вход",
            auth_card("login", "Слишком много попыток — подождите 5 минут", nxt),
        )
    user = await users_col.find_one({"username": username.strip().lower()})
    if user is None or not verify_password(password, user.get("password", "")):
        return RedirectResponse(
            f"/login?error=1&next={html.escape(nxt)}", status_code=303
        )
    resp = RedirectResponse(nxt, status_code=303)
    set_session(resp, str(user["_id"]))
    return resp


@app.get("/register")
async def register_form(request: Request, next: str = "/"):
    nxt = safe_next(next)
    if await current_user(request):
        return RedirectResponse(nxt, status_code=303)
    return page("Регистрация", auth_card("register", "", nxt) + tools_grid(False))


@app.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    nxt = safe_next(next)
    if auth_rate_limited(client_ip(request)):
        return page(
            "Регистрация",
            auth_card("register", "Слишком много попыток — подождите 5 минут", nxt),
        )
    uname = username.strip().lower()
    if not (3 <= len(uname) <= 32) or not all(c.isalnum() or c in "_-." for c in uname):
        return page(
            "Регистрация",
            auth_card("register", "Логин: 3–32 символа, буквы/цифры/._-", nxt),
        )
    if len(password) < 6:
        return page(
            "Регистрация", auth_card("register", "Пароль — минимум 6 символов", nxt)
        )
    if await users_col.find_one({"username": uname}):
        return page("Регистрация", auth_card("register", "Такой логин уже занят", nxt))
    result = await users_col.insert_one(
        {"username": uname, "password": hash_password(password), "created_at": now_iso()}
    )
    resp = RedirectResponse(nxt, status_code=303)
    set_session(resp, str(result.inserted_id))
    return resp


@app.post("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/", domain=COOKIE_DOMAIN or None)
    return resp


@app.get("/health")
async def health():
    return PlainTextResponse("ok")


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
