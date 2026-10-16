"""Узел по пути папки (ТЗ 1.4, 1.8).

Решение пользователя 16.10.2026: учреждения и подведы хранятся как коды и
наименования, «если они есть»: «126» — код без имени, «8. Ошское ДНВТ» —
код и имя, «ГУ Дирекция по управлению…» — имя без кода. Любая папка —
узел, кроме служебных («обработанные», «архив», годы): книги в служебной
папке относятся к узлу папки выше («Узлы не распознало. исправляй. Раскидай
по узлам»).
"""

import re

from django.conf import settings

_CODE = re.compile(r"^\s*(\d{1,6})\s*(?:[.)\-–—]\s*|\s+|$)(.*)$")
_YEAR = re.compile(r"^\s*(19|20)\d{2}\s*(г\.?|год|года)?\s*$", re.I)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower().replace("ё", "е"))


def is_service(name: str) -> bool:
    n = _norm(name)
    return bool(_YEAR.match(n)) or n in {_norm(x) for x in getattr(settings, "SERVICE_FOLDERS", [])}


def parse_folder(name: str):
    """(код, имя): «8. Ошское ДНВТ» → ('8', 'Ошское ДНВТ'); «126» → ('126', '');
    «ГУ Дирекция» → ('', 'ГУ Дирекция')."""
    m = _CODE.match(name)
    if not m:
        return "", name.strip()
    return m.group(1), m.group(2).strip()


def parse_path(parts):
    """Части пути от корня → [{'code', 'name', 'folder'}] без служебных папок."""
    out = []
    for p in parts:
        if is_service(p):
            continue
        code, name = parse_folder(p)
        out.append({"code": code, "name": name, "folder": p})
    return out
