import zipfile
from pathlib import Path

import openpyxl
import xlrd
from openpyxl.utils.cell import range_boundaries

MAX_CELLS = 1_000_000


def workbook(path):
    path = Path(path)
    if path.suffix == ".xlsx":
        if not zipfile.is_zipfile(path):
            raise ValueError("INVALID_XLSX")
        with zipfile.ZipFile(path) as archive:
            if sum(entry.file_size for entry in archive.infolist()) > 200 * 1024 * 1024:
                raise ValueError("WORKBOOK_EXPANSION_LIMIT")
            if len(archive.infolist()) > 10000:
                raise ValueError("WORKBOOK_ENTRY_LIMIT")
        book = openpyxl.load_workbook(path, read_only=False, data_only=False, keep_links=False)
        try:
            for sheet in book:
                if sheet.max_row * sheet.max_column > MAX_CELLS:
                    raise ValueError("WORKBOOK_CELL_LIMIT")
            if sum(s.max_row * s.max_column for s in book) > MAX_CELLS:
                raise ValueError("WORKBOOK_CELL_LIMIT")
            return book
        except BaseException:
            book.close()
            raise
    if path.suffix != ".xls":
        raise ValueError("UNSUPPORTED_FILE_TYPE")
    book = xlrd.open_workbook(path, formatting_info=True)
    if sum(s.nrows * s.ncols for s in book.sheets()) > MAX_CELLS:
        book.release_resources()
        raise ValueError("WORKBOOK_CELL_LIMIT")
    return book


def inspect_workbook(path):
    book = workbook(path)
    try:
        if isinstance(book, openpyxl.Workbook):
            return [
                {
                    "name": s.title,
                    "rows": s.max_row,
                    "columns": s.max_column,
                    "merged": [str(r) for r in s.merged_cells.ranges],
                }
                for s in book
            ]
        return [
            {"name": s.name, "rows": s.nrows, "columns": s.ncols, "merged": [list(r) for r in s.merged_cells]}
            for s in book.sheets()
        ]
    finally:
        book.close() if isinstance(book, openpyxl.Workbook) else book.release_resources()


def read_region(path, sheet_name, region):
    left, top, right, bottom = range_boundaries(region)
    if (
        not all([left, top, right, bottom])
        or min(left, top) < 1
        or right < left
        or bottom < top
        or (right - left + 1) * (bottom - top + 1) > 2000
    ):
        raise ValueError("REGION_LIMIT: use at most 2000 cells")
    book = workbook(path)
    try:
        if isinstance(book, openpyxl.Workbook):
            sheet = book[sheet_name]
            if right > sheet.max_column or bottom > sheet.max_row:
                raise ValueError("REGION_OUT_OF_RANGE")
            return [
                {"cell": cell.coordinate, "value": str(cell.value)[:4000]}
                for row in sheet.iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right)
                for cell in row
                if cell.value is not None
            ]
        sheet = book.sheet_by_name(sheet_name)
        if right > sheet.ncols or bottom > sheet.nrows:
            raise ValueError("REGION_OUT_OF_RANGE")
        return [
            {"row": r + 1, "column": c + 1, "value": str(sheet.cell_value(r, c))[:4000]}
            for r in range(top - 1, bottom)
            for c in range(left - 1, right)
            if sheet.cell_value(r, c) != ""
        ]
    finally:
        book.close() if isinstance(book, openpyxl.Workbook) else book.release_resources()
