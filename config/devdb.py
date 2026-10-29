"""База разработки поднимается сама при `manage.py runserver`.

Если в .env DJANGO_ENV=dev, база на этом компьютере (DB_HOST — localhost)
и не отвечает, а рядом лежит свой экземпляр PostgreSQL (.dev/pgdata), —
запускаем его через pg_ctl и ждём, пока он начнёт принимать подключения;
только потом runserver идёт дальше. Боевой запуск (deploy/serve.py) этого
не делает: там база — служба, и её поднимает администратор.

pg_ctl ищется так: PG_BIN из .env, затем PATH, затем
%LOCALAPPDATA%\\semsearch\\pgsql\\bin. Запущенная база живёт и после
остановки runserver; погасить —
    pg_ctl -D .dev/pgdata stop
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PGDATA = ROOT / ".dev" / "pgdata"
WAIT_S = 60


def _alive(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _pg_ctl():
    exe = "pg_ctl.exe" if os.name == "nt" else "pg_ctl"
    if os.environ.get("PG_BIN"):
        p = Path(os.environ["PG_BIN"]) / exe
        if p.exists():
            return str(p)
    found = shutil.which("pg_ctl")
    if found:
        return found
    p = Path(os.environ.get("LOCALAPPDATA", "")) / "semsearch" / "pgsql" / "bin" / exe
    return str(p) if p.exists() else None


def ensure_dev_db():
    # Под автоперезапуском runserver выполняется дважды: проверяет только
    # внешний процесс, дочерний (RUN_MAIN) застаёт базу уже поднятой.
    if os.environ.get("RUN_MAIN") or os.environ.get("DJANGO_ENV") != "dev":
        return
    host = os.environ.get("DB_HOST", "localhost")
    port = int(os.environ.get("DB_PORT", "5432"))
    if host not in ("localhost", "127.0.0.1", "::1") or _alive(host, port):
        return
    if not PGDATA.is_dir():
        print(f"[база] {host}:{port} не отвечает, а своей базы {PGDATA} нет — поднимите PostgreSQL сами.", file=sys.stderr)
        return
    pg_ctl = _pg_ctl()
    if not pg_ctl:
        print("[база] pg_ctl не найден: укажите PG_BIN в .env.", file=sys.stderr)
        return
    print(f"[база] {host}:{port} не отвечает — запускаю {PGDATA} …", flush=True)
    subprocess.run(
        [pg_ctl, "-D", str(PGDATA), "-o", f"-p {port} -c listen_addresses=localhost",
         "-l", str(PGDATA.parent / "pg.log"), "start", "-w", "-t", str(WAIT_S)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # pg_ctl -w ждёт готовности сам; порт проверяем ещё раз — после
    # нештатного выключения база сначала восстанавливается.
    deadline = time.monotonic() + WAIT_S
    while not _alive(host, port):
        if time.monotonic() > deadline:
            sys.exit(f"[база] не поднялась за {WAIT_S} с — смотрите {PGDATA.parent / 'pg.log'}")
        time.sleep(0.5)
    print("[база] готова.", flush=True)
