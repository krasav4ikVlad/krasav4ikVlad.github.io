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
  --bg: #0a0d14;
  --surface: rgba(255, 255, 255, 0.035);
  --surface-hover: rgba(255, 255, 255, 0.06);
  --border: rgba(255, 255, 255, 0.09);
  --text: #e7ecf3;
  --muted: #8b95a7;
  --accent: #6c8cff;
  --accent-2: #38e1c2;
  --danger: #ff5c7a;
  --radius: 14px;
  --mono: ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { color-scheme: dark; }
body {
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
}
/* живой фон: два дрейфующих цветовых пятна */
body::before, body::after {
  content: "";
  position: fixed;
  z-index: -1;
  width: 60vmax; height: 60vmax;
  border-radius: 50%;
  filter: blur(90px);
  opacity: .14;
  animation: drift 26s ease-in-out infinite alternate;
}
body::before { background: var(--accent); top: -25vmax; left: -15vmax; }
body::after  { background: var(--accent-2); bottom: -30vmax; right: -15vmax; animation-delay: -13s; }
@keyframes drift {
  from { transform: translate(0, 0) scale(1); }
  to   { transform: translate(8vmax, 6vmax) scale(1.15); }
}

.container { max-width: 860px; margin: 0 auto; padding: 0 20px 64px; }

header {
  position: sticky; top: 0; z-index: 10;
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  background: rgba(10, 13, 20, 0.7);
  border-bottom: 1px solid var(--border);
  margin-bottom: 32px;
}
.header-inner {
  max-width: 860px; margin: 0 auto; padding: 14px 20px;
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
}
.logo {
  font-weight: 700; font-size: 18px; letter-spacing: .3px;
  text-decoration: none;
  background: linear-gradient(90deg, var(--accent), var(--accent-2));
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent; color: transparent;
}
.header-actions { display: flex; gap: 10px; align-items: center; }

h1 { font-size: 26px; font-weight: 700; }
.page-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; margin-bottom: 24px;
  animation: rise .5s ease both;
}
.muted { color: var(--muted); font-size: 14px; }

/* кнопки */
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 9px 16px; border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font: inherit; font-size: 14px; font-weight: 600;
  text-decoration: none; cursor: pointer;
  transition: transform .15s ease, box-shadow .15s ease, background .15s ease, border-color .15s ease;
}
.btn:hover { background: var(--surface-hover); transform: translateY(-1px); }
.btn:active { transform: translateY(0) scale(.98); }
.btn-primary {
  border: none;
  background: linear-gradient(135deg, var(--accent), #4f6df5);
  box-shadow: 0 4px 18px rgba(108, 140, 255, .35);
}
.btn-primary:hover {
  background: linear-gradient(135deg, #7d99ff, #5f7bff);
  box-shadow: 0 6px 24px rgba(108, 140, 255, .5);
}
.btn-danger { color: var(--danger); }
.btn-danger:hover { border-color: var(--danger); box-shadow: 0 0 14px rgba(255, 92, 122, .25); }
.btn-sm { padding: 6px 12px; font-size: 13px; }

/* карточки скриптов */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px 20px;
  margin-bottom: 14px;
  animation: rise .5s ease both;
  transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}
.card:hover {
  transform: translateY(-2px);
  border-color: rgba(108, 140, 255, .45);
  box-shadow: 0 10px 30px rgba(0, 0, 0, .35);
}
.card-top {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 10px; flex-wrap: wrap;
}
.card-name { font-size: 17px; font-weight: 700; word-break: break-word; }
.card-url {
  font-family: var(--mono); font-size: 13px; color: var(--accent-2);
  text-decoration: none; word-break: break-all;
  display: inline-block; margin: 8px 0 12px;
}
.card-url:hover { text-decoration: underline; }
.card-actions { display: flex; gap: 8px; flex-wrap: wrap; }

/* формы */
form.editor { animation: rise .5s ease both; }
label { display: block; font-size: 13px; font-weight: 600; color: var(--muted); margin: 18px 0 6px; }
input[type=text], input[type=password], textarea {
  width: 100%;
  padding: 11px 14px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: rgba(0, 0, 0, .25);
  color: var(--text);
  font: inherit;
  transition: border-color .15s ease, box-shadow .15s ease;
}
textarea {
  font-family: var(--mono); font-size: 13.5px; line-height: 1.6;
  min-height: 380px; resize: vertical;
  white-space: pre; overflow-wrap: normal; overflow-x: auto;
  tab-size: 4;
}
input:focus, textarea:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(108, 140, 255, .22);
}
.form-actions { display: flex; gap: 10px; margin-top: 22px; flex-wrap: wrap; }

/* блок ссылки на странице редактирования */
.share {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 18px;
  margin-bottom: 24px;
  animation: rise .5s ease both;
}
.share code {
  display: block;
  font-family: var(--mono); font-size: 13px;
  background: rgba(0, 0, 0, .35);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin: 8px 0;
  word-break: break-all;
  color: var(--accent-2);
}

/* заглушка пустого списка */
.empty {
  text-align: center; padding: 70px 20px;
  border: 1px dashed var(--border); border-radius: var(--radius);
  animation: rise .5s ease both;
}
.empty .glyph { font-size: 44px; animation: float 3s ease-in-out infinite; }
.empty p { color: var(--muted); margin: 12px 0 20px; }
@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-8px); }
}

/* логин */
.login-wrap { min-height: 80vh; display: flex; align-items: center; justify-content: center; }
.login-card {
  width: 100%; max-width: 380px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 34px 30px;
  animation: rise .6s ease both;
  box-shadow: 0 20px 60px rgba(0, 0, 0, .45);
}
.login-card h1 { text-align: center; margin-bottom: 4px; }
.login-card .muted { text-align: center; display: block; }
.error {
  background: rgba(255, 92, 122, .12);
  border: 1px solid rgba(255, 92, 122, .4);
  color: var(--danger);
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 14px;
  margin-top: 16px;
  animation: shake .4s ease;
}
@keyframes shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-6px); }
  75% { transform: translateX(6px); }
}

/* toast */
#toast {
  position: fixed; left: 50%; bottom: 28px; z-index: 100;
  transform: translate(-50%, 80px);
  background: #141a26;
  border: 1px solid var(--accent);
  color: var(--text);
  padding: 11px 22px;
  border-radius: 999px;
  font-size: 14px; font-weight: 600;
  box-shadow: 0 8px 30px rgba(0, 0, 0, .5), 0 0 18px rgba(108, 140, 255, .25);
  opacity: 0;
  pointer-events: none;
  transition: transform .3s cubic-bezier(.2, .9, .3, 1.3), opacity .25s ease;
}
#toast.show { transform: translate(-50%, 0); opacity: 1; }

@keyframes rise {
  from { opacity: 0; transform: translateY(14px); }
  to   { opacity: 1; transform: translateY(0); }
}

@media (max-width: 560px) {
  body { font-size: 15px; }
  .container { padding: 0 14px 48px; }
  .card { padding: 15px 16px; }
  .card-actions .btn { flex: 1; justify-content: center; }
  .form-actions .btn { flex: 1; justify-content: center; }
  textarea { min-height: 300px; }
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
    <a class="logo" href="/">⚡ Script Vault</a>
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
  <title>{html.escape(title)} — Script Vault</title>
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
    <h1>⚡ Script Vault</h1>
    <span class="muted">Вход для администратора</span>
    {err_html}
    <form method="post" action="/login">
      <label for="password">Пароль</label>
      <input type="password" id="password" name="password" required autofocus autocomplete="current-password">
      <div class="form-actions">
        <button class="btn btn-primary" type="submit">Войти</button>
      </div>
    </form>
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
<div class="page-head"><h1>Мои скрипты</h1></div>
<div class="empty">
  <div class="glyph">📜</div>
  <p>Пока ни одного скрипта. Создайте первый — и делитесь им одной командой curl.</p>
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
  <h1>Мои скрипты</h1>
  <span class="muted">всего: {len(rows)}</span>
</div>
{"".join(cards)}"""
    return page("Скрипты", body, authed=True)


@app.get("/new")
def new_form(request: Request):
    if redir := guard(request):
        return redir
    body = """
<div class="page-head"><h1>Новый скрипт</h1></div>
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
<div class="page-head"><h1>Редактирование</h1></div>
<div class="share">
  <span class="muted">Прямая ссылка</span>
  <code>{esc_url}</code>
  <span class="muted">Запуск одной командой</span>
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
    uvicorn.run(app, host="0.0.0.0", port=PORT)
