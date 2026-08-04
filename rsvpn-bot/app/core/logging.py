"""Настройка логирования. Вызывается один раз на старте процесса."""

from __future__ import annotations

import logging
import sys


def setup_logging(level: str = 'INFO') -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format='%(asctime)s %(levelname)-8s %(name)s | %(message)s',
        datefmt='%d.%m %H:%M:%S',
        stream=sys.stdout,
        force=True,
    )
    logging.getLogger('aiogram.event').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
