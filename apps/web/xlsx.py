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


ITEM_COLUMNS = [
    ("Учреждение", lambda i: i.inventory.node.trail if i.inventory.node_id else ""),
    ("Лист", lambda i: i.sheet), ("Строка", lambda i: i.row_no), ("Основное средство", lambda i: i.name),
    ("Инв. номер", lambda i: i.inv_no), ("Код учёта", lambda i: i.account_code),
    ("Рекомендуемый код", lambda i: i.recommended_code), ("Статус", lambda i: str(i.status or "")),
    ("Статус по описи", lambda i: i.status_doc), ("Приоритет", lambda i: i.priority),
    ("Количество, шт.", lambda i: i.qty), ("Количество — пометка", lambda i: i.qty_note),
    ("Исключено из свода", lambda i: i.exclude_reason),
    ("Первонач. стоимость, сом", lambda i: i.cost_initial),
    ("Накоп. износ, сом", lambda i: i.depreciation), ("Балансовая стоимость, сом", lambda i: i.cost_balance),
    ("Модель", lambda i: i.model), ("Тип объекта", lambda i: str(i.object_type or "")),
    ("Причина", lambda i: i.reason), ("Комментарий", lambda i: i.comment),
    ("Полностью амортизирован", lambda i: {True: "Да", False: "Нет"}.get(i.fully_depreciated, "")),
    ("Подразделение", lambda i: i.subdivision), ("МОЛ", lambda i: i.mol),
    ("Отметка пользователя", lambda i: i.user_mark), ("Заливка наименования", lambda i: i.name_fill),
]


def items_xlsx_response(title, qs, params, request):
    """Позиции — потоковой записью (write_only): объём выборки не ограничен,
    память не растёт (граф: vygruzka-i-otchety-bez-potolkov-obema-…)."""
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet(title[:31])
    ws.freeze_panes = "A2"
    ws.append([h for h, _ in ITEM_COLUMNS])
    n = 0
    for it in qs.iterator(chunk_size=2000):
        ws.append([_num(fn(it)) for _, fn in ITEM_COLUMNS])
        n += 1
    ws.auto_filter.ref = f"A1:{get_column_letter(len(ITEM_COLUMNS))}{n + 1}"
    ps = wb.create_sheet("Параметры")
    for p in [("Параметр", "Значение"), ("Вид", title), *params, ("Позиций", n),
              ("Кто выгрузил", request.user.get_username()),
              ("Когда", timezone.localtime().strftime("%d.%m.%Y %H:%M"))]:
        ps.append(list(p))
    buf = io.BytesIO()
    wb.save(buf)
    from urllib.parse import quote
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(title + timezone.localtime().strftime(' %Y-%m-%d %H-%M') + '.xlsx')}"
    return resp
