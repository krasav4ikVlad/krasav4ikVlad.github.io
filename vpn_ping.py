#!/usr/bin/env python3
"""
Парсер VPN-подписки + проверка статуса серверов (ICMP ping + TCP-чек).

Использование:
    python3 vpn_ping.py <subscription_url>
    python3 vpn_ping.py <subscription_url> --timeout 3 --workers 32 --json

Поддерживаемые схемы: ss://, vless://, vmess://, trojan://, hysteria2://, hy2://, tuic://
"""
from __future__ import annotations

import argparse
import base64
import binascii
import concurrent.futures as futures
import json
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict


DEFAULT_UA = "v2rayN/6.0"
SUPPORTED_SCHEMES = ("ss", "vless", "vmess", "trojan", "hysteria2", "hy2", "tuic")


@dataclass
class Server:
    scheme: str
    name: str
    host: str
    port: int
    raw: str


@dataclass
class CheckResult:
    name: str
    host: str
    port: int
    scheme: str
    icmp_ok: bool
    icmp_ms: float | None
    tcp_ok: bool
    tcp_ms: float | None
    status: str


def fetch_subscription(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace").strip()


def try_b64_decode(text: str) -> str:
    """Подписка обычно приходит в base64 одной строкой. Если не base64 — возвращаем как есть."""
    candidate = "".join(text.split())
    # base64 url-safe тоже встречается
    candidate = candidate.replace("-", "+").replace("_", "/")
    padding = "=" * (-len(candidate) % 4)
    try:
        decoded = base64.b64decode(candidate + padding, validate=False).decode(
            "utf-8", errors="replace"
        )
        if any(s + "://" in decoded for s in SUPPORTED_SCHEMES):
            return decoded
    except (binascii.Error, ValueError):
        pass
    return text


def parse_vmess(raw: str) -> Server | None:
    payload = raw[len("vmess://"):]
    payload = payload.replace("-", "+").replace("_", "/")
    padding = "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.b64decode(payload + padding).decode("utf-8", "replace"))
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return None
    host = str(data.get("add") or "").strip()
    port = int(data.get("port") or 0)
    name = str(data.get("ps") or host)
    if not host or not port:
        return None
    return Server("vmess", name, host, port, raw)


def parse_uri(raw: str) -> Server | None:
    raw = raw.strip()
    if not raw or "://" not in raw:
        return None
    scheme = raw.split("://", 1)[0].lower()
    if scheme not in SUPPORTED_SCHEMES:
        return None
    if scheme == "vmess":
        return parse_vmess(raw)

    # для ss:// часть до @ может быть base64 — urlparse не любит такие userinfo,
    # поэтому работаем с куском "host:port" вручную.
    body = raw.split("://", 1)[1]
    fragment = ""
    if "#" in body:
        body, fragment = body.split("#", 1)
    if "?" in body:
        body = body.split("?", 1)[0]
    if "@" in body:
        hostport = body.rsplit("@", 1)[1]
    else:
        # ss://base64(method:password@host:port)#name — раскрываем
        try:
            padding = "=" * (-len(body) % 4)
            decoded = base64.b64decode(body + padding).decode("utf-8", "replace")
            hostport = decoded.rsplit("@", 1)[-1] if "@" in decoded else decoded
        except (binascii.Error, ValueError):
            return None

    # вырезаем IPv6 в скобках: [::1]:443
    m = re.match(r"^\[(?P<h>[^\]]+)\]:(?P<p>\d+)$", hostport)
    if m:
        host, port = m.group("h"), int(m.group("p"))
    else:
        if ":" not in hostport:
            return None
        host, _, port_str = hostport.rpartition(":")
        try:
            port = int(port_str)
        except ValueError:
            return None
    name = urllib.parse.unquote(fragment) if fragment else f"{host}:{port}"
    return Server(scheme, name, host, port, raw)


def parse_servers(text: str) -> list[Server]:
    servers: list[Server] = []
    seen: set[tuple[str, int, str]] = set()
    for line in text.splitlines():
        srv = parse_uri(line)
        if not srv:
            continue
        key = (srv.host, srv.port, srv.scheme)
        if key in seen:
            continue
        seen.add(key)
        servers.append(srv)
    return servers


def icmp_ping(host: str, timeout: float) -> tuple[bool, float | None]:
    """Один ICMP-пакет через системный ping. Возвращает (ok, rtt_ms)."""
    is_windows = platform.system().lower().startswith("win")
    if is_windows:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), host]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 2
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, None
    if out.returncode != 0:
        return False, None
    m = re.search(r"time[=<]\s*([\d.]+)\s*ms", out.stdout)
    return True, (float(m.group(1)) if m else None)


def tcp_check(host: str, port: int, timeout: float) -> tuple[bool, float | None]:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except (OSError, socket.gaierror):
        return False, None
    return True, (time.perf_counter() - start) * 1000


def check_server(srv: Server, timeout: float) -> CheckResult:
    icmp_ok, icmp_ms = icmp_ping(srv.host, timeout)
    tcp_ok, tcp_ms = tcp_check(srv.host, srv.port, timeout)
    if tcp_ok and icmp_ok:
        status = "online"
    elif tcp_ok:
        status = "tcp-only"  # ICMP блокирован, но VPN-порт открыт
    elif icmp_ok:
        status = "ping-only"  # хост жив, но VPN-порт закрыт
    else:
        status = "offline"
    return CheckResult(
        name=srv.name, host=srv.host, port=srv.port, scheme=srv.scheme,
        icmp_ok=icmp_ok, icmp_ms=icmp_ms, tcp_ok=tcp_ok, tcp_ms=tcp_ms,
        status=status,
    )


STATUS_COLOR = {
    "online":    "\033[92m",  # зелёный
    "tcp-only":  "\033[93m",  # жёлтый
    "ping-only": "\033[93m",
    "offline":   "\033[91m",  # красный
}
RESET = "\033[0m"


def fmt_ms(v: float | None) -> str:
    return f"{v:6.1f}" if v is not None else "   -- "


SMART_RE = re.compile(r"\bsmart\b", re.IGNORECASE)
# "плейсхолдеры" в подписке (хост — короткое число вроде "1111", "3333", "❗️..."):
PLACEHOLDER_RE = re.compile(r"^\d{1,4}$")


def is_smart(srv: Server) -> bool:
    return bool(SMART_RE.search(srv.name))


def is_placeholder(srv: Server) -> bool:
    return bool(PLACEHOLDER_RE.match(srv.host)) or "❗" in srv.name


def print_table(results: list[CheckResult], color: bool = True) -> None:
    if not results:
        return
    name_w = max(20, min(50, max((len(r.name) for r in results), default=20)))
    header = f"{'STATUS':<10} {'PROTO':<9} {'ICMP ms':>7}  {'TCP ms':>7}  {'HOST:PORT':<32}  NAME"
    print(header)
    print("-" * (len(header) + name_w))
    for r in sorted(results, key=lambda r: (r.status == "offline", r.tcp_ms or 9e9)):
        hp = f"{r.host}:{r.port}"
        line = (
            f"{r.status:<10} {r.scheme:<9} {fmt_ms(r.icmp_ms)}  {fmt_ms(r.tcp_ms)}  "
            f"{hp:<32}  {r.name[:name_w]}"
        )
        if color and r.status in STATUS_COLOR:
            line = f"{STATUS_COLOR[r.status]}{line}{RESET}"
        print(line)


def print_summary(results: list[CheckResult], color: bool = True) -> None:
    total = len(results)
    # «активен» = принимает TCP-подключение на VPN-порт. ICMP не считаем —
    # его часто блокируют на самом VPN-хосте.
    active = sum(1 for r in results if r.tcp_ok)
    inactive = total - active
    pct = (active / total * 100) if total else 0.0
    line1 = f"Всего серверов:      {total}"
    line2 = f"Активных (TCP):      {active}"
    line3 = f"Неактивных:          {inactive}"
    line4 = f"Общий статус:        {pct:.1f}%  работает"
    if color:
        col = "\033[92m" if pct >= 80 else "\033[93m" if pct >= 50 else "\033[91m"
        line4 = f"{col}{line4}{RESET}"
    print()
    print(line1)
    print(line2)
    print(line3)
    print(line4)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Парсер VPN-подписки + проверка статуса")
    ap.add_argument("url", help="URL подписки")
    ap.add_argument("--timeout", type=float, default=3.0, help="таймаут на проверку, сек")
    ap.add_argument("--workers", type=int, default=16, help="параллельных проверок")
    ap.add_argument("--json", action="store_true", help="вывод в JSON")
    ap.add_argument("--no-color", action="store_true", help="без ANSI-цветов")
    ap.add_argument("--summary-only", action="store_true",
                    help="не печатать таблицу, только сводку")
    ap.add_argument("--include-smart", action="store_true",
                    help="не отфильтровывать Smart-сервера (по умолчанию пропускаются)")
    args = ap.parse_args(argv)

    try:
        raw = fetch_subscription(args.url)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"Не удалось загрузить подписку: {e}", file=sys.stderr)
        return 2

    decoded = try_b64_decode(raw)
    all_servers = parse_servers(decoded)
    if not all_servers:
        print("В подписке не нашлось ни одного сервера.", file=sys.stderr)
        return 3

    skipped_smart = sum(1 for s in all_servers if is_smart(s))
    skipped_placeholder = sum(1 for s in all_servers if is_placeholder(s))
    servers = [
        s for s in all_servers
        if not is_placeholder(s) and (args.include_smart or not is_smart(s))
    ]

    msg = f"Найдено серверов: {len(all_servers)}"
    if not args.include_smart and skipped_smart:
        msg += f", пропущено Smart: {skipped_smart}"
    if skipped_placeholder:
        msg += f", пропущено плейсхолдеров: {skipped_placeholder}"
    msg += f". Проверяю {len(servers)}..."
    print(msg, file=sys.stderr)

    with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda s: check_server(s, args.timeout), servers))

    if args.json:
        total = len(results)
        active = sum(1 for r in results if r.tcp_ok)
        payload = {
            "total": total,
            "active": active,
            "inactive": total - active,
            "percent": round((active / total * 100) if total else 0.0, 1),
            "servers": [asdict(r) for r in results],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not args.summary_only:
        print_table(results, color=not args.no_color)
    print_summary(results, color=not args.no_color)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
