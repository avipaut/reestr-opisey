"""Боевой запуск на Windows: веб (waitress) и обработчик очереди (qcluster)
двумя процессами; упал один — перезапускается, Ctrl+C — гасит оба.

    .venv\Scripts\python deploy\serve.py            # или deploy\start.cmd

Адрес — SERVER_HOST / SERVER_PORT из .env (по умолчанию 127.0.0.1:8000:
только этот компьютер; открыть в сеть — SERVER_HOST=0.0.0.0 и имя сервера
в DJANGO_ALLOWED_HOSTS).
ДЛЯ СЕРВЕРНОЙ ВЕРСИИ: постоянная работа — две службы Windows (WinSW или
NSSM) с теми же командами, deploy/README.md; ставит администратор.
"""

import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from config.settings import env  # noqa: E402  (.env читается там)

PY = sys.executable
HOST, PORT = env("SERVER_HOST", "127.0.0.1"), env("SERVER_PORT", "8000")
CMDS = {
    "веб": [PY, "-m", "waitress", f"--listen={HOST}:{PORT}", "--threads=8", "config.wsgi:application"],
    "очередь": [PY, "manage.py", "qcluster"],
}


def start(name):
    log = open(os.path.join(ROOT, "logs", f"{'web' if name == 'веб' else 'qcluster'}.log"), "a", encoding="utf-8")
    return subprocess.Popen(CMDS[name], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)


def main():
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    # Расписания обхода нет: описи загружаются только вручную (решение пользователя
    # 16.10.2026 «Расписание тоже вырубай. Все только вручную»). Включить — manage.py setup_schedule.
    for cmd in (["migrate", "--noinput"], ["collectstatic", "--noinput", "-v", "0"]):
        subprocess.run([PY, "manage.py", *cmd], cwd=ROOT, check=True)
    procs = {n: start(n) for n in CMDS}
    print(f"Реестр описей: http://{HOST}:{PORT}/ (журналы — logs\). Остановить — Ctrl+C.", flush=True)
    try:
        while True:
            time.sleep(5)
            for n, p in procs.items():
                if p.poll() is not None:
                    print(f"[!] процесс «{n}» завершился с кодом {p.returncode} — перезапуск", flush=True)
                    procs[n] = start(n)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs.values():
            p.terminate()


if __name__ == "__main__":
    main()
