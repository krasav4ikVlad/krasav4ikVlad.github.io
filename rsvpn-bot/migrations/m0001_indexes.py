"""Индексы и первичное наполнение справочников."""

from __future__ import annotations


async def up(container) -> None:
    await container.startup()
