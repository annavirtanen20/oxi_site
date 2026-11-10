from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.catalog.models import Service
from apps.scheduling.models import BookingPolicy, WorkingHours

MALTA = ZoneInfo("Europe/Malta")


def malta(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=MALTA)


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
def tour(db):
    return Service.objects.create(
        name="Valletta walking tour",
        slug="valletta-walk",
        duration=timedelta(hours=2),
        price=Decimal("120"),
        buffer_before=timedelta(0),
        buffer_after=timedelta(minutes=30),
        max_group_size=8,
    )


@pytest.fixture
def client_data():
    return {"name": "Jane Doe", "email": "jane@example.com", "privacy_consent": True}
