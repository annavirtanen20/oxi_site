from datetime import time, timedelta

import pytest

from apps.bookings.services import create_booking, transition
from apps.scheduling.models import TimeOff, WorkingHours
from apps.scheduling.slots import availability_problem, available_slots

from .conftest import malta

pytestmark = pytest.mark.django_db


def hhmm(slots):
    return [s.strftime("%H:%M") for s in slots]


def test_slots_follow_working_hours_and_step(tour, weekdays_9_to_17, policy, now):
    # Wednesday, well past the 24h notice.
    slots = available_slots(tour, malta(2026, 11, 11).date(), now=now)
    assert hhmm(slots)[0] == "09:00"
    assert hhmm(slots)[-1] == "15:00"  # 15:00 + 2h = 17:00 end of day
    assert len(slots) == 13  # every 30 minutes from 09:00 to 15:00


def test_no_slots_on_days_off(tour, weekdays_9_to_17, policy, now):
    assert available_slots(tour, malta(2026, 11, 14).date(), now=now) == []  # Saturday


def test_min_notice_hides_early_slots(tour, weekdays_9_to_17, policy, now):
    # now = Mon 08:00, notice 26h -> on Tuesday the first bookable start is 10:00.
    policy.min_notice = timedelta(hours=26)
    policy.save()
    slots = available_slots(tour, malta(2026, 11, 10).date(), now=now)
    assert hhmm(slots)[0] == "10:00"


def test_horizon_limits_far_future(tour, weekdays_9_to_17, policy, now):
    policy.horizon = timedelta(days=30)
    policy.save()
    assert available_slots(tour, malta(2027, 1, 13).date(), now=now) == []


def test_booking_and_buffer_block_neighbouring_slots(
    tour, weekdays_9_to_17, policy, now, client_data
):
    create_booking(service=tour, start=malta(2026, 11, 11, 11), client_data=client_data, now=now)
    slots = hhmm(available_slots(tour, malta(2026, 11, 11).date(), now=now))
    # Booked 11:00–13:00 + 30 min buffer => blocked 11:00–13:30.
    # A new tour blocks [start, start+2h30), so it must end its buffer by 11:00
    # (start <= 08:30, outside hours) or start at/after 13:30.
    assert "09:00" not in slots  # 09:00–11:30 with buffer overlaps 11:00
    assert "08:30" not in slots
    assert "13:00" not in slots
    assert "13:30" in slots
    assert slots == ["13:30", "14:00", "14:30", "15:00"]


def test_cancelled_booking_frees_slot(tour, weekdays_9_to_17, policy, now, client_data):
    booking = create_booking(
        service=tour, start=malta(2026, 11, 11, 11), client_data=client_data, now=now
    )
    transition(booking, "cancelled", actor="client", now=now)
    slots = hhmm(available_slots(tour, malta(2026, 11, 11).date(), now=now))
    assert "11:00" in slots and "09:00" in slots


def test_time_off_blocks_slots(tour, weekdays_9_to_17, policy, now):
    TimeOff.objects.create(start=malta(2026, 11, 11, 12), end=malta(2026, 11, 11, 13))
    slots = hhmm(available_slots(tour, malta(2026, 11, 11).date(), now=now))
    # Tour + buffer = 2h30; must not touch 12:00–13:00.
    assert "09:30" in slots  # 09:30–12:00 ends exactly at 12:00
    assert "10:00" not in slots
    assert "12:30" not in slots
    assert "13:00" in slots


def test_split_working_day(tour, policy, now):
    WorkingHours.objects.create(weekday=2, start=time(9), end=time(12))
    WorkingHours.objects.create(weekday=2, start=time(14), end=time(18))
    slots = hhmm(available_slots(tour, malta(2026, 11, 11).date(), now=now))
    assert slots == ["09:00", "09:30", "10:00", "14:00", "14:30", "15:00", "15:30", "16:00"]


def test_dst_change_keeps_wall_clock_slots(tour, policy, now):
    # Malta switches to summer time on Sunday 2027-03-28.
    WorkingHours.objects.create(weekday=6, start=time(9), end=time(12))
    policy.horizon = timedelta(days=365)
    policy.save()
    slots = available_slots(tour, malta(2027, 3, 28).date(), now=now)
    assert hhmm(slots) == ["09:00", "09:30", "10:00"]
    assert slots[0].utcoffset() == timedelta(hours=2)


def test_airport_service_can_ignore_working_hours(tour, weekdays_9_to_17, policy, now):
    tour.respects_working_hours = False
    tour.save()
    late_flight = malta(2026, 11, 11, 23, 15)
    assert availability_problem(tour, late_flight, now=now) is None


def test_problem_messages(tour, weekdays_9_to_17, policy, now):
    assert "outside working hours" in availability_problem(tour, malta(2026, 11, 11, 16), now=now)
    assert "too soon" in availability_problem(tour, malta(2026, 11, 9, 10), now=now)
