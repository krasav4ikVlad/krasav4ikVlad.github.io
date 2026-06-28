#!/usr/bin/env python3
"""
nodewiki residential probe — лёгкий агент туннель-теста.

Запускается НА МАШИНЕ С ДОМАШНИМ/МОБИЛЬНЫМ интернетом (домашний ПК, Raspberry Pi,
телефон-хотспот). За NAT — поэтому САМ опрашивает чекер (outbound), берёт задачу,
поднимает xray с конфигом ноды и реально гоняет трафик ЧЕРЕЗ СВОЙ КАНАЛ —
результат «как видит конечный пользователь». Возвращает результат чекеру.

Нужно: python3, xray-core в PATH, pip install "httpx[socks]".

Переменные окружения:
  CHECKER_URL    https://checker.nodewiki.info
  AGENT_TOKEN    общий секрет (тот же, что CHECKER_AGENT_TOKEN на чекере)
  XRAY_BIN       путь к xray (по умолчанию xray)
  POLL_INTERVAL  пауза между опросами, сек (по умолчанию 5)

Запуск:  CHECKER_URL=... AGENT_TOKEN=... python3 probe_agent.py
"""

import asyncio
import json
import os
import socket
import statistics
import sys
import tempfile
import time

import httpx

# Windows: при редиректе вывода в файл консольная кодировка cp1251 не вмещает
# стрелки/эмодзи -> UnicodeEncodeError. Пишем UTF-8 и строкой-за-строкой
# (line_buffering), иначе лог «висит» в буфере, пока процесс жив.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass

CHECKER_URL = os.environ.get("CHECKER_URL", "").rstrip("/")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
XRAY_BIN = os.environ.get("XRAY_BIN", "xray")
XRAY_KNIFE_BIN = os.environ.get("XRAY_KNIFE_BIN", "")  # парсер share-ссылок (libXray)
SINGBOX_BIN = os.environ.get("SINGBOX_BIN", "")        # для Hysteria2 (xray не умеет)
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "5"))

if not CHECKER_URL or not AGENT_TOKEN:
    print("Заданы не все переменные: CHECKER_URL и AGENT_TOKEN обязательны.", file=sys.stderr)
    sys.exit(1)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


_PROXY_PROTOS = {"vless", "vmess", "trojan", "shadowsocks", "socks", "http"}


async def outbound_from_link(link: str):
    """Спарсить share-ссылку через xray-knife (libXray) -> xray outbound.
    Это покрывает все типы/параметры, что понимает xray, в отличие от
    ручного парсера на чекере. None, если xray-knife нет или не разобрал."""
    if not XRAY_KNIFE_BIN or not link:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            XRAY_KNIFE_BIN, "parse", "-c", link, "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=12)
    except (FileNotFoundError, asyncio.TimeoutError, Exception):
        return None
    txt = out.decode("utf-8", "replace").strip()
    cfg = None
    for candidate in (txt, txt[txt.find("{"):] if "{" in txt else ""):
        try:
            cfg = json.loads(candidate)
            break
        except Exception:
            continue
    if not isinstance(cfg, dict):
        return None
    obs = cfg.get("outbounds")
    if not isinstance(obs, list):
        return None
    for o in obs:
        if isinstance(o, dict) and o.get("protocol") in _PROXY_PROTOS:
            o = {k: v for k, v in o.items() if k != "tag"}
            o["tag"] = "proxy"
            return o
    return None


async def _wait_port(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            fut = asyncio.open_connection("127.0.0.1", port)
            _, w = await asyncio.wait_for(fut, timeout=1.0)
            w.close()
            return True
        except Exception:
            await asyncio.sleep(0.25)
    return False


# фолбэк, если чекер не прислал список; только фильтруемые в РФ сервисы
DEFAULT_SERVICES = [
    ["YouTube", "https://www.youtube.com/generate_204"],
    ["Telegram", "https://web.telegram.org/"],
    ["Instagram", "https://www.instagram.com/"],
]


async def _probe_service(name: str, url: str, proxy: str | None = None) -> dict:
    """Один сервис: через socks-прокси туннеля или напрямую (proxy=None).
    Один ретрай на разовый обрыв (ТСПУ часто рвёт первое соединение)."""
    last = "нет ответа"
    for attempt in range(2):
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=10, verify=False,
                                         follow_redirects=False,
                                         headers={"User-Agent": "Mozilla/5.0 nodewiki-checker"}) as cl:
                r = await cl.get(url)
            return {"name": name, "ok": r.status_code < 400,
                    "info": f"{r.status_code} · {(time.perf_counter()-t0)*1000:.0f} ms"}
        except Exception as e:
            last = type(e).__name__
            if attempt == 0:
                await asyncio.sleep(0.3)
    return {"name": name, "ok": False, "info": last}


async def _measure_speed(proxy: str, url: str, window: float, max_bytes: int) -> tuple[float, int]:
    """Качаем через туннель до window секунд / max_bytes и считаем установившуюся
    скорость (Mbps): отбрасываем разгон, берём медиану второй половины интервалов."""
    samples, total, start = [], 0, time.perf_counter()
    try:
        async with httpx.AsyncClient(proxy=proxy, verify=False,
                                     timeout=httpx.Timeout(10.0, read=window + 5)) as cl:
            last_t, last_b = start, 0
            async with cl.stream("GET", url) as resp:
                if resp.status_code < 400:
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        now = time.perf_counter()
                        if now - last_t >= 0.5:
                            samples.append((total - last_b) * 8 / (now - last_t) / 1e6)
                            last_t, last_b = now, total
                        if total >= max_bytes or now - start >= window:
                            break
    except Exception:
        pass
    elapsed = max(time.perf_counter() - start, 0.001)
    avg = total * 8 / elapsed / 1e6
    if len(samples) >= 4:
        steady = statistics.median(samples[len(samples) // 2:])
    elif samples:
        steady = samples[-1]
    else:
        steady = avg
    return (min(steady, avg) if total else 0.0), total


async def _probe_via_proxy(proxy: str, probe_url: str, speed_url: str, p: dict,
                           services: list) -> dict:
    """Через готовый socks-proxy: гео + сервисы + скорость. Общая часть для
    любого движка (xray / sing-box)."""
    # 1) гео/выход — с ретраями и НЕ как стоп-кран (обрыв на гео ≠ нода мертва).
    geo, geo_err = "", ""
    for _ in range(3):
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=p.get("probe_timeout", 14), verify=False) as cl:
                data = (await cl.get(probe_url)).json()
            if data.get("status") == "success":
                geo = f'{data.get("country","")} ({data.get("countryCode","")}) · {data.get("query","")}'
                break
            geo_err = "нет выхода"
        except Exception as e:
            geo_err = type(e).__name__
        await asyncio.sleep(0.4)

    # 2) ГЛАВНОЕ: открываются ли сервисы через туннель — проверяем ВСЕГДА.
    svc = await asyncio.gather(*(_probe_service(n, u, proxy) for n, u in services))
    ok_n = sum(1 for s in svc if s["ok"])
    loc = geo or f"выход не определился ({geo_err or 'обрыв'})"
    res = {"ok": ok_n > 0, "services": svc, "geo": geo}
    if ok_n == 0:
        res["info"] = f"через ноду ничего не открывается — соединение рвётся · {loc}"
    elif ok_n < len(svc):
        res["warn"] = True
        res["info"] = f"часть сервисов недоступна ({ok_n}/{len(svc)}) · {loc}"
    else:
        res["info"] = f"все сервисы открываются · {loc}"

    # 3) реальная пропускная способность через ноду (ловит ТСПУ-резку «в ноль»).
    sp = p.get("speed") or {}
    if speed_url and sp.get("enabled", True) and res["ok"]:
        mbps, nbytes = await _measure_speed(
            proxy, speed_url, float(sp.get("window", 8.0)),
            int(sp.get("max_bytes", 20_000_000)),
        )
        slow = float(sp.get("slow_mbps", 8.0))
        dead = float(sp.get("min_mbps", 0.5))
        res["speed_mbps"] = round(mbps, 1)
        if nbytes == 0:
            res["speed"] = "скорость не измерилась"
        elif mbps < dead:
            res["speed"] = f"{mbps:.1f} Mbps — практически ноль (режется)"
            res["slow"] = res["warn"] = True
        elif mbps < slow:
            res["speed"] = f"{mbps:.1f} Mbps — медленно (похоже на замедление)"
            res["slow"] = res["warn"] = True
        else:
            res["speed"] = f"{mbps:.0f} Mbps"
    return res


async def _raise_and_probe(bin_path: str, cfg: dict, port: int, missing: str,
                           probe_url: str, speed_url: str, p: dict, services: list) -> dict:
    """Записать конфиг, запустить движок (`<bin> run -c`), дождаться socks-порта,
    прогнать проверки. missing — текст, если бинаря нет."""
    proc = path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(cfg, f)
            path = f.name
        try:
            proc = await asyncio.create_subprocess_exec(
                bin_path, "run", "-c", path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return {"ok": False, "info": missing}
        if not await _wait_port(port, p.get("start_timeout", 10)):
            return {"ok": False, "info": "движок не поднялся (конфиг?)"}
        return await _probe_via_proxy(f"socks5://127.0.0.1:{port}", probe_url, speed_url, p, services)
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


async def run_tunnel(outbound: dict, probe_url: str, speed_url: str, p: dict,
                     services: list) -> dict:
    """xray: socks-inbound + outbound."""
    port = _free_port()
    cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks",
                      "settings": {"udp": True}}],
        "outbounds": [outbound],
    }
    return await _raise_and_probe(XRAY_BIN, cfg, port, "xray не установлен на зонде",
                                  probe_url, speed_url, p, services)


async def run_tunnel_hy2(spec: dict, probe_url: str, speed_url: str, p: dict,
                         services: list) -> dict:
    """sing-box: Hysteria2 (xray такое не умеет)."""
    if not SINGBOX_BIN:
        return {"ok": False, "info": "sing-box не установлен на зонде (нужен для Hysteria2)"}
    port = _free_port()
    cfg = {
        "log": {"level": "error"},
        "inbounds": [{"type": "socks", "listen": "127.0.0.1", "listen_port": port}],
        "outbounds": [{
            "type": "hysteria2",
            "server": spec["server"],
            "server_port": int(spec["port"]),
            "password": spec.get("password", ""),
            "tls": {
                "enabled": True,
                "server_name": spec.get("sni") or spec["server"],
                "alpn": spec.get("alpn") or ["h3"],
                "insecure": bool(spec.get("insecure")),
            },
        }],
    }
    return await _raise_and_probe(SINGBOX_BIN, cfg, port,
                                  "sing-box не установлен на зонде (нужен для Hysteria2)",
                                  probe_url, speed_url, p, services)


async def run_task(task: dict, probe_url: str, speed_url: str, p: dict) -> dict:
    """Туннель-тест + КОНТРОЛЬ (те же сервисы напрямую, без туннеля). Движок
    выбираем по типу: Hysteria2 -> sing-box, остальное -> xray."""
    services = p.get("services") or DEFAULT_SERVICES
    direct_fut = asyncio.gather(*(_probe_service(n, u) for n, u in services))
    hy2 = task.get("hy2")
    if hy2:
        res = await run_tunnel_hy2(hy2, probe_url, speed_url, p, services)
    else:
        # outbound: сначала xray-knife по ссылке (libXray), иначе готовый от чекера
        outbound = await outbound_from_link(task.get("link", "")) or task.get("outbound")
        if not outbound:
            res = {"ok": False, "info": "конфиг не разобран (нет outbound)"}
        else:
            res = await run_tunnel(outbound, probe_url, speed_url, p, services)
    try:
        res["direct"] = list(await direct_fut)
    except Exception:
        pass
    return res


async def main():
    headers = {"x-agent-token": AGENT_TOKEN}
    print(f"[i] residential-зонд запущен -> {CHECKER_URL}")
    async with httpx.AsyncClient(timeout=30) as api:
        while True:
            try:
                r = await api.post(f"{CHECKER_URL}/agent/poll", headers=headers)
                if r.status_code == 401:
                    print("[x] неверный AGENT_TOKEN", file=sys.stderr)
                    await asyncio.sleep(30)
                    continue
                task = r.json().get("task")
            except Exception as e:
                print(f"[!] poll: {e}", file=sys.stderr)
                await asyncio.sleep(POLL_INTERVAL)
                continue
            if not task:
                await asyncio.sleep(POLL_INTERVAL)
                continue
            print(f"[>] задача {task['task_id']} — тестирую через свой канал…")
            try:
                result = await run_task(task, task["probe_url"],
                                        task["speed_url"], task.get("params", {}))
            except Exception as e:
                result = {"ok": False, "info": f"зонд: {type(e).__name__}"}
            try:
                await api.post(f"{CHECKER_URL}/agent/result", headers=headers,
                               json={"task_id": task["task_id"], "result": result})
                print(f"[<] результат отправлен: {result.get('info','')}")
            except Exception as e:
                print(f"[!] result: {e}", file=sys.stderr)


if __name__ == "__main__":
    # на Windows для subprocess (запуск xray) нужен ProactorEventLoop
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
