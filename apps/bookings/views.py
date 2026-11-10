"""Public booking wizard: service -> date and time -> details -> booking page.

Works without JavaScript (plain links and forms); HTMX only swaps the slot
list in place when a day is picked.
"""

import calendar
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.catalog.models import Service
from apps.scheduling.models import BookingPolicy
from apps.scheduling.slots import available_days, available_slots, business_tz

from .forms import BookingDetailsForm
from .models import Booking, BookingEvent
from .services import (
    BookingError,
    SlotUnavailable,
    create_booking,
    is_free_cancellation,
    transition,
)


def _active_service(slug):
    return get_object_or_404(Service, slug=slug, is_active=True)


def _today():
    return timezone.localdate()


def _parse_month(value: str | None) -> date:
    try:
        year, month = (int(x) for x in (value or "").split("-"))
        return date(year, month, 1)
    except ValueError:
        return _today().replace(day=1)


def _parse_day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _parse_start(value: str | None) -> datetime | None:
    try:
        start = datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    if start and timezone.is_naive(start):
        start = start.replace(tzinfo=business_tz())
    return start


def choose_service(request):
    services = Service.objects.filter(is_active=True)
    return render(request, "bookings/choose_service.html", {"services": services})


def _month_grid(service: Service, month: date) -> dict:
    today = _today()
    policy = BookingPolicy.get()
    last_bookable = (timezone.now() + policy.horizon).astimezone(business_tz()).date()
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)
    first = max(weeks[0][0], today)
    last = min(weeks[-1][-1], last_bookable)
    free = set(available_days(service, first, last)) if first <= last else set()

    prev_month = (month - timedelta(days=1)).replace(day=1)
    next_month = (month + timedelta(days=32)).replace(day=1)
    return {
        "month": month,
        "weeks": [
            [{"date": d, "in_month": d.month == month.month, "free": d in free} for d in week]
            for week in weeks
        ],
        "prev_month": prev_month if prev_month >= today.replace(day=1) else None,
        "next_month": next_month if next_month <= last_bookable else None,
    }


def choose_time(request, slug):
    service = _active_service(slug)
    if not service.respects_working_hours:
        # Airport pickups are booked by flight time, not from the slot grid.
        return redirect("book_details", slug=service.slug)
    month = _parse_month(request.GET.get("month"))
    day = _parse_day(request.GET.get("date"))
    context = {"service": service, **_month_grid(service, month), "day": day}
    if day:
        context["slots"] = available_slots(service, day)
    return render(request, "bookings/choose_time.html", context)


def slots(request, slug):
    service = _active_service(slug)
    day = _parse_day(request.GET.get("date"))
    if not day:
        return HttpResponseBadRequest("date required")
    return render(
        request,
        "bookings/_slots.html",
        {"service": service, "day": day, "slots": available_slots(service, day)},
    )


@require_http_methods(["GET", "POST"])
def details(request, slug):
    service = _active_service(slug)
    start = None
    if service.respects_working_hours:
        start = _parse_start(request.GET.get("start") or request.POST.get("start"))
        if start is None:
            return redirect("book_time", slug=service.slug)

    form = BookingDetailsForm(request.POST or None, service=service)
    if request.method == "POST" and form.is_valid():
        try:
            booking = create_booking(
                service=service,
                start=form.cleaned_data.get("start") or start,
                client_data=form.client_data(),
                party_size=form.cleaned_data["party_size"],
                details=form.details(),
                comment=form.cleaned_data.get("comment", ""),
            )
        except SlotUnavailable as exc:
            form.add_error(None, str(exc))
            form.slot_taken = True
        except BookingError as exc:
            form.add_error(None, str(exc))
        else:
            return redirect("booking_manage", token=booking.manage_token)

    return render(
        request,
        "bookings/details.html",
        {"service": service, "start": start, "form": form},
    )


def _booking_for_token(token) -> Booking:
    booking = Booking.objects.select_related("service", "client").filter(manage_token=token).first()
    if booking is None:
        raise Http404
    return booking


def manage(request, token):
    booking = _booking_for_token(token)
    return render(
        request,
        "bookings/manage.html",
        {
            "booking": booking,
            "free_cancellation": is_free_cancellation(booking),
            "policy": BookingPolicy.get(),
            "just_created": booking.events.count() == 1
            and timezone.now() - booking.created_at < timedelta(minutes=10),
        },
    )


@require_POST
def cancel(request, token):
    booking = _booking_for_token(token)
    try:
        transition(booking, Booking.Status.CANCELLED, actor=BookingEvent.Actor.CLIENT)
        messages.success(request, "Your booking is cancelled.")
    except BookingError as exc:
        messages.error(request, str(exc))
    return redirect(reverse("booking_manage", kwargs={"token": token}))
