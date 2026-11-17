from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.catalog.models import Service
from apps.scheduling.models import BookingPolicy, WorkingHours

MALTA = ZoneInfo("Europe/Malta")


def malta(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=MALTA)


def hhmm(values):
    return [v.strftime("%H:%M") for v in values]


@pytest.fixture
def now():
    # Monday 2026-11-09 08:00 Malta time.
    return malta(2026, 11, 9, 8, 0)


@pytest.fixture
def policy(db):
    return BookingPolicy.get()


@pytest.fixture
def weekdays_9_to_17(db):
    for wd in range(5):
        WorkingHours.objects.create(weekday=wd, start=time(9), end=time(17))


@pytest.fixture
def round_the_clock(db):
    for wd in range(7):
        WorkingHours.objects.create(weekday=wd, start=time(0), end=time(0))


@pytest.fixture
def hostess(db):
    """Event hostess: blocks of 4 h+, 1 h steps, €30/h, no buffers."""
    return Service.objects.create(
        name="Event hostess",
        slug="event-hostess",
        kind=Service.Kind.EVENT,
        min_duration=timedelta(hours=4),
        duration_step=timedelta(hours=1),
        price=Decimal("30"),
        price_type=Service.PriceType.PER_HOUR,
        max_group_size=200,
    )


@pytest.fixture
def airport(db):
    """Airport meet & greet: any hour, 4 h minimum, 1 h kept free afterwards for travel."""
    return Service.objects.create(
        name="Airport meet & greet",
        slug="airport",
        kind=Service.Kind.AIRPORT,
        min_duration=timedelta(hours=4),
        duration_step=timedelta(hours=1),
        buffer_after=timedelta(hours=1),
        price=Decimal("35"),
        price_type=Service.PriceType.PER_HOUR,
        respects_working_hours=False,
        max_group_size=60,
    )


@pytest.fixture
def client_data():
    return {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "company": "Island Events Ltd",
        "privacy_consent": True,
    }
