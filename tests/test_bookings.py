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

WED_11 = malta(2026, 11, 11, 11)


def test_manual_service_creates_pending_request(tour, weekdays_9_to_17, policy, now, client_data):
    b = create_booking(service=tour, start=WED_11, client_data=client_data, party_size=2, now=now)
    assert b.status == Booking.Status.PENDING
    assert b.end == WED_11 + timedelta(hours=2)
    assert b.expires_at == now + policy.pending_ttl
    assert b.price_total == Decimal("120")
    assert len(b.manage_token) >= 40
    assert b.client.privacy_consent_at == now
    assert list(b.events.values_list("to_status", flat=True)) == ["pending"]


def test_instant_service_is_confirmed_immediately(tour, weekdays_9_to_17, policy, now, client_data):
    tour.confirmation_mode = "instant"
    tour.save()
    b = create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
    assert b.status == Booking.Status.CONFIRMED
    assert b.confirmed_at == now and b.expires_at is None


def test_price_on_request_forces_manual(tour, weekdays_9_to_17, policy, now, client_data):
    tour.confirmation_mode = "instant"
    tour.price_type = "on_request"
    tour.deposit_percent = 30
    tour.save()
    b = create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
    assert b.status == Booking.Status.PENDING
    assert b.price_total is None and b.deposit_amount is None


def test_deposit_flow(tour, weekdays_9_to_17, policy, now, client_data):
    tour.deposit_percent = 30
    tour.price_type = "per_person"
    tour.price = Decimal("45")
    tour.save()
    b = create_booking(service=tour, start=WED_11, client_data=client_data, party_size=3, now=now)
    assert b.status == Booking.Status.AWAITING_PAYMENT
    assert b.price_total == Decimal("135")
    assert b.deposit_amount == Decimal("40.50")
    assert b.expires_at == now + policy.payment_ttl
    b = mark_deposit_paid(b, now=now)
    assert b.status == Booking.Status.PENDING
    assert b.expires_at == now + policy.pending_ttl


def test_same_slot_cannot_be_booked_twice(tour, weekdays_9_to_17, policy, now, client_data):
    create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
    other = {**client_data, "email": "bob@example.com"}
    with pytest.raises(SlotUnavailable):
        create_booking(service=tour, start=malta(2026, 11, 11, 12), client_data=other, now=now)


def test_database_rejects_overlap_even_without_checks(
    tour, weekdays_9_to_17, policy, now, client_data
):
    """Simulates two concurrent requests that both passed the availability check."""
    first = create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
    clash = Booking(
        service=tour,
        client=first.client,
        status=Booking.Status.PENDING,
        start=malta(2026, 11, 11, 12),
        end=malta(2026, 11, 11, 14),
    )
    with pytest.raises(IntegrityError, match="booking_no_overlap"):
        with transaction.atomic():
            clash.save()


def test_race_is_reported_as_slot_unavailable(
    tour, weekdays_9_to_17, policy, now, client_data, monkeypatch
):
    create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
    # Pretend the availability check passed (as in a real race).
    monkeypatch.setattr("apps.bookings.services.availability_problem", lambda *a, **k: None)
    with pytest.raises(SlotUnavailable, match="just been taken"):
        create_booking(
            service=tour,
            start=WED_11,
            client_data={**client_data, "email": "bob@example.com"},
            now=now,
        )


def test_closed_bookings_do_not_block(tour, weekdays_9_to_17, policy, now, client_data):
    b = create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
    transition(b, "rejected", actor="guide", now=now)
    again = create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
    assert again.status == Booking.Status.PENDING


def test_validation_errors(tour, weekdays_9_to_17, policy, now, client_data):
    with pytest.raises(BookingError, match="privacy"):
        create_booking(
            service=tour, start=WED_11, client_data={**client_data, "privacy_consent": False}, now=now
        )
    with pytest.raises(BookingError, match="Group size"):
        create_booking(service=tour, start=WED_11, client_data=client_data, party_size=9, now=now)


def test_client_is_reused_case_insensitively(tour, weekdays_9_to_17, policy, now, client_data):
    create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
    create_booking(
        service=tour,
        start=malta(2026, 11, 12, 11),
        client_data={**client_data, "email": "JANE@example.com", "phone": "+356 1234"},
        now=now,
    )
    assert Client.objects.count() == 1
    assert Client.objects.get().phone == "+356 1234"


def test_transition_rules(tour, weekdays_9_to_17, policy, now, client_data):
    b = create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
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


def test_signal_fires_after_commit(
    tour, weekdays_9_to_17, policy, now, client_data, django_capture_on_commit_callbacks
):
    received = []
    handler = lambda **kw: received.append((kw["from_status"], kw["to_status"]))  # noqa: E731
    booking_status_changed.connect(handler)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            b = create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
            transition(b, "confirmed", actor="guide", now=now)
    finally:
        booking_status_changed.disconnect(handler)
    assert received == [("", "pending"), ("pending", "confirmed")]


def test_expire_stale(tour, weekdays_9_to_17, policy, now, client_data):
    b = create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
    assert expire_stale(now=now + timedelta(hours=23)) == 0
    assert expire_stale(now=now + timedelta(hours=24)) == 1
    b.refresh_from_db()
    assert b.status == "expired"
    assert b.events.last().actor == BookingEvent.Actor.SYSTEM


def test_complete_past(tour, weekdays_9_to_17, policy, now, client_data):
    b = create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
    transition(b, "confirmed", actor="guide", now=now)
    assert complete_past(now=WED_11 + timedelta(hours=1)) == 0
    assert complete_past(now=WED_11 + timedelta(hours=2)) == 1


def test_free_cancellation_window(tour, weekdays_9_to_17, policy, now, client_data):
    b = create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
    assert is_free_cancellation(b, now=WED_11 - timedelta(hours=48))
    assert not is_free_cancellation(b, now=WED_11 - timedelta(hours=47))


def test_reschedule(tour, weekdays_9_to_17, policy, now, client_data):
    b = create_booking(service=tour, start=WED_11, client_data=client_data, now=now)
    # Moving by one hour overlaps its own old slot: allowed, it excludes itself.
    b = reschedule(b, malta(2026, 11, 11, 12), actor="client", now=now)
    assert b.start == malta(2026, 11, 11, 12)
    assert b.blocked_period.upper == malta(2026, 11, 11, 14, 30)
    assert "Rescheduled" in b.events.last().note

    other = create_booking(
        service=tour,
        start=malta(2026, 11, 12, 11),
        client_data={**client_data, "email": "bob@example.com"},
        now=now,
    )
    with pytest.raises(SlotUnavailable):
        reschedule(other, malta(2026, 11, 11, 13), actor="guide", now=now)
    with pytest.raises(BookingError, match="no longer"):
        reschedule(b, malta(2026, 11, 13, 11), actor="client", now=WED_11 - timedelta(hours=10))
