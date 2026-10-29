# Реестр описей

Веб-приложение: автоимпорт нормализованных описей из сетевой папки в
PostgreSQL, просмотр и приём новых версий, свод по кодам учёта и
учреждениям, история «на дату». Django 5.2 LTS, PostgreSQL, django-q2,
waitress; сервер — Windows.

## Знания — в общем графе

ТЗ, решения, вопросы и ответы — в **общем графе** `C:/vs code/inside/knowledge`
(решение пользователя 16.10.2026: «Отдельные репо и используем вообще для
этого общий граф»). Своего `knowledge/` здесь нет.

* ТЗ — запись `tz-reestr-opisey`; формат описи — `normalizovannaya-opis-format`;
  требования по ошибкам APCS — `oshibki-apcs-uchtennye-v-reestre-opisey`;
  раздел — `karta-razdela-reestr-opisey`.
* Поиск: `python "C:/vs code/inside/tools/knowledge/graph.py" find <слова>`.
* Новое решение или ответ пользователя — запись в общем графе и коммит там же
  (с правилами того репозитория: строка в его журнале изменений).

## Правила (из общего графа, коротко)

* Отвечать по-русски.
* Данные проекта наружу не уходят; `git push` — только по прямой просьбе.
* Коммит без `Co-Authored-By`; последняя строка описания —
  «Подготовлено в Claude Code по задаче Осминнова Н.»
* Ветки не создавать без указания.
* Ничего не удалять без прямого указания.
* Описи — данные пользователя: в репозиторий не кладутся; проверки строят
  книги сами (`apps/imports/tests`).

## Разработка

    .venv\Scripts\python manage.py runserver        # DJANGO_ENV=dev в .env
    .venv\Scripts\python manage.py scan_inventories # обход папок INVENTORY_ROOTS
    .venv\Scripts\python manage.py test apps

База разработки — свой экземпляр PostgreSQL 17 на порту 5433, данные в
`.dev/pgdata` (вне git), программы — `%LOCALAPPDATA%\semsearch\pgsql\bin`:

    pg_ctl -D .dev/pgdata -o "-p 5433 -c listen_addresses=localhost" -l .dev/pg.log start

`manage.py runserver` поднимает её сам, если она не отвечает (DJANGO_ENV=dev,
DB_HOST — localhost, есть `.dev/pgdata`), и ждёт готовности —
`config/devdb.py`. pg_ctl — из PG_BIN в .env, PATH или папки выше. База
остаётся работать и после остановки runserver.
