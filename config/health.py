"""Liveness/readiness check used by Docker, the deploy script and uptime monitoring."""

from django.db import connection, transaction
from django.http import JsonResponse
from django.views.decorators.cache import never_cache


# Outside ATOMIC_REQUESTS: otherwise a dead database fails before this view runs
# and the check returns a 500 instead of a clear 503.
@transaction.non_atomic_requests
@never_cache
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # any DB problem means "not ready"
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
