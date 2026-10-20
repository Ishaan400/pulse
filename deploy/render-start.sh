#!/bin/sh
# Render free plan: one service runs the web app AND the SLA checker.
# (Background workers aren't available on Render's free plan.)
set -e

python manage.py migrate --noinput
python manage.py ensure_admin

# Celery worker with embedded beat, single process to fit in 512 MB.
# Broker is the PostgreSQL database itself. While the free service sleeps, checks pause;
# the first run after waking catches every overdue incident.
celery -A config worker -B --pool solo --loglevel "${LOG_LEVEL:-info}" \
  --schedule /tmp/celerybeat-schedule &

exec gunicorn config.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${GUNICORN_WORKERS:-2}" \
  --timeout 90 \
  --access-logfile -
