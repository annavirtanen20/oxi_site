"""Availability engine: which start times can a client book for a service.

All arithmetic is done on aware datetimes converted to UTC, so DST changes in
Malta never produce shifted or duplicated slots. Results are returned in the
business time zone (settings.TIME_ZONE).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.utils import timezone

from apps.catalog.models import Service

from .models import BookingPolicy, TimeOff, WorkingHours


def business_tz() -> ZoneInfo:
    return ZoneInfo(settings.TIME_ZONE)


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def overlaps(self, other: Interval) -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: Interval) -> bool:
        return self.start <= other.start and other.end <= self.end


def working_windows(day: date) -> list[Interval]:
    """Working intervals of a calendar day (Malta time), as UTC-aware intervals."""
    tz = business_tz()
    windows = []
    for wh in WorkingHours.objects.filter(weekday=day.weekday()):
        start = datetime.combine(day, wh.start, tzinfo=tz).astimezone(UTC)
        end = datetime.combine(day, wh.end, tzinfo=tz).astimezone(UTC)
        windows.append(Interval(start, end))
    return sorted(windows, key=lambda w: w.start)


def busy_intervals(span: Interval, exclude_booking_id: int | None = None) -> list[Interval]:
    """Everything that blocks time inside `span`: active bookings and time off."""
    from apps.bookings.models import Booking  # avoid import cycle

    busy: list[Interval] = []
    bookings = Booking.objects.filter(
        status__in=Booking.ACTIVE_STATUSES,
        blocked_period__overlap=DateTimeTZRange(span.start, span.end, "[)"),
    )
    if exclude_booking_id is not None:
        bookings = bookings.exclude(pk=exclude_booking_id)
    for b in bookings.only("blocked_period"):
        busy.append(Interval(b.blocked_period.lower, b.blocked_period.upper))
    for t in TimeOff.objects.filter(start__lt=span.end, end__gt=span.start):
        busy.append(Interval(t.start, t.end))
    return busy


def service_intervals(service: Service, start: datetime) -> tuple[Interval, Interval]:
    """(the service itself, the service plus its buffers)."""
    end = start + service.duration
    core = Interval(start, end)
    blocked = Interval(start - service.buffer_before, end + service.buffer_after)
    return core, blocked


def availability_problem(
    service: Service,
    start: datetime,
    *,
    now: datetime | None = None,
    exclude_booking_id: int | None = None,
    policy: BookingPolicy | None = None,
) -> str | None:
    """Return why `start` cannot be booked for `service`, or None if it can."""
    if timezone.is_naive(start):
        raise ValueError("start must be timezone-aware")
    now = now or timezone.now()
    policy = policy or BookingPolicy.get()
    start = start.astimezone(UTC)
    core, blocked = service_intervals(service, start)

    if not service.is_active:
        return "This service is not available for booking."
    if start < now + policy.min_notice:
        return "This time is too soon to book."
    if start > now + policy.horizon:
        return "This date is too far in the future."
    if service.respects_working_hours:
        local_day = start.astimezone(business_tz()).date()
        if not any(w.contains(core) for w in working_windows(local_day)):
            return "This time is outside working hours."
    if any(blocked.overlaps(b) for b in busy_intervals(blocked, exclude_booking_id)):
        return "This time is already taken."
    return None


def is_available(service: Service, start: datetime, **kwargs) -> bool:
    return availability_problem(service, start, **kwargs) is None


def available_slots(service: Service, day: date, *, now: datetime | None = None) -> list[datetime]:
    """Bookable start times for `service` on `day`, in Malta time."""
    if not service.is_active:
        return []
    now = now or timezone.now()
    policy = BookingPolicy.get()
    windows = working_windows(day)
    if not windows:
        return []

    earliest = now + policy.min_notice
    latest = now + policy.horizon
    span = Interval(
        windows[0].start - service.buffer_before,
        windows[-1].end + service.buffer_after,
    )
    busy = busy_intervals(span)
    tz = business_tz()

    slots: list[datetime] = []
    for window in windows:
        start = window.start
        while start + service.duration <= window.end:
            core, blocked = service_intervals(service, start)
            if earliest <= start <= latest and not any(blocked.overlaps(b) for b in busy):
                slots.append(start.astimezone(tz))
            start += policy.slot_step
    return slots


def available_days(
    service: Service, first: date, last: date, *, now: datetime | None = None
) -> list[date]:
    """Days in [first, last] that have at least one free slot (for the calendar view)."""
    days = []
    day = first
    while day <= last:
        if available_slots(service, day, now=now):
            days.append(day)
        day += timedelta(days=1)
    return days
