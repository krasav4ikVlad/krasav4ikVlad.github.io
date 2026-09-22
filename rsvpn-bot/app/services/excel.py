"""Выгрузка таблицей: .xlsx, а при отсутствии openpyxl — CSV.

Excel открывает и CSV, но только после разговора о кодировке и
разделителе, а выгрузку смотрят с телефона и в спешке. Поэтому основной
формат — настоящий .xlsx: он открывается одним касанием и в Telegram
показывается предпросмотром.

Запасной путь на CSV нужен ровно для одного случая: бот обновился, а
`pip install` не прошёл. Отдать таблицу в чуть менее удобном виде лучше,
чем ответить ошибкой импорта на команду, которую зовут раз в год — в день
подведения итогов.
"""

from __future__ import annotations

import csv
import io
import logging

log = logging.getLogger(__name__)

# Ширина колонок по умолчанию: без неё id и даты прячутся за «#####».
MIN_WIDTH = 10
MAX_WIDTH = 60


def build(columns, rows, sheet: str = 'Лист') -> tuple[bytes, str]:
    """(содержимое файла, расширение). Расширение — 'xlsx' или 'csv'."""
    try:
        return _xlsx(columns, rows, sheet), 'xlsx'
    except ImportError:
        log.warning('openpyxl не установлен, выгружаю CSV: '
                    'проверьте pip install -r requirements.txt')
        return _csv(columns, rows), 'csv'


def _xlsx(columns, rows, sheet: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    book = Workbook()
    page = book.active
    page.title = sheet[:31] or 'Лист'
    page.append(list(columns))

    for cell in page[1]:
        cell.font = Font(bold=True)

    for row in rows:
        page.append([_cell(value) for value in row])

    # Шапка не уезжает при прокрутке: список победителей длинный, и без
    # этого к середине уже не видно, где какая колонка.
    page.freeze_panes = 'A2'
    page.auto_filter.ref = page.dimensions

    for index, title in enumerate(columns, start=1):
        longest = max([len(str(title))]
                      + [len(str(_cell(row[index - 1]))) for row in rows
                         if len(row) >= index])
        page.column_dimensions[get_column_letter(index)].width = \
            min(MAX_WIDTH, max(MIN_WIDTH, longest + 2))

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _cell(value):
    """Excel сам решает, что число, а что текст, и id из 10 цифр он бы
    показал как 8,02421E+08. Числа оставляем числами, остальное — строкой."""
    if isinstance(value, bool):
        return 'да' if value else 'нет'
    if isinstance(value, (int, float)):
        return value
    return '' if value is None else str(value)


def _csv(columns, rows) -> bytes:
    """BOM и точка с запятой: так Excel открывает файл без танцев."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=';', lineterminator='\r\n')
    writer.writerow(list(columns))
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return buffer.getvalue().encode('utf-8-sig')
