@echo off
rem Запуск реестра описей: веб + обработчик очереди. Остановить — Ctrl+C.
cd /d "%~dp0.."
".venv\Scripts\python.exe" deploy\serve.py
