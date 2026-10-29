#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    if len(sys.argv) > 1 and sys.argv[1] == "runserver":
        import config.settings  # noqa: F401  (читает .env)
        from config.devdb import ensure_dev_db

        ensure_dev_db()
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
