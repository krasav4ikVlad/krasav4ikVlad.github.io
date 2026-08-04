"""Раздел «support».

Скелет: заведите роутер, повесьте хендлеры на типизированные callback_data
из app/bot/callbacks.py, экраны собирайте через app/bot/screens/, логику
выносите в app/services/. Переносите сюда соответствующие ветки из старого
start.py по одной — старый обработчик продолжает работать, пока раздел не
перенесён.
"""

from __future__ import annotations

from aiogram import Router

router = Router(name='support')
