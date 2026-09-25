"""Выгрузка таблицей: .xlsx, а без openpyxl — CSV.

Проверяется то, из-за чего выгрузку обычно приходится чинить руками:
кодировка, разделитель и id, который Excel norовит показать как
8,02421E+08.
"""

import io

from app.services import excel

COLUMNS = ('участник_id', 'username', 'билетов')
ROWS = [[802421217, 'vasya', 3], [802421218, '', 1]]


def test_the_file_is_a_real_xlsx():
    body, ext = excel.build(COLUMNS, ROWS, 'Участники')

    assert ext == 'xlsx' and body.startswith(b'PK')


def test_the_numbers_stay_numbers():
    """Строкой id из десяти цифр Excel показывает как 8,02421E+08, и
    скопировать его оттуда уже нельзя."""
    from openpyxl import load_workbook

    body, _ = excel.build(COLUMNS, ROWS, 'Участники')
    page = load_workbook(io.BytesIO(body)).active

    assert page['A1'].value == 'участник_id'
    assert page['A2'].value == 802421217 and isinstance(page['A2'].value, int)
    assert page['C2'].value == 3


def test_the_header_stays_visible_and_filterable():
    """Список победителей длинный: без закреплённой шапки к середине уже
    не видно, где какая колонка."""
    from openpyxl import load_workbook

    body, _ = excel.build(COLUMNS, ROWS, 'Участники')
    page = load_workbook(io.BytesIO(body)).active

    assert page.freeze_panes == 'A2'
    assert page.auto_filter.ref and page['A1'].font.bold


def test_the_sheet_is_named():
    from openpyxl import load_workbook

    body, _ = excel.build(COLUMNS, ROWS, 'Участники')

    assert load_workbook(io.BytesIO(body)).active.title == 'Участники'


def test_without_openpyxl_a_csv_comes_instead_of_an_error(monkeypatch):
    """Команду зовут раз в год — в день подведения итогов. Ответить на неё
    ошибкой импорта нельзя."""
    def no_openpyxl(*args, **kwargs):
        raise ImportError('openpyxl')

    monkeypatch.setattr(excel, '_xlsx', no_openpyxl)
    body, ext = excel.build(COLUMNS, ROWS, 'Участники')

    assert ext == 'csv'
    assert body.startswith(b'\xef\xbb\xbf')        # иначе кракозябры в Excel
    assert b';' in body                            # иначе всё в одной ячейке


def test_empty_cells_do_not_become_the_word_none():
    from openpyxl import load_workbook

    body, _ = excel.build(COLUMNS, [[1, None, 0]], 'Участники')
    page = load_workbook(io.BytesIO(body)).active

    assert page['B2'].value in (None, '')
