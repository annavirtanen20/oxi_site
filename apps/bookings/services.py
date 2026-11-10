"""Booking use cases: create a booking and move it through its statuses.

Views, the Telegram bot, the admin and background jobs should all go through
these functions, never set `Booking.status` directly.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.dispatch import Signal
from django.utils import timezone

from apps.catalog.models import Service
from apps.scheduling.models import BookingPolicy
from apps.scheduling.slots import availability_problem

from .models import Booking, BookingEvent, Client

S = Booking.Status
Actor = BookingEvent.Actor

# Sent after every committed status change: kwargs booking, from_status, to_status, actor.
# Notifications (Telegram, email) and Google Calendar sync subscribe to it.
booking_status_changed = Signal()


class BookingError(Exception):
    """A booking action is not allowed; the message is safe to show to the user."""


class SlotUnavailable(BookingError):
    pass


class InvalidTransition(BookingError):
    pass


# Allowed transitions and who may trigger them (ТЗ, раздел 4.3).
TRANSITIONS: dict[str, dict[str, set[str]]] = {
    S.AWAITING_PAYMENT: {
        S.PENDING: {Actor.SYSTEM},  # deposit paid, manual service
        S.CONFIRMED: {Actor.SYSTEM},  # deposit paid, instant service
        S.EXPIRED: {Actor.SYSTEM},
        S.CANCELLED: {Actor.CLIENT, Actor.GUIDE},
    },
    S.PENDING: {
        S.CONFIRMED: {Actor.GUIDE},
        S.REJECTED: {Actor.GUIDE},
        S.EXPIRED: {Actor.SYSTEM},
        S.CANCELLED: {Actor.CLIENT, Actor.GUIDE},
    },
    S.CONFIRMED: {
        S.COMPLETED: {Actor.GUIDE, Actor.SYSTEM},
        S.CANCELLED: {Actor.CLIENT, Actor.GUIDE},
    },
}

CLOSED_STATUSES = {S.COMPLETED, S.EXPIRED, S.REJECTED, S.CANCELLED}


def _upsert_client(data: dict, now: datetime) -> Client:
    email = data["email"].strip()
    client = Client.objects.filter(email__iexact=email).first()
    fields = {
        k: data[k]
        for k in ("name", "phone", "messenger", "messenger_handle", "kind", "company")
        if data.get(k)
    }
    if client is None:
        client = Client(email=email, **fields)
    else:
        for k, v in fields.items():
            setattr(client, k, v)
    if data.get("privacy_consent"):
        client.privacy_consent_at = now
    client.save()
    return client


def create_booking(
    *,
    service: Service,
    start: datetime,
    client_data: dict,
    party_size: int = 1,
    details: dict | None = None,
    comment: str = "",
    now: datetime | None = None,
) -> Booking:
    """Create a booking request from the public booking form.

    `client_data` needs at least name, email and privacy_consent=True.
    Raises BookingError / SlotUnavailable with a user-facing message.
    """
    now = now or timezone.now()
    policy = BookingPolicy.get()

    if not client_data.get("privacy_consent"):
        raise BookingError("Please accept the privacy policy and terms.")
    if not (1 <= party_size <= service.max_group_size):
        raise BookingError(f"Group size must be between 1 and {service.max_group_size}.")

    problem = availability_problem(service, start, now=now, policy=policy)
    if problem:
        raise SlotUnavailable(problem)

    price = service.total_price(party_size)
    deposit = None
    if service.takes_deposit and price is not None:
        deposit = (price * Decimal(service.deposit_percent) / 100).quantize(Decimal("0.01"))

    if deposit:
        status, expires_at = S.AWAITING_PAYMENT, now + policy.payment_ttl
    elif service.requires_manual_confirmation:
        status, expires_at = S.PENDING, now + policy.pending_ttl
    else:
        status, expires_at = S.CONFIRMED, None

    try:
        with transaction.atomic():
            client = _upsert_client(client_data, now)
            booking = Booking.objects.create(
                service=service,
                client=client,
                status=status,
                start=start,
                end=start + service.duration,
                party_size=party_size,
                details=details or {},
                comment=comment,
                price_total=price,
                deposit_amount=deposit,
                expires_at=expires_at,
                confirmed_at=now if status == S.CONFIRMED else None,
            )
            BookingEvent.objects.create(
                booking=booking, from_status="", to_status=status, actor=Actor.CLIENT
            )
    except IntegrityError as exc:
        # Lost a race: another request took an overlapping slot first.
        if "booking_no_overlap" in str(exc):
            raise SlotUnavailable("This time has just been taken. Please pick another.") from exc
        raise

    transaction.on_commit(
        lambda: booking_status_changed.send(
            sender=Booking, booking=booking, from_status="", to_status=status, actor=Actor.CLIENT
        )
    )
    return booking


def transition(
    booking: Booking,
    to_status: str,
    *,
    actor: str,
    note: str = "",
    now: datetime | None = None,
) -> Booking:
    """Move a booking to `to_status` if the transition is allowed for `actor`."""
    now = now or timezone.now()
    with transaction.atomic():
        locked = Booking.objects.select_for_update().get(pk=booking.pk)
        from_status = locked.status
        allowed = TRANSITIONS.get(from_status, {}).get(to_status)
        if not allowed or actor not in allowed:
            raise InvalidTransition(
                f"Cannot change booking from {from_status} to {to_status} as {actor}."
            )
        locked.status = to_status
        if to_status in (S.CONFIRMED, S.COMPLETED, *CLOSED_STATUSES):
            locked.expires_at = None
        if to_status == S.PENDING:
            locked.expires_at = now + BookingPolicy.get().pending_ttl
        if to_status == S.CONFIRMED:
            locked.confirmed_at = now
        if to_status in CLOSED_STATUSES:
            locked.closed_at = now
        locked.save()
        BookingEvent.objects.create(
            booking=locked, from_status=from_status, to_status=to_status, actor=actor, note=note
        )

    transaction.on_commit(
        lambda: booking_status_changed.send(
            sender=Booking,
            booking=locked,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
        )
    )
    return locked


def mark_deposit_paid(booking: Booking, *, now: datetime | None = None) -> Booking:
    """Called from the payment webhook once the deposit is captured."""
    target = S.PENDING if booking.service.requires_manual_confirmation else S.CONFIRMED
    return transition(booking, target, actor=Actor.SYSTEM, note="Deposit paid", now=now)


def expire_stale(*, now: datetime | None = None) -> int:
    """Expire unpaid / unconfirmed bookings past their deadline. Run every few minutes."""
    now = now or timezone.now()
    count = 0
    stale = Booking.objects.filter(
        status__in=[S.AWAITING_PAYMENT, S.PENDING], expires_at__lte=now
    ).values_list("pk", flat=True)
    for pk in list(stale):
        try:
            transition(Booking(pk=pk), S.EXPIRED, actor=Actor.SYSTEM, note="Timed out", now=now)
            count += 1
        except InvalidTransition:
            pass  # changed concurrently (e.g. confirmed a moment ago)
    return count


def complete_past(*, now: datetime | None = None) -> int:
    """Mark confirmed bookings as completed once they have ended."""
    now = now or timezone.now()
    count = 0
    for pk in list(
        Booking.objects.filter(status=S.CONFIRMED, end__lte=now).values_list("pk", flat=True)
    ):
        try:
            transition(Booking(pk=pk), S.COMPLETED, actor=Actor.SYSTEM, now=now)
            count += 1
        except InvalidTransition:
            pass
    return count


def is_free_cancellation(booking: Booking, *, now: datetime | None = None) -> bool:
    """Whether the client may still cancel or reschedule without losing the deposit."""
    now = now or timezone.now()
    return booking.start - now >= BookingPolicy.get().free_cancellation


def reschedule(
    booking: Booking, new_start: datetime, *, actor: str, now: datetime | None = None
) -> Booking:
    """Move an active booking to a new start time, keeping its status."""
    now = now or timezone.now()
    if actor == Actor.CLIENT and not is_free_cancellation(booking, now=now):
        raise BookingError("This booking can no longer be rescheduled online. Please contact us.")
    with transaction.atomic():
        locked = Booking.objects.select_for_update().select_related("service").get(pk=booking.pk)
        if not locked.is_active:
            raise BookingError("Only active bookings can be rescheduled.")
        problem = availability_problem(
            locked.service, new_start, now=now, exclude_booking_id=locked.pk
        )
        if problem:
            raise SlotUnavailable(problem)
        old_start = locked.start
        locked.start = new_start
        locked.end = new_start + locked.service.duration
        try:
            with transaction.atomic():
                locked.save()
        except IntegrityError as exc:
            if "booking_no_overlap" in str(exc):
                raise SlotUnavailable("This time has just been taken.") from exc
            raise
        BookingEvent.objects.create(
            booking=locked,
            from_status=locked.status,
            to_status=locked.status,
            actor=actor,
            note=f"Rescheduled from {timezone.localtime(old_start):%Y-%m-%d %H:%M}",
        )
    transaction.on_commit(
        lambda: booking_status_changed.send(
            sender=Booking,
            booking=locked,
            from_status=locked.status,
            to_status=locked.status,
            actor=actor,
        )
    )
    return locked
