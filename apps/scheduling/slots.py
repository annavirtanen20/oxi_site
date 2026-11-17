"""Availability engine: which blocks of time a client can book for a service.

A booking is a block [start, end): at least `service.min_duration` long, growing in
`service.duration_step`, starting on the step grid of the day (e.g. every full hour).
Several bookings a day are fine as long as they (plus the service's buffers) don't
overlap. Blocks may run past midnight when the policy allows it and the working
hours of both days join at midnight.

All arithmetic is done on aware datetimes in UTC, so DST changes never shift or
duplicate hours. Results are returned in the business time zone (settings.TIME_ZONE).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.utils import timezone

from apps.catalog.models import Service

from .models import BookingPolicy, TimeOff, WorkingHours

# Without a max_duration, offer end times up to this long after the start
# (a working day; set max_duration on the service to allow longer blocks).
DEFAULT_MAX_BLOCK = timedelta(hours=12)


def business_tz() -> ZoneInfo:
    return ZoneInfo(settings.TIME_ZONE)


def local_midnight(day: date) -> datetime:
    """00:00 Malta time on `day`, as an aware UTC datetime."""
    return datetime.combine(day, time(0), tzinfo=business_tz()).astimezone(UTC)


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def overlaps(self, other: Interval) -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: Interval) -> bool:
        return self.start <= other.start and other.end <= self.end


# --- interval helpers -----------------------------------------------------------


def _merge(intervals: list[Interval]) -> list[Interval]:
    """Join overlapping or touching intervals."""
    merged: list[Interval] = []
    for iv in sorted(intervals, key=lambda i: i.start):
        if merged and iv.start <= merged[-1].end:
            last = merged[-1]
            merged[-1] = Interval(last.start, max(last.end, iv.end))
        else:
            merged.append(iv)
    return merged


def _subtract(intervals: list[Interval], holes: list[Interval]) -> list[Interval]:
    result = intervals
    for hole in holes:
        nxt = []
        for iv in result:
            if not iv.overlaps(hole):
                nxt.append(iv)
                continue
            if iv.start < hole.start:
                nxt.append(Interval(iv.start, hole.start))
            if hole.end < iv.end:
                nxt.append(Interval(hole.end, iv.end))
        result = nxt
    return result


def _clip(intervals: list[Interval], lo: datetime, hi: datetime) -> list[Interval]:
    out = []
    for iv in intervals:
        s, e = max(iv.start, lo), min(iv.end, hi)
        if s < e:
            out.append(Interval(s, e))
    return out


def _split_at_midnights(intervals: list[Interval]) -> list[Interval]:
    """Cut intervals at local midnights, so no block can run into the next day."""
    tz = business_tz()
    out = []
    for iv in intervals:
        start = iv.start
        while True:
            next_midnight = local_midnight(start.astimezone(tz).date() + timedelta(days=1))
            if next_midnight >= iv.end:
                out.append(Interval(start, iv.end))
                break
            out.append(Interval(start, next_midnight))
            start = next_midnight
    return out


# --- what is open and what is taken ------------------------------------------------


def working_windows(first: date, last: date) -> list[Interval]:
    """Working hours from `first` to `last` (inclusive), merged where they touch.

    An end of 00:00 means midnight at the end of that day, so Friday 18:00–00:00
    and Saturday 00:00–03:00 become one window Friday 18:00 → Saturday 03:00.
    """
    by_weekday: dict[int, list[WorkingHours]] = {}
    for wh in WorkingHours.objects.all():
        by_weekday.setdefault(wh.weekday, []).append(wh)
    tz = business_tz()
    windows = []
    day = first
    while day <= last:
        for wh in by_weekday.get(day.weekday(), []):
            start = datetime.combine(day, wh.start, tzinfo=tz).astimezone(UTC)
            end_day = day + timedelta(days=1) if wh.ends_at_midnight else day
            end = datetime.combine(end_day, wh.end, tzinfo=tz).astimezone(UTC)
            windows.append(Interval(start, end))
        day += timedelta(days=1)
    return _merge(windows)


def busy_intervals(span: Interval, exclude_booking_id: int | None = None) -> list[Interval]:
    """Everything that blocks time inside `span`: active bookings (with their
    buffers, as stored in blocked_period) and time off."""
    from apps.bookings.models import Booking  # avoid import cycle

    bookings = Booking.objects.filter(
        status__in=Booking.ACTIVE_STATUSES,
        blocked_period__overlap=DateTimeTZRange(span.start, span.end, "[)"),
    )
    if exclude_booking_id is not None:
        bookings = bookings.exclude(pk=exclude_booking_id)
    busy = [
        Interval(b.blocked_period.lower, b.blocked_period.upper)
        for b in bookings.only("blocked_period")
    ]
    busy += [
        Interval(t.start, t.end)
        for t in TimeOff.objects.filter(start__lt=span.end, end__gt=span.start)
    ]
    return busy


def free_intervals(
    service: Service,
    span: Interval,
    *,
    now: datetime | None = None,
    policy: BookingPolicy | None = None,
    exclude_booking_id: int | None = None,
    apply_notice: bool = True,
) -> list[Interval]:
    """Stretches inside `span` where this service's block (the client's own time,
    without buffers) may lie. `apply_notice=False` skips the min-notice/horizon cut."""
    now = now or timezone.now()
    policy = policy or BookingPolicy.get()
    tz = business_tz()

    if service.respects_working_hours:
        first = span.start.astimezone(tz).date() - timedelta(days=1)
        last = span.end.astimezone(tz).date()
        base = working_windows(first, last)
    else:
        base = [span]

    if apply_notice:
        lo, hi = max(span.start, now + policy.min_notice), min(span.end, now + policy.horizon)
    else:
        lo, hi = span.start, span.end
    base = _clip(base, lo, hi)

    # A new block [s, e) blocks [s - before, e + after); it must not touch existing
    # busy time, so the block itself has to avoid busy time widened by those buffers.
    look = Interval(
        span.start - service.buffer_after - DEFAULT_MAX_BLOCK,
        span.end + service.buffer_before + DEFAULT_MAX_BLOCK,
    )
    # [s - before, e + after) overlaps busy [B.s, B.e)  <=>  s < B.e + before and
    # e > B.s - after, so the forbidden zone for the block is (B.s - after, B.e + before).
    holes = [
        Interval(b.start - service.buffer_after, b.end + service.buffer_before)
        for b in busy_intervals(look, exclude_booking_id)
    ]
    free = _subtract(base, holes)
    if not policy.allow_overnight:
        free = _split_at_midnights(free)
    return free


# --- what the booking wizard offers ------------------------------------------------


def _max_block(service: Service) -> timedelta:
    return service.max_duration or DEFAULT_MAX_BLOCK


def available_starts(service: Service, day: date, *, now: datetime | None = None) -> list[datetime]:
    """Start times on `day` (Malta time) where at least a minimum block fits."""
    if not service.is_active:
        return []
    day_start = local_midnight(day)
    day_end = local_midnight(day + timedelta(days=1))
    free = free_intervals(service, Interval(day_start, day_end + _max_block(service)), now=now)
    step, tz = service.duration_step, business_tz()

    starts = []
    for iv in free:
        # first point of the day's grid at or after the free stretch begins
        offset = (iv.start - day_start) % step
        s = iv.start if not offset else iv.start + (step - offset)
        while s < day_end and s + service.min_duration <= iv.end:
            if s >= day_start:
                starts.append(s.astimezone(tz))
            s += step
    return starts


def available_ends(
    service: Service,
    start: datetime,
    *,
    now: datetime | None = None,
    exclude_booking_id: int | None = None,
) -> list[datetime]:
    """Possible end times for a block starting at `start`, shortest first."""
    start = start.astimezone(UTC)
    span = Interval(start, start + _max_block(service))
    free = free_intervals(service, span, now=now, exclude_booking_id=exclude_booking_id)
    room = next((iv for iv in free if iv.start <= start < iv.end), None)
    if room is None:
        return []
    tz = business_tz()
    ends = []
    end = start + service.min_duration
    while end <= room.end and end - start <= _max_block(service):
        ends.append(end.astimezone(tz))
        end += service.duration_step
    return ends


def availability_problem(
    service: Service,
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
    exclude_booking_id: int | None = None,
    policy: BookingPolicy | None = None,
) -> str | None:
    """Why the block [start, end) can't be booked for `service`, or None if it can."""
    if timezone.is_naive(start) or timezone.is_naive(end):
        raise ValueError("start and end must be timezone-aware")
    now = now or timezone.now()
    policy = policy or BookingPolicy.get()
    tz = business_tz()
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    block = Interval(start, end)

    if not service.is_active:
        return "This service is not available for booking."
    if end <= start:
        return "The end must be after the start."
    if not service.allowed_duration(end - start):
        return f"Please choose at least {service.min_duration_label}, in whole steps" + (
            f", up to {service.max_duration.total_seconds() / 3600:g} h."
            if service.max_duration
            else "."
        )
    day_start = local_midnight(start.astimezone(tz).date())
    if (start - day_start) % service.duration_step:
        return "Please start on the hour grid shown."
    if start < now + policy.min_notice:
        return "This time is too soon to book."
    if start > now + policy.horizon:
        return "This date is too far in the future."
    next_midnight = local_midnight(start.astimezone(tz).date() + timedelta(days=1))
    if not policy.allow_overnight and end > next_midnight:
        return "Bookings can't continue past midnight."
    if service.respects_working_hours:
        windows = working_windows(
            start.astimezone(tz).date() - timedelta(days=1), end.astimezone(tz).date()
        )
        if not any(w.contains(block) for w in windows):
            return "This time is outside working hours."
    free = free_intervals(
        service,
        Interval(start, end),
        now=now,
        policy=policy,
        exclude_booking_id=exclude_booking_id,
        apply_notice=False,  # checked above with clearer messages
    )
    if not any(iv.contains(block) for iv in free):
        return "This time is already taken."
    return None


def is_available(service: Service, start: datetime, end: datetime, **kwargs) -> bool:
    return availability_problem(service, start, end, **kwargs) is None


def day_timeline(service: Service, day: date, *, now: datetime | None = None) -> list[dict]:
    """One entry per hour of `day` for the visual strip in the wizard:
    {'start': local datetime, 'free': bool}. Why an hour is unavailable is not shown."""
    day_start = local_midnight(day)
    day_end = local_midnight(day + timedelta(days=1))
    free = free_intervals(service, Interval(day_start, day_end), now=now)
    tz = business_tz()
    hours = []
    h = day_start
    while h < day_end:
        cell = Interval(h, h + timedelta(hours=1))
        hours.append({"start": h.astimezone(tz), "free": any(iv.contains(cell) for iv in free)})
        h += timedelta(hours=1)
    return hours


def available_days(
    service: Service, first: date, last: date, *, now: datetime | None = None
) -> list[date]:
    """Days in [first, last] with at least one possible start (for the month view)."""
    days = []
    day = first
    while day <= last:
        if available_starts(service, day, now=now):
            days.append(day)
        day += timedelta(days=1)
    return days
