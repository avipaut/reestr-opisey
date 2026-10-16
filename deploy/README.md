# Развёртывание на Windows

1. PostgreSQL 15+; база и пользователь — в `.env` (`DB_*`).
2. `python -m venv .venv` и `.venv\Scripts\pip install -r requirements.txt`.
3. `.env` по образцу `.env.example`: `DJANGO_ENV=prod`, свой `DJANGO_SECRET_KEY`,
   `DJANGO_ALLOWED_HOSTS` (имя сервера), `INVENTORY_ROOTS` (путь UNC к папке
   результата модуля коллеги), `SERVER_HOST` / `SERVER_PORT`.
4. Первый пользователь: `.venv\Scripts\python manage.py createsuperuser`.
5. Запуск: `deploy\start.cmd` — миграции, статика, расписание обхода (создаётся
   один раз, дальше правится в админке: «Django Q → Scheduled tasks»), затем
   веб (waitress) и обработчик очереди (qcluster). Журналы — `logs\`.

Постоянная работа — две службы Windows с командами из `deploy/serve.py`
(`CMDS`) или одна служба на `deploy\start.cmd` (WinSW / NSSM), от учётной
записи с правом чтения сетевой папки. Ставит администратор сервера.

Резервная копия базы — `pg_dump -Fc` по расписанию и проверка восстановления
в пустую базу (граф: oshibki-apcs-uchtennye-v-reestre-opisey, Э2) — настроить
вместе со службами.
