#!/bin/sh
# One image, three roles: web (default), worker, beat.
set -e

case "$1" in
  web)
    python manage.py migrate --noinput
    python manage.py ensure_admin
    exec gunicorn config.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-3}" \
      --access-logfile - \
      --forwarded-allow-ips "*"
    ;;
  worker)
    exec celery -A config worker --loglevel "${LOG_LEVEL:-info}" --concurrency "${CELERY_CONCURRENCY:-2}"
    ;;
  beat)
    exec celery -A config beat --loglevel "${LOG_LEVEL:-info}" --schedule /tmp/celerybeat-schedule
    ;;
  *)
    exec "$@"
    ;;
esac
