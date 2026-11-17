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
    upcoming_free_days,
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


# --- next free dates card ---------------------------------------------------------


def _ranges(day):
    return [f"{s:%H:%M}-{e:%H:%M}" for s, e in day["ranges"]]


def test_free_days_start_after_min_notice(hostess, weekdays_9_to_17, policy, now):
    # Monday 08:00 + 24 h notice: Monday is gone, Tuesday is the first free day.
    days = upcoming_free_days(now=now)
    assert [d["day"].isoformat() for d in days] == ["2026-11-10", "2026-11-11", "2026-11-12"]
    assert _ranges(days[0]) == ["09:00-17:00"]
    assert days[0]["hours"] == 8
    assert not days[0]["all_day"]


def test_free_days_skip_days_without_room_for_a_block(
    hostess, weekdays_9_to_17, policy, now, client_data
):
    # 11:00–15:00 taken on Wednesday leaves 2 h on each side: too short for a 4 h block.
    book(hostess, malta(2026, 11, 11, 11), malta(2026, 11, 11, 15), client_data, now)
    days = upcoming_free_days(now=now)
    assert [d["day"].isoformat() for d in days] == ["2026-11-10", "2026-11-12", "2026-11-13"]


def test_free_days_show_split_ranges(hostess, weekdays_9_to_17, policy, now):
    TimeOff.objects.create(start=malta(2026, 11, 10, 9), end=malta(2026, 11, 10, 10))
    WorkingHours.objects.filter(weekday=1).update(end=time(22))
    TimeOff.objects.create(start=malta(2026, 11, 10, 14), end=malta(2026, 11, 10, 17))
    days = upcoming_free_days(now=now, count=1)
    assert _ranges(days[0]) == ["10:00-14:00", "17:00-22:00"]
    assert days[0]["hours"] == 9


def test_free_days_mark_whole_days(hostess, round_the_clock, policy, now):
    tue, wed = upcoming_free_days(now=now, count=2)
    assert not tue["all_day"]  # free only from 08:00, after the notice period
    assert wed["all_day"]


def test_no_free_days_without_active_services(hostess, weekdays_9_to_17, policy, now):
    hostess.is_active = False
    hostess.save()
    assert upcoming_free_days(now=now) == []


def test_free_days_use_a_fixed_number_of_queries(
    hostess, weekdays_9_to_17, policy, now, django_assert_max_num_queries
):
    # Found quickly, or after scanning the whole month: the query count is the same.
    with django_assert_max_num_queries(5):
        assert len(upcoming_free_days(now=now)) == 3
    TimeOff.objects.create(start=malta(2026, 11, 9), end=malta(2026, 12, 31))
    with django_assert_max_num_queries(5):
        assert upcoming_free_days(now=now) == []


def test_free_days_split_overnight_windows_at_midnight(hostess, policy, now):
    # Friday 18:00–00:00 joins Saturday 00:00–03:00 into one window; the card
    # still shows each day separately, and Saturday's 3 h is too short for a block.
    WorkingHours.objects.create(weekday=4, start=time(18), end=time(0))
    WorkingHours.objects.create(weekday=5, start=time(0), end=time(3))
    days = upcoming_free_days(now=now)
    assert [d["day"].isoformat() for d in days] == ["2026-11-13", "2026-11-20", "2026-11-27"]
    assert _ranges(days[0]) == ["18:00-00:00"]
    assert days[0]["hours"] == 6
