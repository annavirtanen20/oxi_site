"""Small periodic-job runner for the `worker` container.

    python manage.py run_scheduler            # loop forever (production)
    python manage.py run_scheduler --once     # run all jobs once and exit

Jobs are idempotent and cheap, so a plain loop is enough for one guide's site.
When notifications and calendar sync need retries and queues, swap this for Celery.
"""

import logging
import signal
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.bookings.services import complete_past, expire_stale

log = logging.getLogger("scheduler")

# (job, interval in seconds)
JOBS = [
    (expire_stale, 60),
    (complete_past, 300),
]


class Command(BaseCommand):
    help = "Run periodic booking jobs (expire stale requests, complete past bookings)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run every job once and exit.")

    def handle(self, *args, once=False, **options):
        self._stop = False
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)

        last_run = {job: 0.0 for job, _ in JOBS}
        log.info("scheduler started")
        while not self._stop:
            now = time.monotonic()
            for job, interval in JOBS:
                if once or now - last_run[job] >= interval:
                    self._run(job)
                    last_run[job] = now
            if once:
                break
            time.sleep(5)
        log.info("scheduler stopped")

    def _run(self, job):
        close_old_connections()
        try:
            count = job()
            if count:
                log.info("%s: %s booking(s) updated", job.__name__, count)
        except Exception:  # keep the loop alive; Sentry/logs show the error
            log.exception("%s failed", job.__name__)
        finally:
            close_old_connections()

    def _request_stop(self, *_):
        self._stop = True
