from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.bookings.models import Booking, BookingEvent, Client
from apps.bookings.services import (
    BookingError,
    InvalidTransition,
    SlotUnavailable,
    booking_status_changed,
    complete_past,
    create_booking,
    expire_stale,
    is_free_cancellation,
    mark_deposit_paid,
    reschedule,
    transition,
)

from .conftest import malta

pytestmark = pytest.mark.django_db

START = malta(2026, 11, 11, 9)  # Wednesday
END = malta(2026, 11, 11, 13)


@pytest.fixture
def make(hostess, weekdays_9_to_17, policy, now, client_data):
    """make(start_h, end_h, day=11, **kwargs) -> Booking for the hostess service."""

    def _make(start_h=9, end_h=13, day=11, service=None, **kwargs):
        kwargs.setdefault("client_data", client_data)
        kwargs.setdefault("now", now)
        return create_booking(
            service=service or hostess,
            start=malta(2026, 11, day, start_h),
            end=malta(2026, 11, day, end_h),
            **kwargs,
        )

    return _make


def test_request_is_pending_and_priced_by_hours(make, policy, now):
    b = make(9, 15, party_size=2)
    assert b.status == Booking.Status.PENDING
    assert b.end - b.start == timedelta(hours=6)
    assert b.price_total == Decimal("180.00")  # 6 h x €30
    assert b.expires_at == now + policy.pending_ttl
    assert len(b.manage_token) >= 40
    assert b.client.privacy_consent_at == now
    assert list(b.events.values_list("to_status", flat=True)) == ["pending"]


def test_company_marks_client_as_agency(make):
    b = make()
    assert b.client.company == "Island Events Ltd"
    assert b.client.kind == Client.Kind.AGENCY


def test_instant_service_is_confirmed_immediately(make, hostess, now):
    hostess.confirmation_mode = "instant"
    hostess.save()
    b = make()
    assert b.status == Booking.Status.CONFIRMED
    assert b.confirmed_at == now and b.expires_at is None


def test_price_on_request_forces_manual(make, hostess):
    hostess.confirmation_mode = "instant"
    hostess.price_type = "on_request"
    hostess.deposit_percent = 30
    hostess.save()
    b = make()
    assert b.status == Booking.Status.PENDING
    assert b.price_total is None and b.deposit_amount is None


def test_deposit_flow(make, hostess, policy, now):
    hostess.deposit_percent = 30
    hostess.save()
    b = make(9, 14)  # 5 h x €30 = €150
    assert b.status == Booking.Status.AWAITING_PAYMENT
    assert b.price_total == Decimal("150.00")
    assert b.deposit_amount == Decimal("45.00")
    assert b.expires_at == now + policy.payment_ttl
    b = mark_deposit_paid(b, now=now)
    assert b.status == Booking.Status.PENDING
    assert b.expires_at == now + policy.pending_ttl


def test_overlapping_block_is_refused(make, client_data):
    make(9, 13)
    with pytest.raises(SlotUnavailable):
        make(12, 16, client_data={**client_data, "email": "bob@example.com"})


def test_database_rejects_overlap_even_without_checks(make, hostess):
    """Simulates two concurrent requests that both passed the availability check."""
    first = make(9, 13)
    clash = Booking(
        service=hostess,
        client=first.client,
        status=Booking.Status.PENDING,
        start=malta(2026, 11, 11, 12),
        end=malta(2026, 11, 11, 16),
    )
    with pytest.raises(IntegrityError, match="booking_no_overlap"):
        with transaction.atomic():
            clash.save()


def test_race_is_reported_as_slot_unavailable(make, client_data, monkeypatch):
    make(9, 13)
    monkeypatch.setattr("apps.bookings.services.availability_problem", lambda *a, **k: None)
    with pytest.raises(SlotUnavailable, match="just been taken"):
        make(9, 13, client_data={**client_data, "email": "bob@example.com"})


def test_closed_bookings_do_not_block(make, now):
    b = make()
    transition(b, "rejected", actor="guide", now=now)
    assert make().status == Booking.Status.PENDING


def test_validation_errors(make, client_data):
    with pytest.raises(BookingError, match="privacy"):
        make(client_data={**client_data, "privacy_consent": False})
    with pytest.raises(BookingError, match="Group size"):
        make(party_size=500)
    with pytest.raises(SlotUnavailable, match="at least 4 h"):
        make(9, 11)


def test_client_is_reused_case_insensitively(make, client_data):
    make()
    make(day=12, client_data={**client_data, "email": "JANE@example.com", "phone": "+356 1234"})
    assert Client.objects.count() == 1
    assert Client.objects.get().phone == "+356 1234"


def test_transition_rules(make, now):
    b = make()
    with pytest.raises(InvalidTransition):
        transition(b, "confirmed", actor="client", now=now)  # only the guide confirms
    with pytest.raises(InvalidTransition):
        transition(b, "completed", actor="guide", now=now)  # must be confirmed first
    b = transition(b, "confirmed", actor="guide", note="See you!", now=now)
    assert b.status == "confirmed" and b.confirmed_at == now and b.expires_at is None
    b = transition(b, "completed", actor="guide", now=now)
    assert b.closed_at == now
    with pytest.raises(InvalidTransition):
        transition(b, "cancelled", actor="guide", now=now)
    assert list(b.events.values_list("to_status", "actor")) == [
        ("pending", "client"),
        ("confirmed", "guide"),
        ("completed", "guide"),
    ]


def test_signal_fires_after_commit(make, now, django_capture_on_commit_callbacks):
    received = []

    def handler(**kw):
        received.append((kw["from_status"], kw["to_status"]))

    booking_status_changed.connect(handler)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            b = make()
            transition(b, "confirmed", actor="guide", now=now)
    finally:
        booking_status_changed.disconnect(handler)
    assert received == [("", "pending"), ("pending", "confirmed")]


def test_expire_stale(make, now):
    b = make()
    assert expire_stale(now=now + timedelta(hours=23)) == 0
    assert expire_stale(now=now + timedelta(hours=24)) == 1
    b.refresh_from_db()
    assert b.status == "expired"
    assert b.events.last().actor == BookingEvent.Actor.SYSTEM


def test_complete_past(make, now):
    b = make(9, 13)
    transition(b, "confirmed", actor="guide", now=now)
    assert complete_past(now=START + timedelta(hours=3)) == 0
    assert complete_past(now=END) == 1


def test_free_cancellation_window(make):
    b = make()
    assert is_free_cancellation(b, now=START - timedelta(hours=48))
    assert not is_free_cancellation(b, now=START - timedelta(hours=47))


def test_reschedule_keeps_length_or_takes_new_end(make, now, client_data):
    b = make(9, 13)
    # Moving by one hour overlaps its own old block: allowed, it excludes itself.
    b = reschedule(b, malta(2026, 11, 11, 10), actor="client", now=now)
    assert (b.start, b.end) == (malta(2026, 11, 11, 10), malta(2026, 11, 11, 14))
    assert "Rescheduled" in b.events.last().note

    b = reschedule(b, malta(2026, 11, 11, 9), malta(2026, 11, 11, 15), actor="guide", now=now)
    assert b.price_total == Decimal("180.00")  # 6 h now

    other = make(9, 13, day=12, client_data={**client_data, "email": "bob@example.com"})
    with pytest.raises(SlotUnavailable):
        reschedule(other, malta(2026, 11, 11, 12), actor="guide", now=now)
    with pytest.raises(BookingError, match="no longer"):
        reschedule(b, malta(2026, 11, 13, 9), actor="client", now=START - timedelta(hours=10))
