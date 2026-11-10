# oxi_site

Website with booking for a freelance tour guide and ground-handling representative in Malta.
Stack: Django 5.1, PostgreSQL 16. Full spec: the "ТЗ" doc in the project.

## Status

Done (MVP foundation):

- `apps.catalog` — services: duration, price type, group size, buffers, manual/instant
  confirmation, deposit percent, airport services that ignore working hours.
- `apps.scheduling` — weekly working hours (several intervals per day), time off
  (manual or imported from Google), editable booking policy (slot step, notice,
  horizon, request/payment timeouts, free cancellation window), availability engine.
- `apps.bookings` — clients, bookings, status machine, audit log, reschedule,
  expiry and auto-completion jobs, `booking_status_changed` signal for notifications.
- Wagtail CMS is the guide's single workspace: dashboard with requests waiting for an answer
  and the next 7 days, booking list and card with status buttons and history, clients,
  schedule and booking rules. Status changes go through the same service functions.
- `apps.pages` — Wagtail pages: Home, About, Tours and prices (each service has its
  own URL), Questions, Contact (form with honeypot), privacy/terms; site settings for
  contacts and WhatsApp/Telegram buttons. Services and prices are edited in the CMS too.
- Booking wizard: `/book/` → service → calendar with free days and HTMX slot list →
  details form (airport pickups ask for flight and arrival time instead of a slot) →
  `/booking/<token>/` page where the client sees the status and can cancel.
- Self-hosted fonts (Alegreya) and HTMX: no third-party requests from the public site.

Next: emails, Telegram bot, Google Calendar sync, Celery beat for
`expire_stale` / `complete_past`, Stripe deposits.

## URLs

| URL | What |
| --- | --- |
| `/` and other pages | Public site (Wagtail page tree) |
| `/book/` | Booking wizard |
| `/booking/<token>/` | Client's booking page |
| `/cms/` | The guide's workspace (Wagtail): bookings with confirm/reject buttons, clients, working hours, days off, booking rules, pages, photos, services and prices, site settings |
| `/manage-admin/` | Django admin: raw access to all data, for the developer |

## Key design points

- **No double booking at the database level.** `Booking.blocked_period` is a
  `tstzrange` of `[start - buffer_before, end + buffer_after)`; an `EXCLUDE USING gist`
  constraint forbids overlaps between active bookings (awaiting deposit, pending,
  confirmed). A lost race surfaces as `SlotUnavailable`.
- **One time zone.** Everything is shown in `Europe/Malta`; slot arithmetic runs in UTC,
  so DST switches never shift slots.
- **All status changes go through `apps.bookings.services`** (`create_booking`,
  `transition`, `mark_deposit_paid`, `reschedule`, `expire_stale`, `complete_past`).
  Each change is validated against the allowed transitions and actor, logged in
  `BookingEvent`, and announced with `booking_status_changed` after commit.

Status flow:

```
awaiting_payment ──paid──> pending ──guide confirms──> confirmed ──after service──> completed
       │                     │  └──guide rejects──> rejected        │
       └──not paid──> expired <──no answer in 24h                    └──cancel──> cancelled
```

(instant services skip `pending`; services without a deposit skip `awaiting_payment`.)

## CI/CD

GitHub Actions (`.github/workflows/ci.yml`): lint (ruff) and tests on every push and pull
request; pushes to `develop`/`main` build an image to GHCR tagged `sha-<commit>`; pushes to
`main` deploy it with health check and automatic rollback once `DEPLOY_ENABLED` is set.
Before pushing, run locally: `ruff check . && ruff format --check . && pytest`.

## Production

See [deploy/DEPLOY.md](deploy/DEPLOY.md): Hetzner Cloud server, Docker Compose with
Caddy (automatic HTTPS), PostgreSQL, a background `worker`, daily backups.

## Run locally

```bash
cp .env.example .env
docker compose up --build        # app on http://localhost:8000/manage-admin/
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py seed_demo   # pages, sample services, working hours
```

`seed_demo` fills the site with placeholder texts in [square brackets]; replace them
and upload 1–2 photos in `/cms/`. Reviews stay empty until real ones are added.

Without Docker (needs a local PostgreSQL):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env             # point DATABASE_URL at your database
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Tests

```bash
pytest
```

Tests need PostgreSQL (the overlap constraint is Postgres-only); pytest-django creates
and drops a `test_*` database automatically.
