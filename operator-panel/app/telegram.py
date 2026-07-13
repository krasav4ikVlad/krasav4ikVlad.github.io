"""Thin async client for the Telegram Bot API (support bot).

Used by the tickets module to mirror the bot's own behaviour:
  * reply to the user in PM
  * mirror operator replies into the support-chat forum thread
  * rename the forum topic when the ticket status changes
  * send the rating keyboard on close (same rate:{1..5} callbacks the bot handles)
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import get_settings

log = logging.getLogger(__name__)

STATUS_EMOJI = {"pending": "🟡", "open": "🟢", "closed": "🔴"}

RATING_KEYBOARD = {
    "inline_keyboard": [[
        {"text": str(i), "callback_data": f"rate:{i}"} for i in range(1, 6)
    ]]
}


class TelegramError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TelegramClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.token = settings.tg_bot_token
        self.support_chat_id = settings.support_chat_id

    @property
    def configured(self) -> bool:
        return bool(self.token and self.support_chat_id)

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        if not self.token:
            raise TelegramError("Telegram-бот не настроен (TG_BOT_TOKEN)")
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload)
        except httpx.HTTPError as e:
            raise TelegramError(f"Telegram недоступен: {e.__class__.__name__}")
        try:
            data = resp.json()
        except Exception:
            raise TelegramError("Telegram вернул невалидный ответ")
        if not data.get("ok"):
            desc = data.get("description", "неизвестная ошибка")
            raise TelegramError(f"Telegram: {desc}")
        return data.get("result")

    async def send_to_user(self, user_id: int, text: str,
                           reply_markup: dict | None = None) -> None:
        payload: dict[str, Any] = {
            "chat_id": user_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        await self._call("sendMessage", payload)

    async def send_to_chat(self, text: str) -> None:
        """Сообщение в общий раздел саппорт-чата (вне тредов) — для алертов."""
        if not self.support_chat_id:
            raise TelegramError("Не задан SUPPORT_CHAT_ID")
        await self._call("sendMessage", {
            "chat_id": self.support_chat_id,
            "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })

    async def send_to_thread(self, thread_id: int, text: str) -> None:
        if not self.support_chat_id:
            raise TelegramError("Не задан SUPPORT_CHAT_ID")
        await self._call("sendMessage", {
            "chat_id": self.support_chat_id,
            "message_thread_id": thread_id,
            "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })

    async def _call_multipart(self, method: str, data: dict[str, Any],
                              files: dict[str, tuple[str, bytes, str]]) -> Any:
        if not self.token:
            raise TelegramError("Telegram-бот не настроен (TG_BOT_TOKEN)")
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, data=data, files=files)
        except httpx.HTTPError as e:
            raise TelegramError(f"Telegram недоступен: {e.__class__.__name__}")
        try:
            payload = resp.json()
        except Exception:
            raise TelegramError("Telegram вернул невалидный ответ")
        if not payload.get("ok"):
            raise TelegramError(f"Telegram: {payload.get('description', 'неизвестная ошибка')}")
        return payload.get("result")

    async def send_photo_to_user(self, user_id: int, photo: bytes, filename: str,
                                 caption: str | None = None) -> dict:
        data: dict[str, Any] = {"chat_id": str(user_id)}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "HTML"
        return await self._call_multipart(
            "sendPhoto", data, {"photo": (filename, photo, "application/octet-stream")})

    async def send_photo_to_thread(self, thread_id: int, photo: bytes, filename: str,
                                   caption: str | None = None) -> dict:
        if not self.support_chat_id:
            raise TelegramError("Не задан SUPPORT_CHAT_ID")
        data: dict[str, Any] = {
            "chat_id": str(self.support_chat_id),
            "message_thread_id": str(thread_id),
        }
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "HTML"
        return await self._call_multipart(
            "sendPhoto", data, {"photo": (filename, photo, "application/octet-stream")})

    async def get_file(self, file_id: str) -> tuple[bytes, str]:
        """Download an attachment by Telegram file_id. Returns (content, file_path).
        Works for files up to 20 MB (Bot API limit)."""
        result = await self._call("getFile", {"file_id": file_id})
        path = (result or {}).get("file_path")
        if not path:
            raise TelegramError("Файл недоступен (нет file_path)")
        url = f"https://api.telegram.org/file/bot{self.token}/{path}"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(url)
        except httpx.HTTPError as e:
            raise TelegramError(f"Не удалось скачать файл: {e.__class__.__name__}")
        if resp.status_code != 200:
            raise TelegramError(f"Не удалось скачать файл (HTTP {resp.status_code})")
        return resp.content, path

    async def set_thread_status_title(self, thread_id: int, user_id: int, status: str) -> None:
        """Rename the forum topic to '🟢 Тикет #uid' — same convention as the bot."""
        emoji = STATUS_EMOJI.get(status, "🟡")
        try:
            await self._call("editForumTopic", {
                "chat_id": self.support_chat_id,
                "message_thread_id": thread_id,
                "name": f"{emoji} Тикет #{user_id}",
            })
        except TelegramError as e:
            # Заголовок — косметика: не валим операцию, но фиксируем в логах
            log.warning("editForumTopic failed for thread %s: %s", thread_id, e.message)


def get_telegram() -> TelegramClient:
    return TelegramClient()
