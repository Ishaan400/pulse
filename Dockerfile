FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system pulse && adduser --system --ingroup pulse pulse

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Static files are collected at build time (no DB or secrets needed).
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && chmod +x deploy/entrypoint.sh \
    && chown -R pulse:pulse /app

USER pulse
EXPOSE 8000
ENTRYPOINT ["deploy/entrypoint.sh"]
CMD ["web"]
