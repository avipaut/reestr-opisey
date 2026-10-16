"""Узел по пути папки (ТЗ 1.4, 1.8): «126» → код 126; «8. Ошское ДНВТ» →
код 8, имя «Ошское ДНВТ». Разбор терпимый: «<код>. <имя>», «<код>», «<код> <имя>»."""

import re

_RX = re.compile(r"^\s*(\d+)\s*(?:[.)\-–—]\s*|\s+|$)(.*)$")


def parse_folder(name: str):
    """(код, имя) или (None, имя) — если кода нет."""
    m = _RX.match(name)
    if not m:
        return None, name.strip()
    return m.group(1), m.group(2).strip()


def parse_path(parts):
    """Список частей пути → [{'code', 'name', 'folder'}]; ok=False, если у какой-то части нет кода."""
    out, ok = [], True
    for p in parts:
        code, name = parse_folder(p)
        if code is None:
            ok = False
        out.append({"code": code or "", "name": name, "folder": p})
    return out, ok
