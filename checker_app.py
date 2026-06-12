#!/usr/bin/env python3
"""
nodewiki VPN Checker — проверка доступности серверов из VLESS-ссылок и JSON-конфигов.

Принимает share-ссылки (vless/vmess/trojan/ss/hysteria/tuic), URL ПОДПИСКИ
(http(s)-ссылка, отдающая список серверов — plain/base64/JSON) или СОДЕРЖИМОЕ
JSON-конфига (xray/v2ray), вытаскивает все эндпоинты (host:port) и проверяет
по каждому:
  • ICMP   — ping хоста
  • TCP    — соединение на host:port + латенси
  • GET    — HTTP(S) GET на host:port
  • POST   — HTTP(S) POST на host:port

Единый вход (SSO): сессия читается из cookie .nodewiki.info — тот же SECRET_KEY
и та же MongoDB, что у хаба и Script Vault. Вход живёт на хабе.

Токены: на каждого пользователя — token-bucket (баланс + дозаправка со временем),
1 токен за проверяемый эндпоинт. Очередь: in-process FIFO + пул воркеров с
ограниченной параллельностью — наплыв пользователей тестируется по очереди.

Отдельный сервер (РФ). Переменные окружения:
  TOKEN_DB        MongoDB (та же база RS_2, что у остальных сервисов)
  SECRET_KEY      тот же секрет, что у хаба (иначе SSO не сработает)
  COOKIE_DOMAIN   .nodewiki.info
  HUB_URL         https://nodewiki.info  (куда отправлять на вход)
  BASE_URL        https://checker.nodewiki.info
  HOST, PORT      127.0.0.1 : 8002
  CHECKER_WORKERS         число параллельных воркеров очереди (по умолчанию 2)
  CHECKER_TOKEN_CAP       потолок токенов на пользователя (по умолчанию 300)
  CHECKER_REFILL_SECONDS  секунд на дозаправку 1 токена (по умолчанию 30)
  CHECKER_ALLOW_PRIVATE   1 — разрешить проверять приватные диапазоны (по умолч. 0)
  CHECKER_MAX_TARGETS     максимум эндпоинтов на заявку (по умолчанию 500)
  CHECKER_AGENT_QUEUE_TIMEOUT  сек ожидания зонда на длинной очереди (по умолч. 1800)

Запуск:  TOKEN_DB=... SECRET_KEY=... COOKIE_DOMAIN=.nodewiki.info python checker_app.py
"""

import asyncio
import base64
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import socket
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, parse_qs, quote as urlquote

import httpx
import uvicorn
from fastapi import FastAPI, Form, Path, Request
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from bson.errors import InvalidId

# ----------------------------------------------------------------------------
# Конфигурация
# ----------------------------------------------------------------------------

TOKEN_DB = os.environ.get("TOKEN_DB", "mongodb://localhost:27017")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
COOKIE_DOMAIN = os.environ.get("COOKIE_DOMAIN", "")
HUB_URL = os.environ.get("HUB_URL", "https://nodewiki.info").rstrip("/")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8002"))

CHECKER_WORKERS = int(os.environ.get("CHECKER_WORKERS", "2"))
TOKEN_CAP = int(os.environ.get("CHECKER_TOKEN_CAP", "300"))
REFILL_SECONDS = float(os.environ.get("CHECKER_REFILL_SECONDS", "30"))
ALLOW_PRIVATE = os.environ.get("CHECKER_ALLOW_PRIVATE", "0") == "1"

MAX_TARGETS = int(os.environ.get("CHECKER_MAX_TARGETS", "500"))  # эндпоинтов на заявку
MAX_QUEUE = 500           # глубина очереди

# подписки (URL со списком share-ссылок)
SUB_TIMEOUT = 15.0
SUB_MAX_BYTES = 3_000_000
SUB_MAX_URLS = 5          # URL подписок за одну заявку
TCP_TIMEOUT = 6.0
HTTP_TIMEOUT = 8.0
PING_TIMEOUT = 4
PER_JOB_CONCURRENCY = 8   # сколько проверок параллельно внутри одной заявки

# глубокая проверка через туннель (xray)
XRAY_BIN = os.environ.get("XRAY_BIN", "xray")
XRAY_CONCURRENCY = int(os.environ.get("CHECKER_XRAY_CONCURRENCY", "1"))  # одновременных xray
DEEP_COST = int(os.environ.get("CHECKER_DEEP_COST", "5"))   # токенов за эндпоинт в глубоком режиме
XRAY_START_TIMEOUT = 10.0   # ждём готовности socks
TUNNEL_PROBE_TIMEOUT = 14.0 # запрос гео через туннель
TUNNEL_PROBE_URL = os.environ.get(
    "CHECKER_PROBE_URL", "http://ip-api.com/json/?fields=status,country,countryCode,query"
)
# замер скорости через туннель — ловит ТСПУ-резку «в 0»
TUNNEL_SPEED_URL = os.environ.get(
    "CHECKER_SPEED_URL", "https://speed.cloudflare.com/__down?bytes=20000000"
)
SPEED_WINDOW = 10.0          # сколько секунд качаем (чтобы throttle успел сработать)
SPEED_MAX_BYTES = 25_000_000 # либо до стольки байт
SPEED_MIN_MBPS = 0.5         # установившаяся ниже — «режется»
SPEED_SLOW_MBPS = 10.0       # ниже — «медленно/подозрительно» (рабочая нода даёт десятки Mbps)
TUNNEL_DO_SPEED = os.environ.get("CHECKER_TUNNEL_SPEED", "0") == "1"  # замер скорости (по умолч. выкл)

# доступность иностранных сервисов через туннель — главный сигнал «нода живая»
# формат env: "Имя=url, Имя=url, ..."; по умолчанию — популярные сервисы
def _parse_services(raw: str) -> list[tuple[str, str]]:
    out = []
    for part in raw.split(","):
        if "=" in part:
            name, url = part.split("=", 1)
            name, url = name.strip(), url.strip()
            if name and url:
                out.append((name, url))
    return out

SERVICE_CHECKS = _parse_services(os.environ.get("CHECKER_SERVICES", "")) or [
    ("YouTube",   "https://www.youtube.com/generate_204"),
    ("ChatGPT",   "https://chatgpt.com/cdn-cgi/trace"),
    ("Telegram",  "https://web.telegram.org/"),
    ("Instagram", "https://www.instagram.com/"),
    ("Google",    "https://www.gstatic.com/generate_204"),
]
SERVICE_TIMEOUT = 10.0
XRAY_PROTOS = {"vless", "vmess", "trojan", "shadowsocks"}  # что умеет xray-core
JOBS_TTL_DAYS = 7  # автоудаление заявок (в них лежат пользовательские конфиги)
xray_sem: "asyncio.Semaphore" = None  # type: ignore

# residential-зонд: агент на домашнем/мобильном канале сам опрашивает чекер
# и гоняет туннель-тест «как конечный пользователь».
AGENT_TOKEN = os.environ.get("CHECKER_AGENT_TOKEN", "")
AGENT_TIMEOUT = int(os.environ.get("CHECKER_AGENT_TIMEOUT", "120"))  # сек до фолбэка
# подписка на десятки серверов: зонд жуёт очередь последовательно, поэтому пока
# он на связи — queued-задачи живут дольше (но не дольше этого потолка)
AGENT_QUEUE_TIMEOUT = int(os.environ.get("CHECKER_AGENT_QUEUE_TIMEOUT", "1800"))
LOCAL_FALLBACK = os.environ.get("CHECKER_LOCAL_FALLBACK", "1") == "1"
last_agent_poll = 0.0  # время последнего опроса любым зондом (зонд «на связи»)

COOKIE_NAME = "session"
SESSION_TTL = 60 * 60 * 24 * 30

if not SECRET_KEY:
    SECRET_KEY = "dev-only-secret"
    print(
        "[!] SECRET_KEY не задан. Для SSO он обязан совпадать с SECRET_KEY хаба!",
        file=sys.stderr,
    )

cluster = AsyncIOMotorClient(TOKEN_DB)
db = cluster["RS_2"]
users_col = db["users"]
jobs_col = db["checker_jobs"]
tunnel_tasks = db["checker_tunnel_tasks"]  # задачи туннель-теста для residential-зондов

# очередь задач (id) + воркеры; создаётся в lifespan
job_queue: "asyncio.Queue[str]" = None  # type: ignore


# ----------------------------------------------------------------------------
# Сессии (формат идентичен хабу/scripts — это и есть SSO)
# ----------------------------------------------------------------------------


def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


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


SSO_TRY_COOKIE = "sso_try"

SSO_ERROR_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Сессия не принята — VPN Checker</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Syne:wght@800&display=swap">
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b0b0c;color:#ece9e0;font:15px/1.6 "JetBrains Mono",monospace;padding:22px}}
.card{{max-width:520px;background:#121214;border:1px solid #36363f;border-left:3px solid #ff5d4e;
border-radius:4px;padding:30px 32px;box-shadow:10px 10px 0 #000}}
h1{{font-family:"Syne",sans-serif;font-size:24px;margin:0 0 14px}}
p{{color:#817e75;margin:0 0 12px}} b{{color:#ece9e0}}
code{{background:#0d0d0f;border:1px solid #36363f;border-radius:2px;padding:1px 6px;color:#c6f23f;font-size:13px}}
a{{display:inline-block;margin-top:16px;background:#c6f23f;color:#11130a;text-decoration:none;
font-weight:700;padding:10px 16px;border-radius:2px;box-shadow:4px 4px 0 #000}}
</style></head><body><div class="card">
<h1>Сессия не принята</h1>
<p>Ты авторизован на хабе, но этот сервис не смог проверить твою сессию — поэтому вход
зациклился. Почти всегда причина в конфигурации <b>этого</b> сервера:</p>
<p>• <code>SECRET_KEY</code> не совпадает с основным сервером (подпись cookie не проходит), либо<br>
• <code>TOKEN_DB</code> указывает на другую базу (пользователь не находится).</p>
<p>Проверь <code>/opt/nodewiki-checker/nodewiki-checker.env</code> — оба значения должны быть
идентичны тем, что в <code>/opt/script-vault/script-vault.env</code> на основном сервере,
затем <code>systemctl restart nodewiki-checker</code>.</p>
<a href="/">Попробовать снова</a>
</div></body></html>"""


def login_redirect(request: Request) -> HTMLResponse | RedirectResponse:
    """Неавторизованный -> на вход хаба. Защита от петли: если мы уже один раз
    отправляли на вход (cookie sso_try), а сессии всё ещё нет — показываем
    понятную ошибку вместо бесконечного редиректа."""
    if request.cookies.get(SSO_TRY_COOKIE):
        resp = HTMLResponse(SSO_ERROR_HTML, status_code=200)
        resp.delete_cookie(SSO_TRY_COOKIE, path="/", domain=None)
        return resp
    nxt = urlquote(str(request.url), safe="")
    resp = RedirectResponse(f"{HUB_URL}/login?next={nxt}", status_code=303)
    resp.set_cookie(
        SSO_TRY_COOKIE, "1", max_age=30, path="/", samesite="lax",
        httponly=True, secure=BASE_URL.startswith("https"),
    )
    return resp


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fmt_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M:%S")
    except (ValueError, TypeError):
        return str(iso)


# ----------------------------------------------------------------------------
# Токены — token-bucket на пользователя (поля checker_tokens, checker_refill_at)
# ----------------------------------------------------------------------------


async def take_tokens(user: dict, cost: int) -> tuple[bool, int]:
    """Дозаправить по времени, затем списать cost. Возвращает (успех, остаток)."""
    now = time.time()
    tokens = float(user.get("checker_tokens", TOKEN_CAP))
    last = float(user.get("checker_refill_at", now))
    if REFILL_SECONDS > 0:
        tokens = min(TOKEN_CAP, tokens + (now - last) / REFILL_SECONDS)
    if tokens >= cost:
        tokens -= cost
        ok = True
    else:
        ok = False
    await users_col.update_one(
        {"_id": user["_id"]},
        {"$set": {"checker_tokens": tokens, "checker_refill_at": now}},
    )
    return ok, int(tokens)


async def token_balance(user: dict) -> tuple[int, int]:
    """Текущий баланс с учётом дозаправки (не списывая). (баланс, потолок)."""
    now = time.time()
    tokens = float(user.get("checker_tokens", TOKEN_CAP))
    last = float(user.get("checker_refill_at", now))
    if REFILL_SECONDS > 0:
        tokens = min(TOKEN_CAP, tokens + (now - last) / REFILL_SECONDS)
    return int(tokens), TOKEN_CAP


# ----------------------------------------------------------------------------
# Парсинг конфигов -> список целей [{host, port, tls, label}]
# ----------------------------------------------------------------------------


def _tls_from(security: str | None, port: int) -> bool:
    if security and security.lower() in ("tls", "reality", "xtls"):
        return True
    return port in (443, 8443)


# протоколы/транспорты, работающие поверх UDP/QUIC — TCP-проверка к ним неприменима
UDP_PROTOCOLS = {"hysteria", "hysteria2", "hy2", "tuic", "wireguard", "wg", "juicity"}
UDP_NETWORKS = {"quic", "hysteria", "kcp", "mkcp"}


def is_udp(proto: str | None, network: str | None) -> bool:
    return ((proto or "").lower() in UDP_PROTOCOLS) or (
        (network or "").lower() in UDP_NETWORKS
    )


def parse_vless(link: str) -> list[dict]:
    u = urlsplit(link.strip())
    if not u.hostname or not u.port:
        return []
    q = parse_qs(u.query)
    security = (q.get("security") or [None])[0]
    net = (q.get("type") or [None])[0]  # ws/grpc/tcp/xhttp/…
    label = ""
    if u.fragment:
        from urllib.parse import unquote

        label = unquote(u.fragment)[:80]
    return [{
        "host": u.hostname,
        "port": int(u.port),
        "tls": _tls_from(security, int(u.port)),
        "proto": "vless",
        "net": (net or "tcp"),
        "udp": is_udp("vless", net),
        "label": label or u.hostname,
    }]


def _walk_json_targets(obj, ctx=("", "", ""), out=None):
    """Рекурсивно ищем (host, port) в любых формах xray/v2ray-конфигов,
    протягивая контекст (protocol, network, security) с уровня outbound вниз."""
    if out is None:
        out = []
    proto, net, sec = ctx
    if isinstance(obj, dict):
        if isinstance(obj.get("protocol"), str):
            proto = obj["protocol"]
        ss = obj.get("streamSettings")
        if isinstance(ss, dict):
            if isinstance(ss.get("network"), str):
                net = ss["network"]
            if isinstance(ss.get("security"), str):
                sec = ss["security"]
        if isinstance(obj.get("security"), str):
            sec = obj["security"]
        host = obj.get("address") or obj.get("add") or obj.get("server")
        port = obj.get("port")
        if isinstance(host, str) and host and port not in (None, ""):
            try:
                p = int(port)
                label = obj.get("ps") or obj.get("name") or obj.get("tag") or host
                out.append({
                    "host": host,
                    "port": p,
                    "tls": _tls_from(sec, p),
                    "proto": proto or "?",
                    "net": net or "tcp",
                    "udp": is_udp(proto, net),
                    "label": str(label)[:80],
                })
            except (ValueError, TypeError):
                pass
        new_ctx = (proto, net, sec)
        for v in obj.values():
            _walk_json_targets(v, new_ctx, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_json_targets(v, ctx, out)
    return out


# share-ссылки, которые понимаем построчно (в т.ч. содержимое подписок)
SHARE_SCHEMES = ("vless://", "vmess://", "trojan://", "ss://",
                 "hysteria2://", "hy2://", "hysteria://", "tuic://")


def _vmess_obj(link: str) -> dict | None:
    """vmess:// — base64(JSON-объект с add/port/id/net/tls/ps)."""
    try:
        payload = link[len("vmess://"):].strip().split("#")[0]
        payload += "=" * (-len(payload) % 4)
        obj = json.loads(base64.b64decode(payload).decode("utf-8", "replace"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def parse_ss(link: str) -> dict | None:
    """ss:// — SIP002 (base64(method:pass)@host:port) или legacy base64 целиком."""
    from urllib.parse import unquote
    raw = link.strip()[len("ss://"):]
    frag = ""
    if "#" in raw:
        raw, frag = raw.split("#", 1)
    label = unquote(frag)[:80]
    method = password = host = None
    port = 0
    if "@" in raw:
        userinfo, hostpart = raw.rsplit("@", 1)
        hostpart = hostpart.split("?")[0].split("/")[0]
        if ":" not in hostpart:
            return None
        host, p = hostpart.rsplit(":", 1)
        ui = unquote(userinfo)
        try:
            dec = base64.urlsafe_b64decode(ui + "=" * (-len(ui) % 4)).decode("utf-8")
            if ":" in dec:
                ui = dec
        except Exception:
            pass
        if ":" in ui:
            method, password = ui.split(":", 1)
    else:
        try:
            dec = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        except Exception:
            return None
        if "@" not in dec:
            return None
        creds, hostpart = dec.rsplit("@", 1)
        if ":" not in creds or ":" not in hostpart:
            return None
        method, password = creds.split(":", 1)
        host, p = hostpart.rsplit(":", 1)
    try:
        port = int(p)
    except (ValueError, UnboundLocalError):
        return None
    t = {"host": host, "port": port, "tls": False, "proto": "shadowsocks",
         "net": "tcp", "udp": False, "label": label or host}
    if method and password:
        t["_xray"] = {
            "protocol": "shadowsocks",
            "settings": {"servers": [{"address": host, "port": port,
                                      "method": method, "password": password}]},
            "tag": "proxy",
        }
    return t


def _uri_target(link: str, proto: str) -> dict | None:
    """Цель из UDP/QUIC-схем (hysteria2/tuic/…) — туннель xray не поддержан."""
    from urllib.parse import unquote
    u = urlsplit(link.strip())
    if not u.hostname or not u.port:
        return None
    label = unquote(u.fragment)[:80] if u.fragment else ""
    return {"host": u.hostname, "port": int(u.port), "tls": True, "proto": proto,
            "net": "udp", "udp": True, "label": label or u.hostname}


def _parse_share_lines(text: str) -> list[dict]:
    """Построчный разбор share-ссылок (vless/vmess/trojan/ss/hysteria/tuic);
    где можем — сразу подвязываем xray-outbound для туннеля."""
    targets: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        low = line.lower()
        if low.startswith("vless://"):
            ts = parse_vless(line)
            if ts:
                ob = vless_outbound(line)
                if ob:
                    ts[0]["_xray"] = ob
                targets += ts
        elif low.startswith("vmess://"):
            obj = _vmess_obj(line)
            if not obj:
                continue
            host = obj.get("add") or obj.get("address") or ""
            try:
                port = int(obj.get("port"))
            except (ValueError, TypeError):
                continue
            if not host:
                continue
            net = str(obj.get("net") or "tcp")
            t = {"host": host, "port": port,
                 "tls": _tls_from(str(obj.get("tls") or ""), port),
                 "proto": "vmess", "net": net, "udp": is_udp("vmess", net),
                 "label": str(obj.get("ps") or host)[:80]}
            ob = vmess_outbound(obj)
            if ob:
                t["_xray"] = ob
            targets.append(t)
        elif low.startswith("trojan://"):
            u = urlsplit(line)
            if not u.hostname or not u.port:
                continue
            from urllib.parse import unquote
            q = parse_qs(u.query)
            sec = (q.get("security") or ["tls"])[0]
            net = (q.get("type") or ["tcp"])[0]
            label = unquote(u.fragment)[:80] if u.fragment else ""
            t = {"host": u.hostname, "port": int(u.port),
                 "tls": _tls_from(sec, int(u.port)), "proto": "trojan",
                 "net": net, "udp": is_udp("trojan", net),
                 "label": label or u.hostname}
            ob = trojan_outbound(line)
            if ob:
                t["_xray"] = ob
            targets.append(t)
        elif low.startswith("ss://"):
            t = parse_ss(line)
            if t:
                targets.append(t)
        elif low.startswith(("hysteria2://", "hy2://", "hysteria://", "tuic://")):
            proto = low.split("://", 1)[0]
            t = _uri_target(line, {"hy2": "hysteria2"}.get(proto, proto))
            if t:
                targets.append(t)
    return targets


def parse_config(text: str) -> tuple[list[dict], str]:
    """Вернуть (цели, ошибка). Поддержка share-ссылок (vless/vmess/trojan/ss/…),
    их списка (содержимое подписки) и JSON-конфига xray/v2ray."""
    text = text.strip()
    if not text:
        return [], "Пусто — вставьте ссылку (vless://…), URL подписки или JSON-конфиг."

    has_share = any(
        l.strip().lower().startswith(SHARE_SCHEMES) for l in text.splitlines()
    )
    if has_share:
        targets = _parse_share_lines(text)
        if not targets:
            return [], "Не удалось разобрать ссылки (нет host:port)."
    else:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            return [], f"Невалидный JSON: {e}"
        targets = _walk_json_targets(obj)
        if not targets:
            return [], "В JSON не найдено ни одного host:port (address/port)."
        # для туннеля: подвязываем xray-outbound к целям по host:port
        xray_map = _json_xray_outbounds(obj)
        for t in targets:
            ob = xray_map.get((t["host"], t["port"]))
            if ob is not None:
                t["_xray"] = ob

    # дедуп по host:port
    seen, uniq = set(), []
    for t in targets:
        key = (t["host"], t["port"])
        if key not in seen:
            seen.add(key)
            uniq.append(t)
    if len(uniq) > MAX_TARGETS:
        return uniq[:MAX_TARGETS], f"Эндпоинтов больше лимита, проверим первые {MAX_TARGETS}."
    return uniq, ""


# ---- подписки: URL -> текст со списком ссылок --------------------------------


def _decode_subscription(body: str) -> str:
    """Подписки часто отдают base64 от списка ссылок — пробуем раскодировать;
    если не похоже на base64 (или результат бессмысленный) — возвращаем как есть."""
    compact = "".join(body.split())
    if compact and re.fullmatch(r"[A-Za-z0-9+/_=-]+", compact):
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                dec = decoder(compact + "=" * (-len(compact) % 4)).decode("utf-8", "replace")
            except Exception:
                continue
            if "://" in dec or dec.lstrip().startswith(("{", "[")):
                return dec
    return body


async def fetch_subscription(url: str) -> tuple[str, str]:
    """Скачать подписку и привести к тексту со ссылками. Возвращает (текст, ошибка)."""
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return "", "URL подписки должен начинаться с http(s)://"
    _, err = resolve_safe(u.hostname)
    if err:
        return "", f"подписка: {err}"
    try:
        async with httpx.AsyncClient(
            timeout=SUB_TIMEOUT, follow_redirects=True, max_redirects=3, verify=False,
            headers={"User-Agent": "nodewiki-checker/1.0"},
        ) as cl:
            r = await cl.get(url)
    except Exception as e:
        return "", f"подписка не скачалась: {type(e).__name__}"
    if r.status_code >= 400:
        return "", f"подписка: HTTP {r.status_code}"
    if len(r.content) > SUB_MAX_BYTES:
        return "", "подписка слишком большая"
    return _decode_subscription(r.text), ""


# ---- генерация xray-конфига для прогона через туннель -----------------------


def _outbound_hostport(o: dict) -> tuple[str, int] | None:
    ts = _walk_json_targets(o)
    return (ts[0]["host"], ts[0]["port"]) if ts else None


def _json_xray_outbounds(obj) -> dict:
    """{(host,port): outbound} для xray-поддерживаемых outbounds из JSON-конфига."""
    res = {}
    candidates = []
    if isinstance(obj, dict):
        if isinstance(obj.get("outbounds"), list):
            candidates = obj["outbounds"]
        elif obj.get("protocol"):
            candidates = [obj]
    for o in candidates:
        if isinstance(o, dict) and o.get("protocol") in XRAY_PROTOS:
            hp = _outbound_hostport(o)
            if hp:
                oo = {k: v for k, v in o.items() if k != "tag"}
                oo["tag"] = "proxy"
                res[hp] = oo
    return res


def vless_outbound(link: str) -> dict | None:
    """Построить xray-outbound из VLESS share-ссылки."""
    u = urlsplit(link.strip())
    if not u.hostname or not u.port or not u.username:
        return None
    q = parse_qs(u.query)

    def g(k, d=None):
        return (q.get(k) or [d])[0]

    net = g("type", "tcp")
    sec = g("security", "none")
    stream: dict = {"network": net, "security": sec}
    if sec == "reality":
        stream["realitySettings"] = {
            "serverName": g("sni", ""), "fingerprint": g("fp", "chrome"),
            "publicKey": g("pbk", ""), "shortId": g("sid", ""), "spiderX": g("spx", "/"),
        }
    elif sec in ("tls", "xtls"):
        tls = {"serverName": g("sni", ""), "fingerprint": g("fp", "chrome")}
        if g("alpn"):
            from urllib.parse import unquote
            tls["alpn"] = unquote(g("alpn")).split(",")
        stream["tlsSettings"] = tls
    if net == "ws":
        from urllib.parse import unquote
        stream["wsSettings"] = {"path": unquote(g("path", "/")), "headers": {"Host": g("host", "")}}
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": g("serviceName", "")}
    elif net in ("xhttp", "splithttp"):
        from urllib.parse import unquote
        stream["xhttpSettings"] = {"path": unquote(g("path", "/")), "host": g("host", "")}
    elif net in ("http", "h2"):
        from urllib.parse import unquote
        stream["httpSettings"] = {"path": unquote(g("path", "/")),
                                  "host": [g("host")] if g("host") else []}
    user = {"id": u.username, "encryption": g("encryption", "none")}
    if g("flow"):
        user["flow"] = g("flow")
    return {
        "protocol": "vless",
        "settings": {"vnext": [{"address": u.hostname, "port": int(u.port), "users": [user]}]},
        "streamSettings": stream,
        "tag": "proxy",
    }


def vmess_outbound(obj: dict) -> dict | None:
    """Построить xray-outbound из vmess share-JSON (поля add/port/id/net/tls/…)."""
    host = obj.get("add") or obj.get("address")
    if not host or not obj.get("id"):
        return None
    try:
        port = int(obj.get("port"))
    except (ValueError, TypeError):
        return None
    net = str(obj.get("net") or "tcp")
    tls = str(obj.get("tls") or "").lower()
    stream: dict = {"network": net, "security": "tls" if tls in ("tls", "reality") else "none"}
    if tls == "tls":
        stream["tlsSettings"] = {"serverName": obj.get("sni") or obj.get("host") or ""}
    if net == "ws":
        stream["wsSettings"] = {"path": obj.get("path") or "/",
                                "headers": {"Host": obj.get("host") or ""}}
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": obj.get("path") or ""}
    try:
        aid = int(obj.get("aid") or 0)
    except (ValueError, TypeError):
        aid = 0
    user = {"id": obj["id"], "alterId": aid, "security": obj.get("scy") or "auto"}
    return {
        "protocol": "vmess",
        "settings": {"vnext": [{"address": host, "port": port, "users": [user]}]},
        "streamSettings": stream,
        "tag": "proxy",
    }


def trojan_outbound(link: str) -> dict | None:
    """Построить xray-outbound из trojan share-ссылки."""
    from urllib.parse import unquote
    u = urlsplit(link.strip())
    if not u.hostname or not u.port or not u.username:
        return None
    q = parse_qs(u.query)

    def g(k, d=None):
        return (q.get(k) or [d])[0]

    net = g("type", "tcp")
    sec = g("security", "tls")
    stream: dict = {"network": net, "security": sec}
    if sec == "reality":
        stream["realitySettings"] = {
            "serverName": g("sni", ""), "fingerprint": g("fp", "chrome"),
            "publicKey": g("pbk", ""), "shortId": g("sid", ""), "spiderX": g("spx", "/"),
        }
    elif sec in ("tls", "xtls"):
        stream["tlsSettings"] = {"serverName": g("sni", ""), "fingerprint": g("fp", "chrome")}
    if net == "ws":
        stream["wsSettings"] = {"path": unquote(g("path", "/")),
                                "headers": {"Host": g("host", "")}}
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": g("serviceName", "")}
    return {
        "protocol": "trojan",
        "settings": {"servers": [{"address": u.hostname, "port": int(u.port),
                                  "password": unquote(u.username)}]},
        "streamSettings": stream,
        "tag": "proxy",
    }


def tunnel_config(outbound: dict, socks_port: int) -> dict:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "listen": "127.0.0.1", "port": socks_port, "protocol": "socks",
            "settings": {"udp": True},
        }],
        # без routing => весь трафик идёт в первый (единственный) outbound = proxy
        "outbounds": [outbound],
    }


# ----------------------------------------------------------------------------
# SSRF-защита: не даём чекеру стучаться в самого себя / служебные адреса
# ----------------------------------------------------------------------------


def resolve_safe(host: str) -> tuple[list[str], str]:
    """Резолвим host -> список IP; запрещаем loopback/link-local/(private)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return [], "DNS не разрешается"
    ips = sorted({i[4][0] for i in infos})
    if not ips:
        return [], "DNS не разрешается"
    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return [], f"некорректный адрес {ip}"
        if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
            return [], "адрес запрещён (служебный диапазон)"
        if addr.is_private and not ALLOW_PRIVATE:
            return [], "адрес запрещён (приватный диапазон)"
    return ips, ""


# ----------------------------------------------------------------------------
# Проверки: ICMP / TCP / GET / POST
# ----------------------------------------------------------------------------


async def check_icmp(host: str) -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-n", "-c", "1", "-w", str(PING_TIMEOUT), host,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        if proc.returncode == 0:
            ms = ""
            for tok in out.decode("utf-8", "replace").split():
                if tok.startswith("time="):
                    ms = tok.split("=", 1)[1]
            return {"ok": True, "info": f"{ms} ms" if ms else "отвечает"}
        return {"ok": False, "info": "нет ответа"}
    except FileNotFoundError:
        return {"ok": False, "info": "ping недоступен на сервере"}
    except Exception as e:
        return {"ok": False, "info": str(e)[:60]}


async def check_tcp(host: str, port: int) -> dict:
    start = time.perf_counter()
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=TCP_TIMEOUT)
        ms = (time.perf_counter() - start) * 1000
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return {"ok": True, "info": f"{ms:.0f} ms"}
    except asyncio.TimeoutError:
        return {"ok": False, "info": "таймаут"}
    except (ConnectionRefusedError, OSError) as e:
        return {"ok": False, "info": getattr(e, "strerror", None) or "отказ"}
    except Exception as e:
        return {"ok": False, "info": str(e)[:60]}


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _wait_port(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            fut = asyncio.open_connection("127.0.0.1", port)
            _, writer = await asyncio.wait_for(fut, timeout=1.0)
            writer.close()
            return True
        except Exception:
            await asyncio.sleep(0.25)
    return False


async def _probe_service(proxy: str, name: str, url: str) -> dict:
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            proxy=proxy, timeout=SERVICE_TIMEOUT, verify=False, follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 nodewiki-checker"},
        ) as cl:
            r = await cl.get(url)
        ms = (time.perf_counter() - start) * 1000
        # 2xx/3xx = открывается; 4xx/5xx = заблокировано/недоступно
        return {"name": name, "ok": r.status_code < 400,
                "info": f"{r.status_code} · {ms:.0f} ms"}
    except (httpx.TimeoutException, httpx.ProxyError):
        return {"name": name, "ok": False, "info": "таймаут"}
    except Exception as e:
        return {"name": name, "ok": False, "info": type(e).__name__}


async def check_services(proxy: str) -> list[dict]:
    """Проверить доступность набора иностранных сервисов через туннель."""
    return await asyncio.gather(
        *(_probe_service(proxy, name, url) for name, url in SERVICE_CHECKS)
    )


async def measure_speed(proxy: str) -> str:
    """Опциональный замер установившейся скорости через туннель (Mbps)."""
    import statistics
    samples, total, start = [], 0, time.perf_counter()
    try:
        async with httpx.AsyncClient(proxy=proxy, verify=False,
                                     timeout=httpx.Timeout(10.0, read=SPEED_WINDOW + 2)) as cl:
            last_t, last_b = start, 0
            async with cl.stream("GET", TUNNEL_SPEED_URL) as resp:
                if resp.status_code < 400:
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        now = time.perf_counter()
                        if now - last_t >= 1.0:
                            samples.append((total - last_b) * 8 / (now - last_t) / 1e6)
                            last_t, last_b = now, total
                        if total >= SPEED_MAX_BYTES or now - start >= SPEED_WINDOW:
                            break
    except Exception:
        pass
    elapsed = max(time.perf_counter() - start, 0.001)
    avg = total * 8 / elapsed / 1e6
    if len(samples) >= 2:
        steady = statistics.median(samples[len(samples) // 2:])
    elif samples:
        steady = samples[-1]
    else:
        steady = avg
    return f"~{min(steady, avg):.0f} Mbps"


async def tunnel_check(outbound: dict) -> dict:
    """Поднять xray с этим outbound и реально сходить наружу через SOCKS,
    затем проверить, открываются ли иностранные сервисы через туннель."""
    import tempfile

    port = _free_port()
    cfg = tunnel_config(outbound, port)
    proc = None
    path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(cfg, f)
            path = f.name
        try:
            proc = await asyncio.create_subprocess_exec(
                XRAY_BIN, "run", "-c", path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return {"ok": False, "info": "xray не установлен на сервере"}

        if not await _wait_port(port, XRAY_START_TIMEOUT):
            return {"ok": False, "info": "xray не поднялся (конфиг?)"}

        proxy = f"socks5://127.0.0.1:{port}"
        # 1) маршрутизация + гео: реально ли есть выход в сеть
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=TUNNEL_PROBE_TIMEOUT, verify=False) as cl:
                r = await cl.get(TUNNEL_PROBE_URL)
                data = r.json()
        except (httpx.TimeoutException, httpx.ProxyError, httpx.ConnectError):
            return {"ok": False, "info": "нет выхода в сеть (таймаут — вероятно DPI/бан)"}
        except Exception as e:
            return {"ok": False, "info": f"туннель: {type(e).__name__}"}
        if data.get("status") != "success":
            return {"ok": False, "info": "туннель поднялся, но нет выхода в сеть"}
        cc = data.get("countryCode", "")
        country = data.get("country", "")
        exit_ip = data.get("query", "")
        geo = f"{country} ({cc}) · {exit_ip}"

        # 2) главное: открываются ли иностранные сервисы ЧЕРЕЗ туннель
        services = await check_services(proxy)
        ok_n = sum(1 for s in services if s["ok"])

        result = {"ok": ok_n > 0, "info": geo, "geo": geo,
                  "services": services, "ok_count": ok_n, "total": len(services)}
        if ok_n == 0:
            result["info"] = f"сервисы недоступны через ноду · выход {geo}"
        elif ok_n < len(services):
            result["warn"] = True
            result["info"] = f"часть сервисов недоступна ({ok_n}/{len(services)}) · {geo}"
        else:
            result["info"] = f"все сервисы открываются · {geo}"

        # 3) опционально — замер скорости (выкл по умолчанию)
        if TUNNEL_DO_SPEED:
            result["speed"] = await measure_speed(proxy)
        return result
    finally:
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


async def check_http(client: httpx.AsyncClient, method: str, host: str, port: int, tls: bool) -> dict:
    scheme = "https" if tls else "http"
    url = f"{scheme}://{host}:{port}/"
    start = time.perf_counter()
    try:
        resp = await client.request(method, url)
        ms = (time.perf_counter() - start) * 1000
        return {"ok": resp.status_code < 500, "info": f"HTTP {resp.status_code} · {ms:.0f} ms"}
    except httpx.TimeoutException:
        return {"ok": False, "info": "таймаут"}
    except httpx.HTTPError as e:
        return {"ok": False, "info": type(e).__name__}
    except Exception as e:
        return {"ok": False, "info": str(e)[:60]}


async def check_udp(host: str, port: int) -> dict:
    """Грубая UDP-проверка: «connected» сокет ловит ICMP port-unreachable.
    Закрыт -> ConnectionRefused; нет ответа -> открыт/фильтруется (норма для QUIC)."""
    loop = asyncio.get_event_loop()

    def probe():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(TCP_TIMEOUT)
        try:
            s.connect((host, port))
            s.send(b"\x00")
            try:
                s.recv(64)
                return {"ok": True, "info": "ответ получен"}
            except socket.timeout:
                return {"ok": True, "info": "открыт/фильтруется"}
            except ConnectionRefusedError:
                return {"ok": False, "info": "порт закрыт"}
        except Exception as e:
            return {"ok": False, "info": str(e)[:50]}
        finally:
            s.close()

    return await loop.run_in_executor(None, probe)


NA = {"na": True, "info": "не применимо (UDP/QUIC)"}


async def check_target(sem: asyncio.Semaphore, target: dict) -> dict:
    host, port, tls = target["host"], target["port"], target["tls"]
    result = {
        "host": host, "port": port, "tls": tls,
        "proto": target.get("proto", "?"), "net": target.get("net", "tcp"),
        "udp": target.get("udp", False), "label": target.get("label", host),
    }
    ips, err = resolve_safe(host)
    if err:
        result["blocked"] = err
        for k in ("icmp", "tcp", "get", "post"):
            result[k] = {"ok": False, "info": err}
        return result
    result["ip"] = ips[0]
    async with sem:
        if result["udp"]:
            # UDP-протокол: TCP/HTTP неприменимы, проверяем ICMP + UDP
            icmp, udp = await asyncio.gather(check_icmp(host), check_udp(host, port))
            result.update(icmp=icmp, tcp=udp, get=dict(NA), post=dict(NA), udp_check=True)
        else:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT, verify=False, follow_redirects=False,
                headers={"User-Agent": "nodewiki-checker/1.0"},
            ) as client:
                icmp, tcp, get, post = await asyncio.gather(
                    check_icmp(host),
                    check_tcp(host, port),
                    check_http(client, "GET", host, port, tls),
                    check_http(client, "POST", host, port, tls),
                )
            result.update(icmp=icmp, tcp=tcp, get=get, post=post)
    return result


# ----------------------------------------------------------------------------
# Очередь и воркеры
# ----------------------------------------------------------------------------


def tunnel_unsupported(target: dict) -> dict | None:
    """Если туннель к цели невозможен — вернуть готовый na-результат, иначе None."""
    if target.get("udp"):
        return {"na": True, "info": "нужен sing-box (xray не поддерживает)"}
    if not target.get("_xray"):
        return {"na": True, "info": "протокол не поддержан для туннеля"}
    return None


async def finalize_if_ready(job_id: str) -> None:
    """Если все туннель-задачи заявки завершены — перевести её в done."""
    try:
        oid = ObjectId(job_id)
    except (InvalidId, TypeError):
        return
    job = await jobs_col.find_one({"_id": oid})
    if not job or job.get("status") != "probing":
        return
    remaining = await tunnel_tasks.count_documents(
        {"job_id": job_id, "status": {"$ne": "done"}}
    )
    if remaining == 0:
        await jobs_col.update_one(
            {"_id": oid}, {"$set": {"status": "done", "finished_at": now_iso()}}
        )


async def run_job(job_id: str) -> None:
    doc = await jobs_col.find_one({"_id": ObjectId(job_id)})
    if not doc:
        return
    await jobs_col.update_one(
        {"_id": doc["_id"]}, {"$set": {"status": "running", "started_at": now_iso()}}
    )
    sem = asyncio.Semaphore(PER_JOB_CONCURRENCY)
    deep = bool(doc.get("deep"))
    targets = doc["targets"]
    try:
        results = await asyncio.gather(*(check_target(sem, t) for t in targets))

        if not deep:
            await jobs_col.update_one(
                {"_id": doc["_id"]},
                {"$set": {"status": "done", "results": results, "finished_at": now_iso()}},
            )
            return

        # глубокая проверка: ставим задачи-туннели для residential-зондов
        pending = []
        for i, (t, r) in enumerate(zip(targets, results)):
            na = tunnel_unsupported(t)
            if na is not None:
                r["tunnel"] = na
                continue
            if AGENT_TOKEN:
                r["tunnel"] = {"pending": True, "info": "ожидает residential-зонд…"}
                await tunnel_tasks.insert_one({
                    "job_id": job_id, "idx": i, "owner": doc["owner"],
                    "outbound": t["_xray"], "status": "queued",
                    "created_dt": datetime.now(timezone.utc),
                })
                pending.append(i)
            else:
                # зонды не настроены — меряем локально (из ДЦ), как раньше
                async with xray_sem:
                    res = await tunnel_check(t["_xray"])
                res["via"] = "дата-центр"
                r["tunnel"] = res

        status = "probing" if pending else "done"
        upd = {"status": status, "results": results}
        if status == "done":
            upd["finished_at"] = now_iso()
        await jobs_col.update_one({"_id": doc["_id"]}, {"$set": upd})
    except Exception as e:
        await jobs_col.update_one(
            {"_id": doc["_id"]},
            {"$set": {"status": "error", "error": str(e)[:200], "finished_at": now_iso()}},
        )


async def reaper() -> None:
    """Фолбэк: задачи, не взятые зондом за AGENT_TIMEOUT, добиваем сами (из ДЦ)
    либо помечаем «зонд офлайн». Чтобы заявки не висели вечно."""
    while True:
        await asyncio.sleep(20)
        try:
            now_dt = datetime.now(timezone.utc)
            stale = now_dt - timedelta(seconds=AGENT_TIMEOUT)
            hard = now_dt - timedelta(seconds=AGENT_QUEUE_TIMEOUT)
            agent_online = (time.time() - last_agent_poll) < AGENT_TIMEOUT
            # claimed: зонд взял задачу и пропал — добиваем по claimed_at.
            # queued: если зонд на связи, длинная очередь (подписка) — это норма,
            # ждём до AGENT_QUEUE_TIMEOUT; если зонда нет — фолбэк через AGENT_TIMEOUT.
            cond = [
                {"status": "claimed", "claimed_at": {"$lt": stale}},
                {"status": "queued",
                 "created_dt": {"$lt": hard if agent_online else stale}},
            ]
            async for task in tunnel_tasks.find({"$or": cond}):
                if LOCAL_FALLBACK:
                    async with xray_sem:
                        res = await tunnel_check(task["outbound"])
                    res["via"] = "дата-центр (зонд офлайн)"
                else:
                    res = {"na": True, "info": "residential-зонд офлайн"}
                await tunnel_tasks.update_one(
                    {"_id": task["_id"]}, {"$set": {"status": "done", "result": res}}
                )
                await jobs_col.update_one(
                    {"_id": ObjectId(task["job_id"])},
                    {"$set": {f"results.{task['idx']}.tunnel": res}},
                )
                await finalize_if_ready(task["job_id"])
        except Exception as e:
            print(f"[!] reaper: {e}", file=sys.stderr)


async def worker(name: int) -> None:
    while True:
        job_id = await job_queue.get()
        try:
            await run_job(job_id)
        except Exception as e:
            print(f"[!] worker {name} job {job_id}: {e}", file=sys.stderr)
        finally:
            job_queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global job_queue, xray_sem
    job_queue = asyncio.Queue(maxsize=MAX_QUEUE)
    xray_sem = asyncio.Semaphore(XRAY_CONCURRENCY)
    try:
        await jobs_col.create_index([("owner", 1), ("created_at", -1)])
        # TTL: заявки (с пользовательскими конфигами) автоудаляются
        await jobs_col.create_index("created_dt", expireAfterSeconds=JOBS_TTL_DAYS * 86400)
        await tunnel_tasks.create_index([("status", 1), ("created_dt", 1)])
        await tunnel_tasks.create_index("created_dt", expireAfterSeconds=JOBS_TTL_DAYS * 86400)
    except Exception as e:
        print(f"[!] индекс jobs: {e}", file=sys.stderr)
    # восстановление после рестарта: незавершённые -> обратно в очередь
    try:
        async for d in jobs_col.find({"status": {"$in": ["queued", "running"]}}):
            await jobs_col.update_one({"_id": d["_id"]}, {"$set": {"status": "queued"}})
            try:
                job_queue.put_nowait(str(d["_id"]))
            except asyncio.QueueFull:
                break
    except Exception as e:
        print(f"[!] восстановление очереди: {e}", file=sys.stderr)
    workers = [asyncio.create_task(worker(i)) for i in range(CHECKER_WORKERS)]
    bg = [asyncio.create_task(reaper())]
    yield
    for w in workers + bg:
        w.cancel()


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------

CSS = """
:root {
  --bg:#0b0b0c; --panel:#121214; --panel-2:#171719; --line:#25252b; --line-bright:#36363f;
  --ink:#ece9e0; --muted:#817e75; --lime:#c6f23f; --lime-soft:#d6ff52; --lime-dim:#9cbf33;
  --cyan:#38e1c2; --coral:#ff5d4e;
  --mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  --display:"Syne","JetBrains Mono",sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
html{color-scheme:dark;-webkit-text-size-adjust:100%}
::selection{background:var(--lime);color:#0c0d07}
body{font:15px/1.6 var(--mono);background:var(--bg);color:var(--ink);min-height:100vh;position:relative;overflow-x:hidden}
body::before{content:"";position:fixed;inset:0;z-index:-2;
  background:linear-gradient(var(--line) 1px,transparent 1px) 0 0/100% 64px,
  linear-gradient(90deg,var(--line) 1px,transparent 1px) 0 0/64px 100%,
  radial-gradient(120% 70% at 80% -10%,rgba(198,242,63,.13),transparent 56%),
  radial-gradient(120% 80% at -10% 110%,rgba(56,225,194,.08),transparent 55%),var(--bg);
  -webkit-mask-image:radial-gradient(150% 110% at 50% 0%,#000 50%,transparent 100%);
  mask-image:radial-gradient(150% 110% at 50% 0%,#000 50%,transparent 100%)}
body::after{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:repeating-linear-gradient(0deg,rgba(0,0,0,.16) 0 1px,transparent 1px 3px);opacity:.4;mix-blend-mode:multiply}
.container{max-width:920px;margin:0 auto;padding:0 22px 80px}
header{position:sticky;top:0;z-index:20;backdrop-filter:blur(11px) saturate(1.3);-webkit-backdrop-filter:blur(11px) saturate(1.3);
  background:rgba(11,11,12,.78);border-bottom:1px solid var(--line);margin-bottom:38px}
.header-inner{max-width:920px;margin:0 auto;padding:15px 22px;display:flex;align-items:center;justify-content:space-between;gap:14px}
.logo{font-family:var(--display);font-weight:800;font-size:19px;letter-spacing:-.6px;color:var(--ink);text-decoration:none;display:inline-flex;align-items:center}
.logo b{color:var(--lime)} .logo a{color:inherit;text-decoration:none;transition:color .15s}
.logo a.hub{color:var(--ink)} .logo a.hub:hover{color:var(--lime)} .logo a.app{color:var(--lime-dim)}
.logo::after{content:"_";color:var(--lime);margin-left:2px;animation:blink 1.1s steps(1) infinite}
@keyframes blink{50%{opacity:0}}
.header-actions{display:flex;gap:10px;align-items:center} .header-actions form{display:inline}
.tok{font-family:var(--mono);font-size:12px;color:var(--lime-dim);border:1px solid var(--line-bright);border-radius:2px;padding:6px 11px}
.tok b{color:var(--lime)}
h1{font-family:var(--display);font-size:31px;font-weight:800;letter-spacing:-1.2px;line-height:1.04}
.kicker{display:block;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:var(--lime-dim);margin-bottom:9px}
.kicker::before{content:"> "}
.page-head{display:flex;align-items:flex-end;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:26px;animation:rise .55s cubic-bezier(.2,.7,.2,1) both}
.muted{color:var(--muted);font-size:12.5px}
.btn{display:inline-flex;align-items:center;gap:7px;padding:10px 16px;border-radius:2px;border:1px solid var(--line-bright);background:var(--panel-2);color:var(--ink);font-family:var(--mono);font-size:13px;font-weight:600;letter-spacing:.3px;text-transform:lowercase;text-decoration:none;cursor:pointer;white-space:nowrap;transition:transform .12s,box-shadow .12s,border-color .15s,color .15s,background .15s}
.btn:hover{border-color:var(--lime);color:var(--lime);transform:translate(-2px,-2px);box-shadow:4px 4px 0 #000}
.btn:active{transform:translate(0,0);box-shadow:0 0 0 #000}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-primary{background:var(--lime);color:#11130a;border-color:var(--lime);font-weight:700;box-shadow:4px 4px 0 #000}
.btn-primary:hover{background:var(--lime-soft);color:#11130a;border-color:var(--lime-soft);box-shadow:6px 6px 0 #000}
.btn-sm{padding:7px 12px;font-size:12px}
label{display:block;font-size:11px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
label::before{content:"// ";color:var(--lime-dim)}
textarea{width:100%;padding:12px 14px;border-radius:2px;border:1px solid var(--line-bright);background:#0d0d0f;color:var(--ink);font-family:var(--mono);font-size:13px;line-height:1.6;min-height:200px;resize:vertical;white-space:pre;overflow-wrap:normal;overflow-x:auto;tab-size:4}
textarea:focus{outline:none;border-color:var(--lime);box-shadow:0 0 0 1px var(--lime),0 0 24px rgba(198,242,63,.13)}
textarea::placeholder{color:#494842}
.form-actions{display:flex;gap:12px;margin-top:18px;align-items:center;flex-wrap:wrap}
.editor{animation:rise .55s cubic-bezier(.2,.7,.2,1) both}
.error{background:rgba(255,93,78,.1);border:1px solid rgba(255,93,78,.45);border-left:2px solid var(--coral);color:var(--coral);border-radius:2px;padding:11px 14px;font-size:13px;margin:14px 0;animation:shake .4s ease}
.error::before{content:"\\2717  "}
.note{background:rgba(56,225,194,.08);border:1px solid rgba(56,225,194,.35);border-left:2px solid var(--cyan);color:var(--cyan);border-radius:2px;padding:10px 14px;font-size:12.5px;margin:14px 0}
@keyframes shake{0%,100%{transform:translateX(0)}20%{transform:translateX(-7px)}40%{transform:translateX(6px)}60%{transform:translateX(-4px)}80%{transform:translateX(3px)}}
@keyframes rise{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:translateY(0)}}
/* статусы заявки */
.status{display:inline-block;font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;border-radius:2px;padding:4px 9px}
.s-queued{color:var(--cyan);border:1px dashed rgba(56,225,194,.5)}
.s-running{color:#11130a;background:var(--cyan)}
.s-done{color:#11130a;background:var(--lime)}
.s-error{color:#fff;background:var(--coral)}
.spin{display:inline-block;width:11px;height:11px;border:2px solid rgba(198,242,63,.25);border-top-color:var(--lime);border-radius:50%;animation:sp .7s linear infinite;vertical-align:-1px;margin-right:6px}
@keyframes sp{to{transform:rotate(360deg)}}
/* карточка эндпоинта */
.ep{background:var(--panel);border:1px solid var(--line);border-left:2px solid var(--line-bright);border-radius:3px;padding:16px 18px;margin-bottom:14px;animation:rise .5s cubic-bezier(.2,.7,.2,1) both}
.ep-top{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.ep-name{font-family:var(--display);font-size:16px;font-weight:700}
.ep-addr{font-family:var(--mono);font-size:12.5px;color:var(--lime-dim);word-break:break-all}
.checks{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.chk{border:1px solid var(--line-bright);border-radius:2px;padding:9px 11px;background:#0d0d0f}
.chk-name{font-size:10.5px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted)}
.chk-val{margin-top:5px;font-size:13px;display:flex;align-items:center;gap:7px}
.chk-val::before{content:"";width:8px;height:8px;border-radius:50%;flex:none;background:var(--muted)}
.chk-ok .chk-val::before{background:var(--lime);box-shadow:0 0 8px rgba(198,242,63,.6)}
.chk-bad .chk-val::before{background:var(--coral)}
.chk-ok .chk-val{color:var(--ink)} .chk-bad .chk-val{color:var(--coral)}
.chk-na{opacity:.65} .chk-na .chk-val::before{background:var(--line-bright)} .chk-na .chk-val{color:var(--muted)}
/* строка туннеля (глубокая проверка) */
.tunnel{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:12px;padding:11px 13px;border-radius:2px;border:1px solid var(--line-bright);background:#0d0d0f}
.tunnel .tn-label{font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:var(--lime-dim)}
.tunnel .tn-head{font-weight:700;font-size:13px}
.tunnel .tn-info{color:var(--muted);font-size:12.5px}
.tn-ok{border-left:3px solid var(--lime)} .tn-ok .tn-head{color:var(--lime)}
.tn-bad{border-left:3px solid var(--coral)} .tn-bad .tn-head{color:var(--coral)}
.tn-na{border-left:3px solid var(--line-bright);opacity:.7} .tn-na .tn-head{color:var(--muted)}
.tn-warn{border-left:3px solid #ffb627} .tn-warn .tn-head{color:#ffb627}
.tn-pend{border-left:3px solid var(--cyan)} .tn-pend .tn-head{color:var(--cyan);display:inline-flex;align-items:center;gap:7px}
.tn-via{color:var(--lime-dim);font-size:11px}
.svc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-top:10px}
.svc{display:flex;flex-direction:column;gap:3px;border:1px solid var(--line-bright);border-radius:2px;padding:8px 10px;background:#0d0d0f}
.svc-name{font-size:13px;display:flex;align-items:center;gap:7px}
.svc-name::before{content:"";width:8px;height:8px;border-radius:50%;flex:none;background:var(--muted)}
.svc-ok .svc-name::before{background:var(--lime);box-shadow:0 0 8px rgba(198,242,63,.6)}
.svc-bad .svc-name::before{background:var(--coral)}
.svc-bad .svc-name{color:var(--coral)}
.svc-info{font-size:11px;color:var(--muted);padding-left:15px}
@media (max-width:560px){.svc-grid{grid-template-columns:1fr 1fr}}
/* история */
.job-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 14px;border:1px solid var(--line);border-radius:2px;background:var(--panel);margin-bottom:8px;font-size:13px;animation:rise .5s ease both}
.job-row a{color:var(--lime);text-decoration:none} .job-row a:hover{color:var(--lime-soft)}
.empty{text-align:center;padding:70px 20px;border:1px dashed var(--line-bright);border-radius:4px;background:repeating-linear-gradient(135deg,transparent 0 14px,rgba(255,255,255,.012) 14px 28px)}
.empty .glyph{font-family:var(--display);font-size:34px;font-weight:800;color:var(--lime)}
.empty p{color:var(--muted);margin:14px auto 0;max-width:440px}
@media (max-width:560px){.checks{grid-template-columns:1fr 1fr}}
"""


def page(title: str, body: str, *, user: dict, refresh: int = 0) -> HTMLResponse:
    bal, cap = 0, TOKEN_CAP  # заполняется вызывающим через user; покажем через data
    meta_refresh = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    doc = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex">
  <meta name="theme-color" content="#0b0b0c">
  {meta_refresh}
  <title>{html.escape(title)} — VPN Checker</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Syne:wght@700;800&display=swap">
  <style>{CSS}</style>
</head>
<body>
<header>
  <div class="header-inner">
    <span class="logo"><a class="hub" href="{HUB_URL}">nodewiki</a><b>/</b><a class="app" href="/">checker</a></span>
    <div class="header-actions">
      <span class="tok" id="tok">токены: <b>{user["_bal"]}</b>/{user["_cap"]}</span>
      <form method="post" action="{HUB_URL}/logout"><button class="btn btn-sm" type="submit">выйти</button></form>
    </div>
  </div>
</header>
<div class="container">
{body}
</div>
</body>
</html>"""
    return HTMLResponse(doc)


async def with_balance(user: dict) -> dict:
    bal, cap = await token_balance(user)
    user = dict(user)
    user["_bal"], user["_cap"] = bal, cap
    return user


def render_check(c: dict) -> str:
    if c.get("na"):
        cls = "chk-na"
    elif c.get("ok"):
        cls = "chk-ok"
    else:
        cls = "chk-bad"
    return f'<div class="chk {cls}"><div class="chk-name">{c["_n"]}</div><div class="chk-val">{html.escape(c.get("info",""))}</div></div>'


def render_results(results: list[dict]) -> str:
    cards = []
    for r in results:
        addr = f'{html.escape(r["host"])}:{r["port"]}'
        ipinfo = f' · {html.escape(r["ip"])}' if r.get("ip") else ""
        # метка протокола/транспорта: vless · reality · xhttp
        bits = [b for b in (r.get("proto"), ("tls" if r.get("tls") else None), r.get("net")) if b and b != "?"]
        proto = (" · " + " · ".join(html.escape(str(b)) for b in bits)) if bits else ""
        udp_note = ""
        if r.get("udp"):
            udp_note = '<div class="note" style="margin:0 0 12px">UDP/QUIC-протокол: вместо TCP проверяется UDP-доступность; HTTP-проверки неприменимы.</div>'
        if r.get("blocked"):
            checks = f'<div class="note" style="margin:0">{html.escape(r["blocked"])}</div>'
        else:
            items = []
            tcp_name = "UDP" if r.get("udp_check") else "TCP"
            for key, name in (("icmp", "ICMP"), ("tcp", tcp_name), ("get", "GET"), ("post", "POST")):
                c = dict(r.get(key, {"ok": False, "info": "—"}))
                c["_n"] = name
                items.append(render_check(c))
            checks = f'{udp_note}<div class="checks">{"".join(items)}</div>'
        # строка результата прогона через туннель (глубокая проверка)
        tunnel = ""
        if r.get("tunnel"):
            tw = r["tunnel"]
            if tw.get("pending"):
                cls, head = "tn-pend", '<span class="spin"></span>прогон через туннель'
            elif tw.get("na"):
                cls, head = "tn-na", "туннель"
            elif tw.get("warn"):
                cls, head = "tn-warn", "работает частично"
            elif tw.get("ok"):
                cls, head = "tn-ok", "сервисы открываются"
            else:
                cls, head = "tn-bad", "не работает"
            via = ""
            if tw.get("via"):
                via = f' <span class="tn-via">через {html.escape(tw["via"])}</span>'
            speed = f' · {html.escape(tw["speed"])}' if tw.get("speed") else ""
            svc_grid = ""
            if tw.get("services"):
                cells = []
                for s in tw["services"]:
                    scls = "svc-ok" if s.get("ok") else "svc-bad"
                    cells.append(
                        f'<div class="svc {scls}"><span class="svc-name">{html.escape(s["name"])}</span>'
                        f'<span class="svc-info">{html.escape(s.get("info",""))}</span></div>'
                    )
                svc_grid = f'<div class="svc-grid">{"".join(cells)}</div>'
            tunnel = f"""
  <div class="tunnel {cls}">
    <span class="tn-label">⟿ туннель</span>
    <span class="tn-head">{head}</span>
    <span class="tn-info">{html.escape(tw.get("info",""))}{speed}{via}</span>
  </div>{svc_grid}"""
        cards.append(f"""
<div class="ep">
  <div class="ep-top">
    <span class="ep-name">{html.escape(r.get("label", r["host"]))}</span>
    <span class="ep-addr">{addr}{ipinfo}{proto}</span>
  </div>
  {checks}{tunnel}
</div>""")
    return "".join(cards)


# ----------------------------------------------------------------------------
# Роуты
# ----------------------------------------------------------------------------

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    )
    return resp


@app.get("/health")
async def health():
    return PlainTextResponse("ok")


# ---- residential-зонды: outbound-поллинг агентами ---------------------------


def _agent_ok(request: Request) -> bool:
    if not AGENT_TOKEN:
        return False
    tok = request.headers.get("x-agent-token", "")
    return hmac.compare_digest(tok, AGENT_TOKEN)


@app.post("/agent/poll")
async def agent_poll(request: Request):
    global last_agent_poll
    if not _agent_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    last_agent_poll = time.time()
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=AGENT_TIMEOUT)
    # атомарно забираем одну незанятую (или зависшую у мёртвого зонда) задачу
    task = await tunnel_tasks.find_one_and_update(
        {"$or": [{"status": "queued"},
                 {"status": "claimed", "claimed_at": {"$lt": stale}}]},
        {"$set": {"status": "claimed", "claimed_at": now}},
        sort=[("created_dt", 1)],
        return_document=True,  # ReturnDocument.AFTER
    )
    if not task:
        return JSONResponse({"task": None})
    return JSONResponse({"task": {
        "task_id": str(task["_id"]),
        "outbound": task["outbound"],
        "probe_url": TUNNEL_PROBE_URL,
        "speed_url": TUNNEL_SPEED_URL,
        "params": {
            "start_timeout": XRAY_START_TIMEOUT, "probe_timeout": TUNNEL_PROBE_TIMEOUT,
            "services": [[n, u] for n, u in SERVICE_CHECKS],
        },
    }})


@app.post("/agent/result")
async def agent_result(request: Request):
    if not _agent_ok(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    task_id = body.get("task_id", "")
    result = body.get("result") or {}
    try:
        toid = ObjectId(task_id)
    except (InvalidId, TypeError):
        return JSONResponse({"error": "bad task_id"}, status_code=400)
    task = await tunnel_tasks.find_one({"_id": toid})
    if not task:
        return JSONResponse({"error": "not found"}, status_code=404)
    if task.get("status") == "done":
        return JSONResponse({"ok": True})
    result["via"] = "residential"
    await tunnel_tasks.update_one(
        {"_id": toid}, {"$set": {"status": "done", "result": result}}
    )
    await jobs_col.update_one(
        {"_id": ObjectId(task["job_id"])},
        {"$set": {f"results.{task['idx']}.tunnel": result}},
    )
    await finalize_if_ready(task["job_id"])
    return JSONResponse({"ok": True})


@app.get("/")
async def index(request: Request, error: str = ""):
    raw_user = await current_user(request)
    if not raw_user:
        return login_redirect(request)
    user = await with_balance(raw_user)
    recent = (
        await jobs_col.find({"owner": str(user["_id"])})
        .sort("_id", -1).to_list(length=10)
    )
    hist = ""
    if recent:
        rows = []
        for j in recent:
            st = j.get("status", "queued")
            n = len(j.get("targets", []))
            rows.append(
                f'<div class="job-row"><span><span class="status s-{st}">{st}</span> '
                f'&nbsp;{n} эндпоинт(ов) · {html.escape(fmt_dt(j.get("created_at","")))}</span>'
                f'<a href="/job/{j["_id"]}">открыть →</a></div>'
            )
        hist = f'<div class="page-head" style="margin-top:34px"><div><span class="kicker">последние проверки</span></div></div>{"".join(rows)}'

    err_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    qsize = job_queue.qsize() if job_queue else 0
    body = f"""
<div class="page-head">
  <div><span class="kicker">vpn checker</span><h1>Проверка конфигов</h1></div>
  <span class="muted">в очереди: {qsize}</span>
</div>
{err_html}
<form class="editor" method="post" action="/check">
  <label for="config">Ссылка, URL подписки или JSON-конфиг</label>
  <textarea id="config" name="config" required spellcheck="false"
    placeholder="vless://uuid@host:443?security=reality&sni=...#MyNode&#10;&#10;— или URL подписки (все серверы из неё) —&#10;https://provider.tld/abc123&#10;&#10;— или JSON-конфиг xray/v2ray —&#10;{{&quot;outbounds&quot;: [...]}}"></textarea>
  <p class="muted" style="margin-top:10px">Доступность: ICMP, TCP, HTTP GET и POST по каждому host:port (1 токен/эндпоинт).
  <b>Подписка</b>: вставьте http(s)-ссылку — скачаем её, достанем все серверы
  (vless/vmess/trojan/ss/hysteria) и проверим каждый отдельным блоком.
  <b>Глубокая проверка</b> дополнительно поднимает туннель (xray) и проверяет, открываются ли
  через ноду YouTube, ChatGPT, Telegram, Instagram — ловит случай «пинг есть, а интернета нет»
  ({DEEP_COST} токенов/эндпоинт).
  Дозаправка: +1 токен каждые {int(REFILL_SECONDS)} c (до {TOKEN_CAP}).</p>
  <div class="form-actions">
    <button class="btn btn-primary" type="submit" name="deep" value="">Проверить доступность</button>
    <button class="btn" type="submit" name="deep" value="1">Глубокая проверка (через туннель)</button>
  </div>
</form>
{hist}"""
    return page("Проверка", body, user=user)


@app.post("/check")
async def submit_check(request: Request, config: str = Form(...), deep: str = Form("")):
    raw_user = await current_user(request)
    if not raw_user:
        return login_redirect(request)

    is_deep = deep == "1"

    # подписка: одна или несколько http(s)-ссылок -> скачиваем и разбираем всё
    cfg_text = config.strip()
    lines = [l.strip() for l in cfg_text.splitlines() if l.strip()]
    from_sub = bool(lines) and all(re.fullmatch(r"https?://\S+", l) for l in lines)
    if from_sub:
        if len(lines) > SUB_MAX_URLS:
            return RedirectResponse(
                f"/?error={urlquote(f'Не больше {SUB_MAX_URLS} URL подписок за раз.')}",
                status_code=303,
            )
        bodies = []
        for url in lines:
            body, err = await fetch_subscription(url)
            if err:
                return RedirectResponse(f"/?error={urlquote(err)}", status_code=303)
            bodies.append(body)
        cfg_text = "\n".join(bodies)

    targets, msg = parse_config(cfg_text)
    if not targets:
        if from_sub:
            msg = f"В подписке не нашлось ссылок на серверы. {msg}"
        return RedirectResponse(f"/?error={urlquote(msg)}", status_code=303)
    if from_sub:
        msg = f"Из подписки получено серверов: {len(targets)}." + (f" {msg}" if msg else "")

    cost = len(targets) * (DEEP_COST if is_deep else 1)
    ok, _bal = await take_tokens(raw_user, cost)
    if not ok:
        return RedirectResponse(
            f"/?error={urlquote(f'Недостаточно токенов: нужно {cost}. Подождите дозаправки.')}",
            status_code=303,
        )

    if job_queue.full():
        return RedirectResponse(
            f"/?error={urlquote('Очередь переполнена, попробуйте позже.')}", status_code=303
        )

    doc = {
        "owner": str(raw_user["_id"]),
        "status": "queued",
        "targets": targets,
        "deep": is_deep,
        "note": msg,
        "created_at": now_iso(),
        "created_dt": datetime.now(timezone.utc),  # для TTL
    }
    res = await jobs_col.insert_one(doc)
    job_id = str(res.inserted_id)
    await job_queue.put(job_id)
    return RedirectResponse(f"/job/{job_id}", status_code=303)


@app.get("/job/{job_id}")
async def job_view(request: Request, job_id: str = Path(...)):
    raw_user = await current_user(request)
    if not raw_user:
        return login_redirect(request)
    try:
        oid = ObjectId(job_id)
    except (InvalidId, TypeError):
        return RedirectResponse("/", status_code=303)
    job = await jobs_col.find_one({"_id": oid, "owner": str(raw_user["_id"])})
    if not job:
        return RedirectResponse("/", status_code=303)
    user = await with_balance(raw_user)

    status = job.get("status", "queued")
    note = f'<div class="note">{html.escape(job["note"])}</div>' if job.get("note") else ""
    n = len(job.get("targets", []))

    if status in ("queued", "running"):
        # позиция в очереди (грубо): для queued
        pending = "" if status == "running" else " — ждёт своей очереди"
        body = f"""
<div class="page-head">
  <div><span class="kicker">проверка</span><h1>Заявка #{html.escape(job_id[-6:])}</h1></div>
  <span class="status s-{status}">{status}</span>
</div>
{note}
<div class="ep" style="text-align:center;padding:46px 18px">
  <div><span class="spin"></span> Проверяю {n} эндпоинт(ов){pending}…</div>
  <p class="muted" style="margin-top:12px">Страница обновится автоматически.</p>
</div>
<div class="form-actions"><a class="btn" href="/">← Новая проверка</a></div>"""
        return page("Проверка…", body, user=user, refresh=3)

    deep_label = " (глубокая)" if job.get("deep") else ""
    rerun = f"""
  <form method="post" action="/job/{job_id}/rerun" style="display:inline">
    <button class="btn btn-primary" type="submit">↻ Повторить{deep_label}</button>
  </form>"""

    if status == "error":
        body = f"""
<div class="page-head"><div><span class="kicker">проверка</span><h1>Заявка #{html.escape(job_id[-6:])}</h1></div>
<span class="status s-error">error</span></div>
<div class="error">{html.escape(job.get("error","неизвестная ошибка"))}</div>
<div class="form-actions">{rerun}<a class="btn" href="/">← Новая проверка</a></div>"""
        return page("Ошибка", body, user=user)

    results = job.get("results", [])

    # probing: доступность готова, ждём residential-зонды по туннелю — авторефреш
    if status == "probing":
        body = f"""
<div class="page-head">
  <div><span class="kicker">прогон через residential-зонд…</span><h1>Заявка #{html.escape(job_id[-6:])}</h1></div>
  <span class="status s-running">probing</span>
</div>
{note}
{render_results(results)}
<div class="form-actions"><a class="btn" href="/">← Новая проверка</a></div>"""
        return page("Прогон через зонд…", body, user=user, refresh=4)

    body = f"""
<div class="page-head">
  <div><span class="kicker">результат · {html.escape(fmt_dt(job.get("finished_at","")))}</span><h1>Заявка #{html.escape(job_id[-6:])}</h1></div>
  <span class="status s-done">done</span>
</div>
{note}
{render_results(results)}
<div class="form-actions">{rerun}<a class="btn" href="/">← Новая проверка</a></div>"""
    return page("Результат", body, user=user)


@app.post("/job/{job_id}/rerun")
async def job_rerun(request: Request, job_id: str = Path(...)):
    raw_user = await current_user(request)
    if not raw_user:
        return login_redirect(request)
    try:
        oid = ObjectId(job_id)
    except (InvalidId, TypeError):
        return RedirectResponse("/", status_code=303)
    job = await jobs_col.find_one({"_id": oid, "owner": str(raw_user["_id"])})
    if not job or not job.get("targets"):
        return RedirectResponse("/", status_code=303)

    is_deep = bool(job.get("deep"))
    cost = len(job["targets"]) * (DEEP_COST if is_deep else 1)
    ok, _bal = await take_tokens(raw_user, cost)
    if not ok:
        return RedirectResponse(
            f"/?error={urlquote(f'Недостаточно токенов: нужно {cost}. Подождите дозаправки.')}",
            status_code=303,
        )
    if job_queue.full():
        return RedirectResponse(
            f"/?error={urlquote('Очередь переполнена, попробуйте позже.')}", status_code=303
        )
    doc = {
        "owner": str(raw_user["_id"]),
        "status": "queued",
        "targets": job["targets"],
        "deep": is_deep,
        "note": job.get("note", ""),
        "created_at": now_iso(),
        "created_dt": datetime.now(timezone.utc),
    }
    res = await jobs_col.insert_one(doc)
    new_id = str(res.inserted_id)
    await job_queue.put(new_id)
    return RedirectResponse(f"/job/{new_id}", status_code=303)


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
