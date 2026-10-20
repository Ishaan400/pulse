# Pulse

Production incident tracking with SLA monitoring. Engineers report incidents, managers put one or more engineers on them, any assigned engineer drives the incident through its lifecycle, and a background job flags incidents that miss their resolution deadline.

**Stack:** Django 5.1 · Django REST Framework · PostgreSQL 16 · Celery + RabbitMQ (worker and beat) · Prometheus · vanilla-JS web console · optional Groq-powered incident analysis.

---

## Deploy (Docker Compose)

On any Linux server with Docker:

```bash
git clone <your repo> pulse && cd pulse
cp .env.example .env
nano .env        # fill in every blank value (see below)
docker compose up -d --build
docker compose ps            # web should become "healthy"
```

Open `http://<server>:8000` and sign in with `PULSE_ADMIN_USERNAME` / `PULSE_ADMIN_PASSWORD`.

What starts:

| Service    | Role |
|------------|------|
| `db`       | PostgreSQL 16, data in the `pgdata` volume |
| `rabbitmq` | Celery broker |
| `web`      | Runs migrations, creates the first admin, serves the API + UI with gunicorn on :8000 |
| `worker`   | Celery worker that runs the SLA check |
| `beat`     | Schedules the SLA check every `PULSE_SLA_CHECK_SECONDS` (default 60) |
| `prometheus` | Scrapes `/metrics`, bound to `127.0.0.1:9090` on the host |

### Required `.env` values

- `DJANGO_SECRET_KEY` — `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`
- `DJANGO_ALLOWED_HOSTS` — your domain and/or server IP
- `DJANGO_CSRF_TRUSTED_ORIGINS` — the exact origin users load (e.g. `https://pulse.example.com` or `http://203.0.113.7:8000`)
- `POSTGRES_PASSWORD`, `RABBITMQ_PASSWORD` — long random strings
- `PULSE_ADMIN_EMAIL`, `PULSE_ADMIN_PASSWORD` — first admin account

### HTTPS

Put a TLS proxy in front of port 8000. With Caddy on the host this is two lines:

```
pulse.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Then set `DJANGO_SECURE_COOKIES=1` and `DJANGO_BEHIND_TLS_PROXY=1`, and change `PULSE_PORT` to `127.0.0.1:8000` so the app is only reachable through the proxy. Restart with `docker compose up -d`.

### Turning on AI analysis

Add your Groq API key to `.env` and restart the app containers:

```bash
GROQ_API_KEY=gsk_...
docker compose up -d
```

Until a key is set, `POST /api/incidents/{id}/ai-analysis/` returns `503` and the UI says it isn't configured.
The default model is `llama-3.3-70b-versatile`; set `GROQ_MODEL` in `.env` to use a different Groq model.

### Demo data (optional)

```bash
docker compose exec web python manage.py seed_demo --password 'choose-a-password'
```

Creates `admin`, `maya.manager`, `eli.engineer`, `sam.engineer`, `rio.engineer`, three services and three incidents.

### Operations

```bash
docker compose logs -f web worker beat     # logs
docker compose exec db pg_dump -U pulse pulse > backup.sql   # backup
git pull && docker compose up -d --build   # upgrade (migrations run on boot)
```

---

## Multi-engineer assignment

An incident can have any number of engineers through an explicit join table:

```
User ──< IncidentAssignment >── Incident
          incident_id  (FK)
          engineer_id  (FK → User, must be role ENGINEER)
          assigned_by_id (FK → User, the manager)
          assigned_at
          UNIQUE (incident_id, engineer_id)
```

`Incident.assignees` is a `ManyToManyField(through="IncidentAssignment")`, so `incident.assignees.all()` still works, while the join row records who assigned whom and when.

**Rules**

- Only managers change assignments: `PATCH /api/incidents/{id}/assign/` with `{"engineer_ids": [20, 21]}`. The body is the **complete desired set**; the server diffs it against the current rows, adds and removes as needed, writes one `ASSIGNED` / `UNASSIGNED` history entry per engineer, and notifies only people whose assignment changed. The response lists `added` and `removed`.
- Duplicate IDs are collapsed; unknown, inactive or non-engineer users are rejected with 400.
- Assignments can change only while the incident is `OPEN` or `IN_PROGRESS`. An `IN_PROGRESS` incident must keep at least one engineer.
- **Any** assigned engineer can move the incident through its lifecycle. Managers may additionally cancel (`OPEN → CANCELLED`) or close (`RESOLVED → CLOSED`).
- SLA breach notifications go to **every** assigned engineer plus all managers.
- Engineers see incidents they reported or are assigned to.

## Lifecycle

```
OPEN ──► IN_PROGRESS ──► RESOLVED ──► CLOSED
  └────► CANCELLED
```

Enforced in `incidents/services.py:change_status` under a row lock (`SELECT … FOR UPDATE`), with the status update and its history entry in one transaction. Invalid moves return 400 and name the allowed next states.

## SLA monitoring

On creation the server picks the `SLAPolicy` for the severity (seeded by migration: LOW 24h, MEDIUM 8h, HIGH 2h, CRITICAL 30m) and stores `sla_deadline` on the incident. Celery beat runs `check_sla_breaches`, which claims overdue `OPEN`/`IN_PROGRESS` rows with `FOR UPDATE SKIP LOCKED`, sets `sla_breached`, logs history and creates notifications. A partial unique index (`one_breach_notification_per_user`) makes duplicate breach notifications impossible even across concurrent workers or retries.

## API

All endpoints use Django session auth; unsafe methods need the `X-CSRFToken` header (get the cookie from `GET /api/auth/csrf/`).

| Method | Path | Who |
|---|---|---|
| GET | `/api/auth/csrf/` | anyone |
| POST | `/api/auth/login/` · `/api/auth/logout/` | — |
| GET | `/api/auth/me/` | signed in |
| GET, POST | `/api/incidents/` | list: per visibility · create: engineers |
| GET, PATCH | `/api/incidents/{id}/` | PATCH accepts only `title`, `description`, `status` |
| PATCH | `/api/incidents/{id}/assign/` | managers |
| GET | `/api/incidents/{id}/history/` | anyone who can see it |
| GET, POST | `/api/incidents/{id}/comments/` | anyone who can see it |
| POST | `/api/incidents/{id}/ai-analysis/` | anyone who can see it |
| GET | `/api/engineers/` | managers, admins |
| GET, PATCH | `/api/notifications/` · `/{id}/` | own notifications only |
| GET | `/api/notifications/unread-count/` | own |
| POST | `/api/notifications/mark-all-read/` | own |
| GET, POST, PATCH | `/api/users/` | admins (users are deactivated, never deleted) |
| GET / write | `/api/services/` | read: all · write: admins |
| GET | `/api/sla-policies/` | signed in |
| GET | `/api/health/` | anyone |
| GET | `/metrics` | private-network scrapers only |

List filters on `/api/incidents/`: `status=OPEN,IN_PROGRESS`, `severity=HIGH`, `service=3`, `mine=true`, `unassigned=true`, `breached=true`, `page`, `page_size` (≤200).

Incidents cannot be deleted (405) so the audit trail is permanent. Django admin is at `/admin/` for admin users.

## Local development

```bash
pip install -r requirements-dev.txt
export DJANGO_DEBUG=1 DATABASE_URL=postgres://pulse:pulse@localhost:5432/pulse
python manage.py migrate
python manage.py seed_demo --password 'PulseDemo#2026'
python manage.py runserver
pytest                      # needs PostgreSQL (uses SKIP LOCKED and partial indexes)
```

For Celery locally: `celery -A config worker -l info` and `celery -A config beat -l info` with RabbitMQ on `localhost:5672`.

## Layout

```
config/            settings (all from env), urls, celery app
accounts/          User model with ADMIN / MANAGER / ENGINEER role
incidents/
  models.py        Service, SLAPolicy, Incident, IncidentAssignment, IncidentHistory, Comment, Notification
  services.py      business logic: create, set_assignees, change_status, check_sla_breaches
  permissions.py   VALID_TRANSITIONS + role and object-level rules
  serializers.py   validation boundary; protected fields are never writable
  views.py         thin DRF viewsets
  tasks.py         Celery task wrapper
  ai.py            Groq analysis (inactive without a key)
  metrics.py       Prometheus collector computed from the DB at scrape time
web/               single-page console (template + static JS/CSS)
deploy/            entrypoint, Prometheus config
tests/             pytest suite against PostgreSQL
```
