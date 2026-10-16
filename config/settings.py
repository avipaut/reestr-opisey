"""Настройки. Всё, что зависит от места установки, — из окружения (.env).

По умолчанию — боевой режим: DEBUG выключен, ключ обязателен (граф APCS:
debt-settings-default-development, debt-hardcoded-infra).
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env(path: Path):
    """Простой разбор .env: КЛЮЧ=значение, # — комментарий. Окружение главнее."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env(BASE_DIR / ".env")


def env(key, default=None):
    return os.environ.get(key, default)


def env_list(key, sep=","):
    return [x.strip() for x in (env(key) or "").split(sep) if x.strip()]


DJANGO_ENV = env("DJANGO_ENV", "prod")
DEBUG = DJANGO_ENV == "dev"
SECRET_KEY = env("DJANGO_SECRET_KEY") or ("dev-only-insecure" if DEBUG else None)
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY не задан (боевой режим). Для разработки — DJANGO_ENV=dev в .env")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS") or (["localhost", "127.0.0.1"] if DEBUG else [])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_q",
    "apps.orgs",
    "apps.inventories",
    "apps.imports",
    "apps.stats",
    "apps.web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "apps.web.context.nav",
    ]},
}]

# Ограничение времени запроса — на уровне соединения, для всех запросов
# (граф APCS: debt-search-timeout-never-applied, debt-web-queries-outlive-workers).
# Импорт поднимает его для себя через SET LOCAL внутри своей транзакции.
STATEMENT_TIMEOUT_MS = int(env("DB_STATEMENT_TIMEOUT_MS", "30000"))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "reestr"),
        "USER": env("DB_USER", "reestr"),
        "PASSWORD": env("DB_PASSWORD", ""),
        "HOST": env("DB_HOST", "localhost"),
        "PORT": env("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
# ДЛЯ СЕРВЕРНОЙ ВЕРСИИ: вход по доменным учётным записям — позже (ответ
# пользователя 16.10.2026 «Используем доменные учетки. Но это потом»):
# django-auth-ldap или вход Windows через IIS (REMOTE_USER); выбор — при
# подключении, от того, есть ли IIS перед waitress.

LANGUAGE_CODE = "ru"
TIME_ZONE = "Asia/Bishkek"  # сутки и даты — по местному поясу (граф APCS: debt-daily-view-reads-raw)
USE_I18N = True
USE_TZ = True

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "review_list"
LOGOUT_REDIRECT_URL = "login"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Папки с нормализованными описями (результат модуля коллеги).
INVENTORY_ROOTS = env_list("INVENTORY_ROOTS", ";")
# Листы-списки описи и листы-сведения (формат — запись графа normalizovannaya-opis-format).
INVENTORY_LIST_SHEETS = ["Опись", "Вынесено", "Ручная валидация"]
INVENTORY_EXTENSIONS = [".xlsx", ".xlsm"]

# Фоновые задачи — очередь в самой базе: Celery на Windows не поддерживается,
# а очередь не должна жить в вытесняющем кэше (граф APCS: debt-redis-evicts-queue).
# Расписание — только в базе (Schedule), создаётся командой один раз и
# правится из админки (граф APCS: debt-celery-schedule-split, debt-beat-admin-edits-reverted).
Q_CLUSTER = {
    "name": "reestr",
    "orm": "default",
    "workers": int(env("Q_WORKERS", "2")),
    "timeout": 3600,
    "retry": 4000,
    "max_attempts": 1,
    "catch_up": False,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    # Имена — по модулям приложений (граф APCS: debt-logging-names-mismatch).
    "loggers": {"apps": {"handlers": ["console"], "level": "INFO"}},
}

if not DEBUG:
    SESSION_COOKIE_SECURE = env("HTTPS", "0") == "1"
    CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
