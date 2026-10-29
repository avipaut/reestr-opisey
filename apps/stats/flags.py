"""Флаги «учитывать» — одно правило для всех чисел интерфейса (решение пользователя
29.10.2026: «адаптировать все сборы статистики, все числа в интерфейсе под флаги. Ибо
сейчас вообще непонятно что и как считается»).

Позиция описи бывает четырёх видов, которые в свод по умолчанию не входят:

* повтор строки другой описи той же группы похожих описей (apps.imports.dedup);
* строка устаревшей описи — в группе есть опись более нового года;
* мера — материалы и запасы (литры, тонны, дробное количество);
* подозрительная стоимость, пока не подтверждена на вкладке «Подозрительные».

Флаг включает вид в счёт. Флаги свои на каждой странице (выбор пользователя 29.10.2026)
и живут в адресе: ?inc=dup&inc=old. Без флагов — свод как прежде.
"""

from django.db.models import Q

# (ключ, подпись, подсказка)
FLAGS = [
    ("dup", "Повторы строк", "Строка дороже 100 000 сом, которая повторяет строку другой описи "
                             "того же учреждения (тот же инв. номер или наименование, цена, количество)"),
    ("old", "Устаревшие описи", "Опись группы похожих описей, чей год старше самого нового года группы"),
    ("measure", "Меры", "Материалы и запасы: литры, тонны, дробное количество"),
    ("suspect", "Подозрительные", "Подозрительная стоимость, не подтверждённая на вкладке «Подозрительные»"),
]
KEYS = [k for k, _, _ in FLAGS]
LABELS = {k: label for k, label, _ in FLAGS}


class Flags:
    def __init__(self, include=()):
        self.inc = [k for k in KEYS if k in set(include)]

    @classmethod
    def from_request(cls, request, force=()):
        """Флаги страницы; force — виды, которые отбор страницы просит показать сам
        (например, «Дубли описей: повторы» на поиске позиций)."""
        return cls([*request.GET.getlist("inc"), *force])

    def __contains__(self, key):
        return key in self.inc

    @property
    def default(self):
        return not self.inc

    @property
    def everything(self):
        return len(self.inc) == len(KEYS)

    def item_q(self, prefix=""):
        """Условие на позиции (Item); prefix — путь до позиции, например «items__»."""
        q = Q()
        if "dup" not in self:
            q &= Q(**{f"{prefix}dup_of__isnull": True})
        if "old" not in self:
            q &= Q(**{f"{prefix}inventory__outdated_by__isnull": True})
        if "measure" not in self:
            q &= Q(**{f"{prefix}excluded": False})
        if "suspect" not in self:
            # Подозрительная — отложенная и не мера; мера с пометкой «отложена» — это мера.
            q &= Q(**{f"{prefix}held": False}) | Q(**{f"{prefix}excluded": True})
        return q

    def stat_q(self, prefix=""):
        """Условие на строки свода версий (VersionStat)."""
        q = Q()
        for key, field in (("dup", "dup"), ("measure", "measure"), ("suspect", "suspect")):
            if key not in self:
                q &= Q(**{f"{prefix}{field}": False})
        if "old" not in self:
            q &= Q(**{f"{prefix}inventory__outdated_by__isnull": True})
        return q

    def summary(self):
        """Строка для страницы и листа «Параметры»."""
        on = [LABELS[k].lower() for k in self.inc]
        off = [LABELS[k].lower() for k in KEYS if k not in self]
        if not off:
            return "Учтены все позиции, включая " + ", ".join(on)
        return ("Не учтены: " + ", ".join(off)) + ("; учтены: " + ", ".join(on) if on else "")

    def params(self):
        return [("Учитывать", ", ".join(LABELS[k] for k in self.inc) or "только свод (без повторов, "
                 "устаревших описей, мер и подозрительных)")]

    def query(self):
        """Часть адреса: inc=dup&inc=old."""
        from urllib.parse import urlencode
        return urlencode([("inc", k) for k in self.inc])

    def choices(self):
        return [{"key": k, "label": label, "tip": tip, "on": k in self} for k, label, tip in FLAGS]


DEFAULT = Flags()
