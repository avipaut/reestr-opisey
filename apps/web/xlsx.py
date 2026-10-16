"""Выгрузка выборки в Excel: лист результата (шапка закреплена, автофильтр,
числа числами, итоговая строка) и лист «Параметры». Рабочих файлов на диске
нет — книга собирается в памяти (граф APCS: debt-export-workdir-never-removed)."""

import io
from decimal import Decimal

import openpyxl
from django.http import HttpResponse
from django.utils import timezone
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

BOLD = Font(bold=True)
HEAD = PatternFill("solid", fgColor="E8EDF3")


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def xlsx_response(title, headers, rows, total, params):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(headers)
    for c in ws[1]:
        c.font, c.fill = BOLD, HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for r in rows:
        ws.append([_num(v) for v in r])
    if total:
        ws.append([_num(v) for v in total])
        for c in ws[ws.max_row]:
            c.font = BOLD
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
    ws.column_dimensions["A"].width = 40
    for i in range(2, len(headers) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 16
        for (c,) in ws.iter_rows(min_row=2, min_col=i, max_col=i):
            if isinstance(c.value, (int, float)):
                c.number_format = "# ##0" if float(c.value).is_integer() else "# ##0.000"
    ps = wb.create_sheet("Параметры")
    ps.append(["Параметр", "Значение"])
    for c in ps[1]:
        c.font, c.fill = BOLD, HEAD
    for p in params:
        ps.append(list(p))
    ps.column_dimensions["A"].width = 22
    ps.column_dimensions["B"].width = 80
    buf = io.BytesIO()
    wb.save(buf)
    name = f"{title} {timezone.localtime():%Y-%m-%d %H-%M}.xlsx"
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    from urllib.parse import quote
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
    return resp
