"""Проверка каждой строки описей (пользователь 28.10.2026: «Надо подобное как то
валидировать. Прошерсти базу на подобные моменты. надо буквально анализировать каждую
строку»; «Я про алгоритмы»). Повод — автобус БГТ 01 KG 480 BA: в одной описи он —
отдельная строка, в трёх других — строка «пачкой» с десятью госномерами, в одной из
них цена в тысячу раз больше, в другой — ноль.

Две части:

1. Строка сама по себе (row_flags): несколько госномеров или VIN в наименовании —
   «пачка»; количество в тексте («10 шт») расходится с полем; балансовая не равна
   первоначальной минус износ; износ больше первоначальной; отрицательные суммы.
2. Строки учреждения между собой (по узлу): объект, опознанный наверняка — госномер,
   VIN, инв. номер, — встречается в другой актуальной описи учреждения или дважды в
   одной. Для пары — как соотносятся цены: равны, одна ноль, отличаются в 1000 раз
   (сдвиг запятой), другие. Пачка — все её госномера есть отдельными строками в другой
   описи, и сумма их цен равна цене пачки или нет.

Сравниваются только строки, которые сейчас в своде (не повторы и не из устаревших
описей): двойной счёт — среди них. Модуль ничего не пишет — analyze() возвращает
счётчики и примеры; что из этого станет правилом импорта, решает пользователь.
"""

import re
from collections import defaultdict
from decimal import Decimal

from apps.inventories.models import Inventory, Item

# Латиница и кириллица одинаковы: в описях «01KG027AХ» — с кириллической «Х».
HOMO = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")
# Госномер КР: «01 KG 480 BA», «01KG480BA», «-01KG480BA», «01KG480ВA».
PLATE = re.compile(r"(?<![0-9])(\d{2})\s*-?\s*KG\s*-?\s*(\d{3})\s*-?\s*([A-Z]{2,3})(?![A-Z])")
# Номер по старому образцу встречается только после слова «гос»: «гос.№9075 ВС».
PLATE_OLD = re.compile(r"ГОС\.?\s*(?:НОМЕР|НОМ\.?|№|N)?\s*[-:]?\s*([A-Z]?\s?\d{4}\s?[A-Z]{2,3})(?![A-Z0-9])")
VIN = re.compile(r"(?<![A-Z0-9])([A-HJ-NPR-Z0-9]{17})(?![A-Z0-9])")
QTY_TEXT = re.compile(r"(?<![\d.,])(\d{1,5})\s*(?:шт|штук|ед\.|единиц|компл)", re.I)

ONE = Decimal("1")


def ids_of(name):
    """(госномера, VIN) в наименовании — множества нормализованных строк."""
    up = (name or "").upper().translate(HOMO)
    plates = {"".join(m.groups()) for m in PLATE.finditer(up)}
    plates |= {re.sub(r"\s", "", m.group(1)) for m in PLATE_OLD.finditer(up)}
    vins = {v for v in VIN.findall(up) if re.search(r"\d", v) and re.search(r"[A-Z]", v)}
    return plates, vins


def row_flags(name, qty, ci, dep, cb):
    """Отметки строки самой по себе."""
    out = []
    plates, vins = ids_of(name)
    if len(plates) > 1 or len(vins) > 1:
        out.append("пачка")
    m = QTY_TEXT.search(name or "")
    if m and qty is not None and Decimal(m.group(1)) != qty and Decimal(m.group(1)) > 1:
        out.append("количество в тексте")
    if any(v is not None and v < 0 for v in (ci, dep, cb)):
        out.append("отрицательная сумма")
    if ci and dep and dep > ci:
        out.append("износ больше первонач.")
    if ci is not None and cb is not None and dep is not None and abs(ci - dep - cb) > ONE:
        out.append("балансовая ≠ первонач. − износ")
    return out, plates, vins


def price_relation(a, b):
    """Как соотносятся цены одного объекта в двух описях."""
    a, b = a or Decimal(0), b or Decimal(0)
    if a == b:
        return "цена равна"
    if not a or not b:
        return "одна цена — ноль"
    r = max(a, b) / min(a, b)
    for k in (10, 100, 1000, 10000):
        if abs(r - k) / k < Decimal("0.001"):
            return f"цены отличаются в {k} раз"
    return "цена другая"


FIELDS = ("id", "inventory_id", "name", "qty", "cost_initial", "depreciation", "cost_balance", "inv_key", "held")

# Итоговая строка ведомости, а не имущество: «Итого», «Всего», по-кыргызски «Жыйынтыгы»,
# «Бардыгы», «Жалпы»; «в том числе». Повод — в своде строки «Жыйынтыгы:» (662 млн),
# «Основные фонды», «Активы и обязательства» среди самых дорогих.
TOTAL_WORD = re.compile(r"^\W*(итого|итог|всего|в\s*т\.?\s*ч|в\s+том\s+числе|жыйынтык\w*|бардыгы\w*|жалпы|total|сальдо)",
                        re.I)


def total_rows(seq):
    """Итоговые строки листа. seq — [(id, стоимость)] в порядке строк. Строка — итог, если
    её стоимость равна сумме идущих подряд строк (не меньше двух) прямо над ней или прямо
    под ней, или сумме всех остальных строк листа. Возвращает {id: (как, сколько строк)}."""
    n = len(seq)
    c = [v or Decimal(0) for _, v in seq]
    P = [Decimal(0)]
    for v in c:
        P.append(P[-1] + v)
    last = {}  # значение префикса → последний индекс
    for k, v in enumerate(P):
        last[v] = k
    out, seen = {}, {}
    for i in range(n):
        if i >= 2:
            seen.setdefault(P[i - 2], i - 2)
        if c[i] <= 0:
            continue
        if n >= 3 and c[i] == P[n] - c[i]:
            out[seq[i][0]] = ("сумма всех остальных строк", n - 1)
            continue
        j = seen.get(P[i] - c[i])
        if j is not None:
            out[seq[i][0]] = ("сумма строк над ней", i - j)
            continue
        k = last.get(P[i + 1] + c[i])
        if k is not None and k >= i + 3:
            out[seq[i][0]] = ("сумма строк под ней", k - i - 1)
    return out


def analyze(samples=5, progress=None):
    """Пройти каждую строку актуальных описей. Возвращает {отметка: [строк, сумма, примеры]}."""
    stat = defaultdict(lambda: [0, Decimal(0), []])

    def note(flag, row, extra=""):
        s = stat[flag]
        s[0] += 1
        s[1] += row[4] or 0
        if len(s[2]) < samples:
            s[2].append((row[0], row[1], row[2][:160], row[3], row[4], extra))

    counted = Inventory.objects.filter(is_current=True, outdated_by__isnull=True)
    nodes = list(counted.values_list("node_id", flat=True).distinct())
    total = 0
    for done, node_id in enumerate(nodes, 1):
        invs = list(counted.filter(node_id=node_id).values_list("id", flat=True))
        rows = list(Item.objects.filter(inventory_id__in=invs, dup_of__isnull=True, excluded=False)
                    .values_list(*FIELDS).iterator(chunk_size=20000))
        total += len(rows)
        # Итоговые строки — по листу в порядке строк, среди всех строк описи.
        seqs = defaultdict(list)
        for rid, inv, sheet, cost in (Item.objects.filter(inventory_id__in=invs)
                                      .order_by("inventory_id", "sheet", "row_no")
                                      .values_list("id", "inventory_id", "sheet", "cost_initial").iterator(chunk_size=20000)):
            seqs[(inv, sheet)].append((rid, cost))
        totals = {}
        for seq in seqs.values():
            totals.update(total_rows(seq))
        del seqs
        for row in rows:
            word = TOTAL_WORD.match(row[2] or "")
            how = totals.get(row[0])
            if word:
                note("итог: по слову" + (", и сумма сходится" if how else ""), row, word.group(1))
            elif how:
                size = "2 строк" if how[1] == 2 else "3–9 строк" if how[1] < 10 else "10 и больше строк"
                note(f"итог: {how[0]} ({size})", row, f"{how[1]} строк")
        by_id = defaultdict(list)  # ("госномер"|"VIN"|"инв. номер", значение) → строки
        packs = []
        for row in rows:
            flags, plates, vins = row_flags(row[2], row[3], row[4], row[5], row[6])
            for f in flags:
                note(f, row)
            if "пачка" in flags:
                packs.append((row, plates))
                continue
            for p in plates:
                by_id[("госномер", p)].append(row)
            for v in vins:
                by_id[("VIN", v)].append(row)
            if row[7]:
                by_id[("инв. номер", row[7])].append(row)
        # Один объект в нескольких строках.
        seen = set()
        for (kind, val), group in by_id.items():
            if len(group) < 2:
                continue
            for row in group:
                if row[0] in seen and kind != "инв. номер":
                    continue
                others = [r for r in group if r[0] != row[0]]
                same_inv = [r for r in others if r[1] == row[1]]
                other_inv = [r for r in others if r[1] != row[1]]
                if same_inv and kind != "инв. номер":
                    note(f"{kind}: дважды в одной описи", row, val)
                if other_inv:
                    rel = price_relation(row[4], other_inv[0][4])
                    note(f"{kind}: в другой описи, {rel}", row, f"{val}; там {other_inv[0][4]}")
                if kind != "инв. номер":
                    seen.add(row[0])
        # Пачка: все её госномера — отдельными строками в другой описи?
        singles = {p: [r for r in by_id.get(("госномер", p), [])] for _, ps in packs for p in ps}
        for row, plates in packs:
            found = defaultdict(list)
            for p in plates:
                for r in singles.get(p, []):
                    if r[1] != row[1]:
                        found[r[1]].append(r)
            full = [(inv, rs) for inv, rs in found.items() if len({p for r in rs for p in ids_of(r[2])[0]} & plates) == len(plates)]
            if full:
                inv, rs = full[0]
                s = sum((r[4] or 0) for r in rs)
                rel = "сумма равна" if s == (row[4] or 0) else price_relation(row[4], s).replace("цена", "сумма").replace("цены", "суммы")
                note(f"пачка: все госномера есть поштучно в другой описи, {rel}", row, f"{len(plates)} шт.; поштучно {s}")
            elif found:
                note("пачка: часть госномеров есть поштучно в другой описи", row, f"{len(plates)} шт.")
        if progress:
            progress(done, len(nodes))
    return total, dict(stat)

