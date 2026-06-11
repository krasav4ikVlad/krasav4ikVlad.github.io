#!/usr/bin/env python3
"""
Script Vault — self-hosted хостинг скриптов для одного разработчика.

Один файл, FastAPI + SQLite. Вход по паролю (ADMIN_PASSWORD), сессия —
HMAC-подписанная cookie. Каждый скрипт доступен по неугадываемой ссылке
GET /raw/{slug} без авторизации — удобно для `curl -fsSL <url> | bash`.

Запуск:  ADMIN_PASSWORD=... SECRET_KEY=... python app.py
"""

import hmac
import hashlib
import html
import os
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, Form, Path, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

# ----------------------------------------------------------------------------
# Конфигурация
# ----------------------------------------------------------------------------

DEFAULT_PASSWORD = "admin"

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", DEFAULT_PASSWORD)
SECRET_KEY = os.environ.get("SECRET_KEY", "")
DB_PATH = os.environ.get("DB_PATH", "scripts.db")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "0.0.0.0")  # за nginx ставьте 127.0.0.1

COOKIE_NAME = "session"
SESSION_TTL = 60 * 60 * 24 * 30  # 30 дней

if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(32)
    print(
        "[!] SECRET_KEY не задан — сгенерирован временный ключ. "
        "Сессии слетят после перезапуска. Задайте SECRET_KEY в окружении.",
        file=sys.stderr,
    )

if ADMIN_PASSWORD == DEFAULT_PASSWORD:
    print(
        "[!] ВНИМАНИЕ: используется пароль по умолчанию. "
        "Задайте переменную окружения ADMIN_PASSWORD!",
        file=sys.stderr,
    )

# ----------------------------------------------------------------------------
# База данных (SQLite, только параметризованные запросы)
# ----------------------------------------------------------------------------


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scripts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                slug        TEXT NOT NULL UNIQUE,
                name        TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
            """
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_slug(conn: sqlite3.Connection) -> str:
    while True:
        slug = secrets.token_urlsafe(8)
        row = conn.execute("SELECT 1 FROM scripts WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            return slug


def normalize_newlines(text: str) -> str:
    # textarea по спецификации HTML отправляет CRLF; bash спотыкается о \r
    return text.replace("\r\n", "\n").replace("\r", "\n")


def fmt_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return iso


# ----------------------------------------------------------------------------
# Сессии: HMAC-подписанная cookie вида "<timestamp>.<signature>"
# ----------------------------------------------------------------------------


def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session_cookie() -> str:
    ts = str(int(time.time()))
    return f"{ts}.{_sign(ts)}"


def is_authed(request: Request) -> bool:
    cookie = request.cookies.get(COOKIE_NAME, "")
    ts, _, sig = cookie.partition(".")
    if not ts or not sig:
        return False
    if not hmac.compare_digest(_sign(ts), sig):
        return False
    try:
        return time.time() - int(ts) < SESSION_TTL
    except ValueError:
        return False


def guard(request: Request) -> RedirectResponse | None:
    """Вернуть редирект на /login, если пользователь не авторизован."""
    if not is_authed(request):
        return RedirectResponse("/login", status_code=303)
    return None


# ----------------------------------------------------------------------------
# Вспомогательное
# ----------------------------------------------------------------------------


def external_base(request: Request) -> str:
    if BASE_URL:
        return BASE_URL
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


def raw_url(request: Request, slug: str) -> str:
    return f"{external_base(request)}/raw/{slug}"


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
.logo::after {
  content: "_"; color: var(--lime); margin-left: 2px;
  animation: blink 1.1s steps(1) infinite;
}
@keyframes blink { 50% { opacity: 0; } }
.header-actions { display: flex; gap: 10px; align-items: center; }
.header-actions form { display: inline; }

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

/* login — terminal window */
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
  textarea { min-height: 320px; }
}
"""

JS = """
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2200);
}
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text)
      .then(() => toast('Ссылка скопирована'))
      .catch(() => toast('Не удалось скопировать'));
  } else {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); toast('Ссылка скопирована'); }
    catch (e) { toast('Не удалось скопировать'); }
    document.body.removeChild(ta);
  }
}
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-copy]');
  if (btn) copyText(btn.dataset.copy);
});
"""


def page(title: str, body: str, *, authed: bool = False) -> HTMLResponse:
    header = ""
    if authed:
        header = """
<header>
  <div class="header-inner">
    <a class="logo" href="/">script<b>/</b>vault</a>
    <div class="header-actions">
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
<script>{JS}</script>
</body>
</html>"""
    return HTMLResponse(doc)


# ----------------------------------------------------------------------------
# Приложение и роуты
# ----------------------------------------------------------------------------

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
init_db()


@app.get("/login")
def login_form(request: Request, error: int = 0):
    if is_authed(request):
        return RedirectResponse("/", status_code=303)
    err_html = '<div class="error">Неверный пароль</div>' if error else ""
    body = f"""
<div class="login-wrap">
  <div class="login-card">
    <div class="login-bar"><i></i><i></i><i></i><span>auth · script/vault</span></div>
    <div class="login-body">
      <h1>script<span style="color:var(--lime)">/</span>vault</h1>
      <span class="muted">Защищённый доступ — введите пароль администратора</span>
      {err_html}
      <form method="post" action="/login">
        <label for="password">Пароль</label>
        <input type="password" id="password" name="password" required autofocus autocomplete="current-password">
        <div class="form-actions">
          <button class="btn btn-primary" type="submit">Войти →</button>
        </div>
      </form>
    </div>
  </div>
</div>"""
    return page("Вход", body)


@app.post("/login")
def login(password: str = Form(...)):
    if not hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
        return RedirectResponse("/login?error=1", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        COOKIE_NAME,
        make_session_cookie(),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@app.get("/")
def index(request: Request):
    if redir := guard(request):
        return redir
    with db() as conn:
        rows = conn.execute(
            "SELECT id, slug, name, updated_at FROM scripts ORDER BY updated_at DESC"
        ).fetchall()

    if not rows:
        body = """
<div class="page-head"><div><span class="kicker">репозиторий</span><h1>Мои скрипты</h1></div></div>
<div class="empty">
  <div class="glyph">~/scripts</div>
  <p>Пока ни одного скрипта. Создайте первый — и раздавайте его одной командой curl.</p>
  <a class="btn btn-primary" href="/new">+ Новый скрипт</a>
</div>"""
        return page("Скрипты", body, authed=True)

    cards = []
    for i, row in enumerate(rows):
        url = raw_url(request, row["slug"])
        esc_url = html.escape(url, quote=True)
        cards.append(f"""
<div class="card" style="animation-delay: {min(i * 60, 480)}ms">
  <div class="card-top">
    <span class="card-name">{html.escape(row["name"])}</span>
    <span class="muted">обновлён {html.escape(fmt_dt(row["updated_at"]))}</span>
  </div>
  <a class="card-url" href="{esc_url}" target="_blank" rel="noopener">{esc_url}</a>
  <div class="card-actions">
    <button class="btn btn-sm" type="button" data-copy="{esc_url}">Копировать ссылку</button>
    <a class="btn btn-sm" href="/scripts/{row["id"]}/edit">Изменить</a>
    <form method="post" action="/scripts/{row["id"]}/delete"
          onsubmit="return confirm('Удалить скрипт «{html.escape(row["name"], quote=True)}»? Это действие необратимо.')">
      <button class="btn btn-sm btn-danger" type="submit">Удалить</button>
    </form>
  </div>
</div>""")

    body = f"""
<div class="page-head">
  <div><span class="kicker">репозиторий</span><h1>Мои скрипты</h1></div>
  <span class="muted">в хранилище: {len(rows)}</span>
</div>
{"".join(cards)}"""
    return page("Скрипты", body, authed=True)


@app.get("/new")
def new_form(request: Request):
    if redir := guard(request):
        return redir
    body = """
<div class="page-head"><div><span class="kicker">создание</span><h1>Новый скрипт</h1></div></div>
<form class="editor" method="post" action="/scripts">
  <label for="name">Название</label>
  <input type="text" id="name" name="name" required maxlength="200" placeholder="deploy.sh" autofocus>
  <label for="content">Содержимое</label>
  <textarea id="content" name="content" spellcheck="false" placeholder="#!/usr/bin/env bash&#10;set -euo pipefail&#10;..."></textarea>
  <div class="form-actions">
    <button class="btn btn-primary" type="submit">Сохранить</button>
    <a class="btn" href="/">Отмена</a>
  </div>
</form>"""
    return page("Новый скрипт", body, authed=True)


@app.post("/scripts")
def create_script(request: Request, name: str = Form(...), content: str = Form("")):
    if redir := guard(request):
        return redir
    name = name.strip() or "Без названия"
    content = normalize_newlines(content)
    ts = now_iso()
    with db() as conn:
        slug = new_slug(conn)
        cur = conn.execute(
            "INSERT INTO scripts (slug, name, content, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (slug, name, content, ts, ts),
        )
        script_id = cur.lastrowid
    return RedirectResponse(f"/scripts/{script_id}/edit", status_code=303)


@app.get("/scripts/{script_id}/edit")
def edit_form(request: Request, script_id: int = Path(...)):
    if redir := guard(request):
        return redir
    with db() as conn:
        row = conn.execute(
            "SELECT id, slug, name, content, updated_at FROM scripts WHERE id = ?",
            (script_id,),
        ).fetchone()
    if row is None:
        return page("Не найдено", "<h1>Скрипт не найден</h1>", authed=True)

    url = raw_url(request, row["slug"])
    esc_url = html.escape(url, quote=True)
    curl_cmd = f"curl -fsSL {url} | bash"
    body = f"""
<div class="page-head"><div><span class="kicker">редактор</span><h1>Редактирование</h1></div></div>
<div class="share">
  <span class="kicker">прямая ссылка</span>
  <code>{esc_url}</code>
  <span class="kicker">запуск одной командой</span>
  <code>{html.escape(curl_cmd)}</code>
  <button class="btn btn-sm" type="button" data-copy="{esc_url}">Копировать ссылку</button>
  <button class="btn btn-sm" type="button" data-copy="{html.escape(curl_cmd, quote=True)}">Копировать curl</button>
</div>
<form class="editor" method="post" action="/scripts/{row["id"]}">
  <label for="name">Название</label>
  <input type="text" id="name" name="name" required maxlength="200" value="{html.escape(row["name"], quote=True)}">
  <label for="content">Содержимое</label>
  <textarea id="content" name="content" spellcheck="false">{html.escape(row["content"])}</textarea>
  <div class="form-actions">
    <button class="btn btn-primary" type="submit">Сохранить</button>
    <a class="btn" href="/">К списку</a>
  </div>
</form>"""
    return page(f"Редактирование — {row['name']}", body, authed=True)


@app.post("/scripts/{script_id}")
def update_script(
    request: Request,
    script_id: int = Path(...),
    name: str = Form(...),
    content: str = Form(""),
):
    if redir := guard(request):
        return redir
    name = name.strip() or "Без названия"
    content = normalize_newlines(content)
    with db() as conn:
        conn.execute(
            "UPDATE scripts SET name = ?, content = ?, updated_at = ? WHERE id = ?",
            (name, content, now_iso(), script_id),
        )
    return RedirectResponse(f"/scripts/{script_id}/edit", status_code=303)


@app.post("/scripts/{script_id}/delete")
def delete_script(request: Request, script_id: int = Path(...)):
    if redir := guard(request):
        return redir
    with db() as conn:
        conn.execute("DELETE FROM scripts WHERE id = ?", (script_id,))
    return RedirectResponse("/", status_code=303)


@app.get("/raw/{slug}")
def raw(slug: str):
    """Публичная выдача скрипта — без авторизации, защищено неугадываемым slug."""
    with db() as conn:
        row = conn.execute(
            "SELECT content FROM scripts WHERE slug = ?", (slug,)
        ).fetchone()
    if row is None:
        return PlainTextResponse("Not found\n", status_code=404)
    return PlainTextResponse(
        row["content"],
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
