"""ИИ-помощник оператора: черновики ответов в тикетах.

Не «обучение» в смысле дообучения весов — модель (Claude) получает контекст:
  * системный промпт с правилами поддержки + FAQ-инструкции бота
    (support_quick_replies — те же тексты, что бот шлёт пользователям);
  * карточку пользователя (подписка, баланс, устройства);
  * последние сообщения тикета (support_messages);
  * похожие решённые случаи из ПРОШЛЫХ чатов других пользователей
    (текстовый поиск по support_messages) — «вопрос → как ответил оператор».

Чем больше переписки накапливается в support_messages, тем лучше примеры —
это и есть практическое «обучение на чатах».

Ответ модели — ЧЕРНОВИК: оператор просматривает, правит и отправляет сам.
"""
from __future__ import annotations

import logging

try:
    import anthropic
except ImportError:  # пакет не установлен — скажем об этом словами, а не 500-кой
    anthropic = None  # type: ignore[assignment]

from .config import get_settings
from .database import get_db
from .user_service import users_col
from .utils import GB, jsonable, parse_any_ts

log = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None


class AIError(Exception):
    """Показывается оператору как есть — писать по-русски и по делу."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _get_client() -> "anthropic.AsyncAnthropic":
    global _client
    if anthropic is None:
        raise AIError(
            "На сервере не установлен пакет anthropic — выполните "
            "venv/bin/pip install -r requirements.txt и перезапустите панель", 503)
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise AIError("ИИ-помощник не настроен (ANTHROPIC_API_KEY в .env)", 503)
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


SYSTEM_PROMPT = """Ты — помощник оператора техподдержки VPN-сервиса RS VPN.
Твоя задача — написать ЧЕРНОВИК ответа пользователю, который оператор проверит и отправит.

Правила:
- Пиши по-русски, вежливо и по делу, как живой оператор. Обращение на «вы».
- Ответ уйдёт в Telegram как обычный текст: без markdown, без заголовков, без списков со звёздочками. Эмодзи — умеренно.
- Опирайся ТОЛЬКО на инструкции (FAQ) и данные пользователя ниже. Не выдумывай тарифы, цены, команды и ссылки, которых нет в инструкциях.
- Если по переписке непонятно, в чём проблема, — задай один-два уточняющих вопроса (например, попроси скрин из Happ и название сервера).
- Если проблема решается инструкцией из FAQ — перескажи её шаги коротко, применительно к ситуации пользователя.
- Если вопрос требует действий оператора (возврат денег, продление, отвязка устройств) — напиши пользователю, что сейчас проверишь/сделаешь это, но НЕ обещай конкретные суммы или сроки, которых нет в контексте.
- Не подписывайся и не представляйся. Не упоминай, что ты ИИ.
- Верни ТОЛЬКО текст ответа пользователю, без пояснений для оператора."""


def _fmt_user_context(doc: dict) -> str:
    ud = doc.get("user_data") or {}
    info = doc.get("info") or {}
    vpn = doc.get("vpn") or {}
    exp = parse_any_ts(vpn.get("expireAt"))
    bts = vpn.get("bypass_trafficLimitBytes") or 0
    return (
        f"user_id: {ud.get('user_id')}, имя: {ud.get('first_name') or '—'}\n"
        f"Баланс: {info.get('balance', 0)} руб.\n"
        f"Подписка до: {exp.strftime('%d.%m.%Y') if exp else 'нет подписки'}\n"
        f"Лимит устройств: {vpn.get('hwidDeviceLimit') or '—'}\n"
        f"ByPass трафик: {round(bts / GB, 1)} ГБ"
    )


async def _load_faq() -> str:
    """FAQ-инструкции бота — та же коллекция support_quick_replies."""
    try:
        cursor = get_db()["support_quick_replies"].find(
            {"active": True}, {"_id": 0, "title": 1, "text": 1}).sort("order", 1)
        parts = []
        async for item in cursor:
            text = (item.get("text") or "").strip()
            if text:
                parts.append(f"### {item.get('title', '')}\n{text}")
        return "\n\n".join(parts)
    except Exception:
        return ""


async def _load_dialog(user_id: int, limit: int) -> list[dict]:
    settings = get_settings()
    col = get_db()[settings.support_messages_collection]
    msgs = [m async for m in col.find({"user_id": user_id}).sort("timestamp", -1).limit(limit)]
    msgs.reverse()
    return msgs


async def _find_similar_cases(user_id: int, query_text: str, limit: int = 3) -> list[str]:
    """«Обучение на чатах»: похожие вопросы других пользователей и ответы операторов.
    Требует text-индекс на support_messages.text; при его отсутствии тихо пропускается."""
    query_text = (query_text or "").strip()
    if len(query_text) < 8:
        return []
    settings = get_settings()
    col = get_db()[settings.support_messages_collection]
    examples: list[str] = []
    try:
        cursor = col.find(
            {"$text": {"$search": query_text}, "direction": "user",
             "user_id": {"$ne": user_id}},
            {"score": {"$meta": "textScore"}},
        ).sort([("score", {"$meta": "textScore"})]).limit(limit * 2)
        async for q in cursor:
            answer = await col.find_one(
                {"user_id": q["user_id"], "direction": "operator",
                 "timestamp": {"$gt": q["timestamp"]}},
                sort=[("timestamp", 1)],
            )
            if answer and (answer.get("text") or "").strip():
                examples.append(
                    f"Вопрос: {(q.get('text') or '')[:300]}\n"
                    f"Ответ оператора: {(answer.get('text') or '')[:500]}"
                )
            if len(examples) >= limit:
                break
    except Exception as e:
        log.debug("similar-cases search unavailable: %s", e)
    return examples


def _dialog_to_text(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        who = {"user": "Пользователь", "operator": "Оператор", "system": "Система"}.get(
            m.get("direction"), "?")
        text = (m.get("text") or "").strip()
        att = m.get("attachment") or {}
        if att.get("type"):
            text = (text + f" [вложение: {att['type']}]").strip()
        if text:
            lines.append(f"{who}: {text[:600]}")
    return "\n".join(lines) or "(сообщений пока нет)"


async def suggest_reply(user_id: int) -> str:
    """Собирает контекст и возвращает черновик ответа."""
    settings = get_settings()
    client = _get_client()

    user_doc = await users_col().find_one(
        {"user_data.user_id": user_id},
        {"user_data": 1, "info.balance": 1, "vpn": 1},
    )
    if user_doc is None:
        raise AIError(f"Пользователь {user_id} не найден", 404)

    dialog = await _load_dialog(user_id, settings.ai_history_messages)
    last_user_msg = next(
        (m.get("text") or "" for m in reversed(dialog) if m.get("direction") == "user"), "")
    faq, similar = await _load_faq(), await _find_similar_cases(user_id, last_user_msg)

    # Стабильная часть (правила + FAQ) — первой и с cache_control: кэшируется между запросами
    system_blocks = [{
        "type": "text",
        "text": SYSTEM_PROMPT + ("\n\n## Инструкции (FAQ) сервиса:\n\n" + faq if faq else ""),
        "cache_control": {"type": "ephemeral"},
    }]

    user_parts = [f"## Данные пользователя:\n{_fmt_user_context(jsonable(user_doc))}"]
    if similar:
        user_parts.append("## Похожие решённые случаи (для стиля и подхода):\n\n"
                          + "\n\n".join(similar))
    user_parts.append(f"## Переписка тикета:\n{_dialog_to_text(dialog)}")
    user_parts.append("Напиши черновик следующего ответа оператора пользователю.")

    async def _call(extra: dict):
        return await client.messages.create(
            model=settings.ai_model,
            max_tokens=settings.ai_max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": "\n\n".join(user_parts)}],
            **extra,
        )

    try:
        try:
            response = await _call({"output_config": {"effort": settings.ai_effort}})
        except TypeError:
            # SDK старее 0.92 не знает output_config — работаем без него
            log.warning("anthropic SDK без output_config — обновите пакет "
                        "(pip install -r requirements.txt); работаю без effort")
            response = await _call({})
    except anthropic.AuthenticationError:
        raise AIError("Неверный ANTHROPIC_API_KEY", 503)
    except anthropic.RateLimitError:
        raise AIError("ИИ перегружен (rate limit) — попробуйте через минуту")
    except anthropic.APIStatusError as e:
        detail = ""
        try:
            detail = (e.body or {}).get("error", {}).get("message", "")[:200]
        except Exception:
            pass
        raise AIError(f"Ошибка ИИ-сервиса ({e.status_code})" + (f": {detail}" if detail else ""))
    except anthropic.APIConnectionError as e:
        raise AIError(f"Нет соединения с ИИ-сервисом: {str(e)[:150]}")

    if response.stop_reason == "refusal":
        raise AIError("ИИ отказался отвечать на этот запрос")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise AIError("ИИ вернул пустой ответ — попробуйте ещё раз")
    return text
