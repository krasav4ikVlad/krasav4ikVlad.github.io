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

CHECKER_URL = os.environ.get("CHECKER_URL", "").rstrip("/")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
XRAY_BIN = os.environ.get("XRAY_BIN", "xray")
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


async def run_tunnel(outbound: dict, probe_url: str, speed_url: str, p: dict) -> dict:
    """Поднять xray и реально сходить наружу + замерить установившуюся скорость."""
    port = _free_port()
    cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks",
                      "settings": {"udp": True}}],
        "outbounds": [outbound],
    }
    proc = path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(cfg, f)
            path = f.name
        try:
            proc = await asyncio.create_subprocess_exec(
                XRAY_BIN, "run", "-c", path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return {"ok": False, "info": "xray не установлен на зонде"}
        if not await _wait_port(port, p.get("start_timeout", 10)):
            return {"ok": False, "info": "xray не поднялся (конфиг?)"}

        proxy = f"socks5://127.0.0.1:{port}"
        # 1) гео/выход
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=p.get("probe_timeout", 14), verify=False) as cl:
                data = (await cl.get(probe_url)).json()
        except (httpx.TimeoutException, httpx.ProxyError, httpx.ConnectError):
            return {"ok": False, "info": "нет выхода в сеть (таймаут — вероятно DPI/бан)"}
        except Exception as e:
            return {"ok": False, "info": f"туннель: {type(e).__name__}"}
        if data.get("status") != "success":
            return {"ok": False, "info": "туннель поднялся, но нет выхода в сеть"}
        geo = f'{data.get("country","")} ({data.get("countryCode","")}) · {data.get("query","")}'

        # 2) главное: открываются ли иностранные сервисы через туннель
        services = p.get("services") or [
            ["YouTube", "https://www.youtube.com/generate_204"],
            ["ChatGPT", "https://chatgpt.com/cdn-cgi/trace"],
            ["Telegram", "https://web.telegram.org/"],
            ["Instagram", "https://www.instagram.com/"],
            ["Google", "https://www.gstatic.com/generate_204"],
        ]

        async def probe(name, url):
            t0 = time.perf_counter()
            try:
                async with httpx.AsyncClient(proxy=proxy, timeout=10, verify=False,
                                             follow_redirects=False,
                                             headers={"User-Agent": "Mozilla/5.0 nodewiki-checker"}) as cl:
                    r = await cl.get(url)
                return {"name": name, "ok": r.status_code < 400,
                        "info": f"{r.status_code} · {(time.perf_counter()-t0)*1000:.0f} ms"}
            except Exception as e:
                return {"name": name, "ok": False, "info": type(e).__name__}

        svc = await asyncio.gather(*(probe(n, u) for n, u in services))
        ok_n = sum(1 for s in svc if s["ok"])
        res = {"ok": ok_n > 0, "services": svc, "geo": geo}
        if ok_n == 0:
            res["info"] = f"сервисы недоступны через ноду · выход {geo}"
        elif ok_n < len(svc):
            res["warn"] = True
            res["info"] = f"часть сервисов недоступна ({ok_n}/{len(svc)}) · {geo}"
        else:
            res["info"] = f"все сервисы открываются · {geo}"
        return res
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


async def main():
    headers = {"x-agent-token": AGENT_TOKEN}
    print(f"[i] residential-зонд запущен → {CHECKER_URL}")
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
                result = await run_tunnel(task["outbound"], task["probe_url"],
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
