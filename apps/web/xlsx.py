"""Выгрузка выборки в Excel: лист результата (шапка закреплена, автофильтр,
числа числами, итоговая строка) и лист «Параметры». Рабочих файлов на диске
нет — книга собирается в памяти (граф APCS: debt-export-workdir-never-removed)."""

import io
import re
import zipfile
from decimal import Decimal
from xml.sax.saxutils import escape

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


ITEM_COLUMNS = [  # первый столбец выгрузки — «Учреждение» (цепочка узла описи)
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


def items_xlsx_response(title, qs, params, request, trails):
    """Позиции — потоковой записью: объём выборки не ограничен, память не растёт
    (граф: vygruzka-i-otchety-bez-potolkov-obema-…). trails — {опись: цепочка узла}."""
    headers = ["Учреждение", *(h for h, _ in ITEM_COLUMNS)]
    count = [0]

    def rows():
        for it in qs.iterator(chunk_size=2000):
            count[0] += 1
            yield [trails.get(it.inventory_id, ""), *(fn(it) for _, fn in ITEM_COLUMNS)]

    def params_rows():  # число позиций известно, когда лист позиций уже записан
        return [("Параметр", "Значение"), ("Вид", title), *params, ("Позиций", count[0]),
                ("Кто выгрузил", request.user.get_username()),
                ("Когда", timezone.localtime().strftime("%d.%m.%Y %H:%M"))]

    return xlsx_file(title, write_xlsx([(title, headers, rows, {"filter": True, "widths": {1: 40, 4: 40}}),
                                        ("Параметры", None, params_rows, {"widths": {1: 26, 2: 80}})]))


def xlsx_file(title, data):
    """Книга — файлом «<название> <дата время>.xlsx»."""
    from urllib.parse import quote
    resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(title + timezone.localtime().strftime(' %Y-%m-%d %H-%M') + '.xlsx')}"
    return resp


# ------------------------------------------------------------------ быстрая запись книги
# openpyxl пишет ячейку за ~10 мкс — опись на 27 тыс. строк выгружалась 10 с.
# Здесь лист пишется строками XML сразу в архив: втрое-вчетверо быстрее.

_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_BAD_TITLE = re.compile(r"[\[\]:*?/\\]")
_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_NS_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_STYLES = (_HEAD + f'<styleSheet {_NS}><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
           '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
           '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
           '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
           '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
           '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
           '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
           '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def _col(i):
    """Номер столбца с 1 → буквы: 1 → A, 27 → AA."""
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _xml_cell(ref, v, style=""):
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return f'<c r="{ref}"{style} t="b"><v>{int(v)}</v></c>'
    if isinstance(v, (int, float, Decimal)):
        return f'<c r="{ref}"{style}><v>{v}</v></c>'
    s = escape(_BAD_XML.sub("", str(v)))
    return f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">{s}</t></is></c>'


def sheet_title(title, taken=()):
    t = _BAD_TITLE.sub(" ", title).strip("' ")[:31] or "Лист"
    base, n = t, 2
    while t.lower() in taken:
        t = f"{base[:28]} {n}"
        n += 1
    return t


def write_xlsx(sheets):
    """Книга из листов [(название, шапка | None, строки(), {filter, widths})]: строки —
    функция, отдающая списки значений; шапка — жирная, закреплена. Возвращает байты."""
    buf = io.BytesIO()
    titles, filters = [], []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for si, (title, headers, rows, opt) in enumerate(sheets, 1):
            titles.append(sheet_title(title, {t.lower() for t in titles}))
            ncols = len(headers) if headers else 2
            cols = [_col(i) for i in range(1, ncols + 1)]
            with z.open(f"xl/worksheets/sheet{si}.xml", "w") as f:
                f.write((_HEAD + f"<worksheet {_NS}>").encode())
                if headers:
                    f.write(b'<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
                            b'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
                widths = {**{i: 16 for i in range(1, ncols + 1)}, **opt.get("widths", {})}
                f.write(("<cols>" + "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
                                            for i, w in sorted(widths.items())) + "</cols><sheetData>").encode())
                r, out = 0, []
                if headers:
                    r = 1
                    out.append('<row r="1">' + "".join(_xml_cell(f"{c}1", h, ' s="1"') for c, h in zip(cols, headers))
                               + "</row>")
                for values in rows():
                    r += 1
                    if len(values) > len(cols):
                        cols += [_col(i) for i in range(len(cols) + 1, len(values) + 1)]
                    out.append(f'<row r="{r}">' + "".join(_xml_cell(f"{c}{r}", v) for c, v in zip(cols, values))
                               + "</row>")
                    if len(out) >= 1000:
                        f.write("".join(out).encode())
                        out = []
                f.write("".join(out).encode())
                f.write(b"</sheetData>")
                if headers and opt.get("filter"):
                    last = (cols[len(headers) - 1], max(r, 1))
                    f.write(f'<autoFilter ref="A1:{last[0]}{last[1]}"/>'.encode())
                    filters.append((si - 1, last))
                f.write(b"</worksheet>")
        n = len(sheets)
        z.writestr("[Content_Types].xml", _HEAD + (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                      'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                      for i in range(1, n + 1)) + "</Types>"))
        z.writestr("_rels/.rels", _HEAD + (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>'))
        z.writestr("xl/_rels/workbook.xml.rels", _HEAD + (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                      f'relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
            + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
              'relationships/styles" Target="styles.xml"/></Relationships>'))
        quoted = lambda t: "'" + t.replace("'", "''") + "'"  # noqa: E731 — имя листа в формуле
        names = "".join(f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
                        f"{escape(quoted(titles[i]))}!$A$1:${col}${row}</definedName>"
                        for i, (col, row) in filters)
        z.writestr("xl/workbook.xml", _HEAD + (
            f"<workbook {_NS} {_NS_R}><sheets>"
            + "".join(f'<sheet name="{escape(t, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
                      for i, t in enumerate(titles, 1))
            + "</sheets>" + (f"<definedNames>{names}</definedNames>" if names else "") + "</workbook>"))
        z.writestr("xl/styles.xml", _STYLES)
    return buf.getvalue()
