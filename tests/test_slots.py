from datetime import time, timedelta

import pytest

from apps.bookings.services import create_booking, transition
from apps.scheduling.models import TimeOff, WorkingHours
from apps.scheduling.slots import (
    availability_problem,
    available_days,
    available_ends,
    available_starts,
    day_timeline,
)

from .conftest import hhmm, malta

pytestmark = pytest.mark.django_db

WED = malta(2026, 11, 11).date()


def book(service, start, end, client_data, now, email="a@example.com"):
    return create_booking(
        service=service,
        start=start,
        end=end,
        client_data={**client_data, "email": email},
        now=now,
    )


# --- starts and ends ---------------------------------------------------------------


def test_starts_leave_room_for_minimum_block(hostess, weekdays_9_to_17, policy, now):
    # 09:00–17:00, at least 4 h: the last possible start is 13:00.
    assert hhmm(available_starts(hostess, WED, now=now)) == [
        "09:00",
        "10:00",
        "11:00",
        "12:00",
        "13:00",
    ]


def test_ends_grow_in_steps_up_to_the_free_limit(hostess, weekdays_9_to_17, policy, now):
    ends = available_ends(hostess, malta(2026, 11, 11, 10), now=now)
    assert hhmm(ends) == ["14:00", "15:00", "16:00", "17:00"]


def test_max_duration_caps_the_block(hostess, weekdays_9_to_17, policy, now):
    hostess.max_duration = timedelta(hours=5)
    hostess.save()
    assert hhmm(available_ends(hostess, malta(2026, 11, 11, 9), now=now)) == ["13:00", "14:00"]


def test_no_starts_on_days_off(hostess, weekdays_9_to_17, policy, now):
    assert available_starts(hostess, malta(2026, 11, 14).date(), now=now) == []  # Saturday


def test_min_notice_and_horizon(hostess, weekdays_9_to_17, policy, now):
    policy.min_notice = timedelta(hours=26)  # now = Mon 08:00 -> from Tue 10:00
    policy.save()
    assert hhmm(available_starts(hostess, malta(2026, 11, 10).date(), now=now))[0] == "10:00"
    policy.horizon = timedelta(days=30)
    policy.save()
    assert available_starts(hostess, malta(2027, 1, 13).date(), now=now) == []


# --- several bookings a day ----------------------------------------------------------


def test_airport_at_five_and_event_at_eight_pm_same_day(
    airport, hostess, round_the_clock, policy, now, client_data
):
    """The example from the brief: 05:00 airport pickup and a 20:00 event on one day."""
    book(airport, malta(2026, 11, 11, 5), malta(2026, 11, 11, 9), client_data, now)

    starts = hhmm(available_starts(hostess, WED, now=now))
    # Airport 05:00–09:00 plus 1 h travel buffer -> busy until 10:00.
    assert "09:00" not in starts
    assert "10:00" in starts
    assert "20:00" in starts
    # 01:00 would need 01:00–05:00, which touches the airport block: allowed (it ends at 05:00).
    assert "01:00" in starts
    assert "02:00" not in starts

    evening = book(
        hostess, malta(2026, 11, 11, 20), malta(2026, 11, 12, 0), client_data, now, "b@example.com"
    )
    assert evening.status == "pending"


def test_back_to_back_blocks_without_buffers(hostess, round_the_clock, policy, now, client_data):
    book(hostess, malta(2026, 11, 11, 5), malta(2026, 11, 11, 9), client_data, now)
    assert "09:00" in hhmm(available_starts(hostess, WED, now=now))
    assert hhmm(available_ends(hostess, malta(2026, 11, 11, 1), now=now)) == ["05:00"]


def test_buffer_of_new_service_respects_existing_booking(
    airport, hostess, round_the_clock, policy, now, client_data
):
    book(hostess, malta(2026, 11, 11, 14), malta(2026, 11, 11, 18), client_data, now)
    # An airport block needs 1 h free after it: it must end by 13:00.
    ends = hhmm(available_ends(airport, malta(2026, 11, 11, 8), now=now))
    assert ends == ["12:00", "13:00"]


def test_cancelled_booking_frees_time(hostess, weekdays_9_to_17, policy, now, client_data):
    b = book(hostess, malta(2026, 11, 11, 9), malta(2026, 11, 11, 17), client_data, now)
    assert available_starts(hostess, WED, now=now) == []
    transition(b, "cancelled", actor="client", now=now)
    assert len(available_starts(hostess, WED, now=now)) == 5


def test_time_off_blocks_hours(hostess, weekdays_9_to_17, policy, now):
    TimeOff.objects.create(start=malta(2026, 11, 11, 12), end=malta(2026, 11, 11, 13))
    # 09–12 is only 3 h, 13–17 is 4 h: just one start left.
    assert hhmm(available_starts(hostess, WED, now=now)) == ["13:00"]


# --- past midnight ------------------------------------------------------------------


def test_block_can_run_past_midnight(hostess, round_the_clock, policy, now):
    ends = hhmm(available_ends(hostess, malta(2026, 11, 11, 21), now=now))
    assert ends[:3] == ["01:00", "02:00", "03:00"]
    assert (
        availability_problem(hostess, malta(2026, 11, 11, 21), malta(2026, 11, 12, 2), now=now)
        is None
    )


def test_overnight_can_be_switched_off(hostess, round_the_clock, policy, now):
    policy.allow_overnight = False
    policy.save()
    assert hhmm(available_ends(hostess, malta(2026, 11, 11, 19), now=now)) == ["23:00", "00:00"]
    assert "21:00" not in hhmm(available_starts(hostess, WED, now=now))
    problem = availability_problem(
        hostess, malta(2026, 11, 11, 21), malta(2026, 11, 12, 2), now=now
    )
    assert "past midnight" in problem


def test_overnight_follows_working_hours_that_join(hostess, policy, now):
    WorkingHours.objects.create(weekday=4, start=time(18), end=time(0))  # Fri 18:00–24:00
    WorkingHours.objects.create(weekday=5, start=time(0), end=time(3))  # Sat 00:00–03:00
    fri = malta(2026, 11, 13, 22)
    assert hhmm(available_ends(hostess, fri, now=now)) == ["02:00", "03:00"]

    WorkingHours.objects.filter(weekday=5).delete()
    assert available_ends(hostess, fri, now=now) == []  # 22:00 + 4 h no longer fits
    assert hhmm(available_starts(hostess, fri.date(), now=now)) == [
        "18:00",
        "19:00",
        "20:00",
    ]


# --- edge cases ----------------------------------------------------------------------


def test_dst_change_keeps_hour_grid(hostess, round_the_clock, policy, now):
    # Malta switches to summer time on Sunday 2027-03-28: 02:00 doesn't exist.
    policy.horizon = timedelta(days=365)
    policy.save()
    day = malta(2027, 3, 28).date()
    starts = hhmm(available_starts(hostess, day, now=now))
    assert starts[:3] == ["00:00", "01:00", "03:00"]
    assert "02:00" not in starts
    assert len(day_timeline(hostess, day, now=now)) == 23


def test_service_outside_working_hours(airport, weekdays_9_to_17, policy, now):
    assert "05:00" in hhmm(available_starts(airport, WED, now=now))
    assert "05:00" in hhmm(available_starts(airport, malta(2026, 11, 14).date(), now=now))


def test_problem_messages(hostess, weekdays_9_to_17, policy, now, client_data):
    def problem(start_h, end_h, day=11):
        return availability_problem(
            hostess, malta(2026, 11, day, start_h), malta(2026, 11, day, end_h), now=now
        )

    assert "at least 4 h" in problem(9, 12)
    assert "hour grid" in availability_problem(
        hostess, malta(2026, 11, 11, 9, 30), malta(2026, 11, 11, 13, 30), now=now
    )
    assert "outside working hours" in problem(14, 18)
    assert "too soon" in problem(9, 13, day=9)
    book(hostess, malta(2026, 11, 11, 9), malta(2026, 11, 11, 13), client_data, now)
    assert "already taken" in problem(12, 16)
    assert problem(13, 17) is None


def test_timeline_and_month_view(hostess, weekdays_9_to_17, policy, now):
    timeline = day_timeline(hostess, WED, now=now)
    free = [h["start"].strftime("%H") for h in timeline if h["free"]]
    assert free == ["09", "10", "11", "12", "13", "14", "15", "16"]
    days = available_days(hostess, malta(2026, 11, 9).date(), malta(2026, 11, 15).date(), now=now)
    assert [d.day for d in days] == [10, 11, 12, 13]  # Mon is too soon, weekend closed
