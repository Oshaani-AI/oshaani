#!/bin/sh
set -e

if [ "${WAIT_FOR_DB:-true}" = "true" ]; then
  echo "Waiting for database..."
  python - <<'PY'
import os
import sys
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oshani.settings")

for attempt in range(1, 31):
    try:
        import django
        django.setup()
        from django.db import connection
        connection.ensure_connection()
        print("Database is ready.")
        break
    except Exception as exc:
        print(f"Database not ready (attempt {attempt}/30): {exc}")
        time.sleep(2)
else:
    sys.exit("Database unavailable after 60 seconds.")
PY
fi

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Running migrations..."
  python manage.py migrate --noinput
fi

if [ "${COLLECT_STATIC:-true}" = "true" ]; then
  echo "Collecting static files..."
  python manage.py collectstatic --noinput
fi

if [ "${CREATE_SUPERUSER:-false}" = "true" ]; then
  echo "Ensuring initial admin user exists..."
  python manage.py createsuperuser --noinput 2>/dev/null || echo "Admin user already exists."
fi

if [ "${OLLAMA_WARMUP_ON_START:-false}" = "true" ]; then
  echo "Preloading Ollama model in background..."
  python manage.py warm_ollama &
fi

exec "$@"
