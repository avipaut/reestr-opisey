@echo off
rem Разработка: runserver сам перезапускается при правке кода, шаблоны — сразу.
rem В .env — DJANGO_ENV=dev. Обход папки идёт фоновым потоком, обработчик очереди не нужен.
cd /d "%~dp0.."
".venv\Scripts\python.exe" manage.py migrate --noinput
".venv\Scripts\python.exe" manage.py runserver 127.0.0.1:8000
