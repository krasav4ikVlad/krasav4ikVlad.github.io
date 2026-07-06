"""Диагностика ИИ-помощника: где именно ломается путь до api.anthropic.com.

Запуск на сервере из каталога панели:
    cd /opt/operator-panel && venv/bin/python scripts/check_ai.py

Скрипт проверяет по шагам: настройки .env -> версия пакетов -> доступность
api.anthropic.com (напрямую и через AI_PROXY_URL, если задан) -> реальный
мини-запрос к модели. На каждом шаге печатает ОК или точную ошибку.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def step(name):
    print(f"\n=== {name}")


def ok(msg=""):
    print(f"  OK {msg}")


def fail(msg):
    print(f"  ОШИБКА: {msg}")


async def main() -> int:
    step("1. Настройки (.env)")
    try:
        from app.config import get_settings
        s = get_settings()
    except Exception as e:
        fail(f"не удалось загрузить настройки: {e}")
        return 1
    if not s.anthropic_api_key:
        fail("ANTHROPIC_API_KEY пуст — добавьте ключ в .env")
        return 1
    ok(f"ключ задан ({s.anthropic_api_key[:12]}…), модель {s.ai_model}, "
       f"effort {s.ai_effort}, таймаут {s.ai_timeout_sec}с")
    if s.ai_proxy_url:
        safe = s.ai_proxy_url.split("@")[-1] if "@" in s.ai_proxy_url else s.ai_proxy_url
        ok(f"прокси задан: {s.ai_proxy_url.split('://')[0]}://…@{safe.split('://')[-1]}"
           if "@" in s.ai_proxy_url else f"прокси задан: {s.ai_proxy_url}")
    else:
        print("  прокси не задан — соединение напрямую")

    step("2. Пакеты")
    try:
        import anthropic
        ok(f"anthropic {getattr(anthropic, '__version__', '?')}")
    except ImportError as e:
        fail(f"пакет anthropic не установлен: {e} — venv/bin/pip install -r requirements.txt")
        return 1
    import httpx
    ok(f"httpx {httpx.__version__}")
    if s.ai_proxy_url.startswith("socks"):
        try:
            import socksio  # noqa: F401
            ok("socksio (поддержка socks5) установлен")
        except ImportError:
            fail("socks5-прокси задан, но пакет socksio не установлен — "
                 "venv/bin/pip install 'httpx[socks]'")
            return 1

    async def probe(label, proxy):
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=httpx.Timeout(20, connect=8)) as c:
                r = await c.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": s.anthropic_api_key,
                             "anthropic-version": "2023-06-01"})
            dt = time.monotonic() - t0
            if r.status_code == 200:
                ok(f"{label}: HTTP 200 за {dt:.1f}с — доступ есть")
                return True
            body = r.text[:200]
            fail(f"{label}: HTTP {r.status_code} за {dt:.1f}с — {body}")
            if r.status_code == 403:
                print("     похоже на блокировку региона — нужен прокси из другой страны")
            elif r.status_code == 401:
                print("     ключ неверный или отозван — проверьте ANTHROPIC_API_KEY")
            return False
        except Exception as e:
            dt = time.monotonic() - t0
            fail(f"{label}: {type(e).__name__}: {e} (через {dt:.1f}с)")
            return False

    step("3. Доступность api.anthropic.com")
    direct = await probe("напрямую", None)
    via_proxy = None
    if s.ai_proxy_url:
        via_proxy = await probe(f"через прокси", s.ai_proxy_url)

    if not direct and via_proxy is None:
        print("\nИтог: прямого доступа нет и прокси не задан. "
              "Добавьте AI_PROXY_URL в .env (http:// или socks5:// прокси за пределами РФ).")
        return 1
    if via_proxy is False:
        print("\nИтог: прокси не работает. Проверьте адрес/логин/пароль, что прокси жив "
              "(curl -x <прокси> https://api.anthropic.com/v1/models с сервера), "
              "и что его страна не под блокировкой Anthropic.")
        return 1

    step("4. Реальный запрос к модели (как делает панель)")
    try:
        from app import ai as ai_mod
        ai_mod._client = None  # свежий клиент с текущими настройками
        client = ai_mod._get_client()
        t0 = time.monotonic()
        resp = await client.messages.create(
            model=s.ai_model, max_tokens=50,
            messages=[{"role": "user", "content": "Ответь одним словом: работаешь?"}])
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        ok(f"модель ответила за {time.monotonic() - t0:.1f}с: «{text[:60]}»")
    except Exception as e:
        fail(f"{type(e).__name__}: {str(e)[:300]}")
        return 1

    print("\nВсё работает — кнопка «Ещё вариант ИИ» в панели должна отвечать. "
          "Если в браузере всё ещё ошибка — pm2 restart operator-panel и "
          "проверьте, что обновлённый код скопирован (rsync).")
    return 0


sys.exit(asyncio.run(main()))
