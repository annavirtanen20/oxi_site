"""Public booking wizard: service -> day -> start -> end -> details -> booking page.

Works without JavaScript (plain links and forms); HTMX only swaps the day panel and
the list of end times in place.
"""

import calendar
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.catalog.models import Service, format_duration
from apps.scheduling.models import BookingPolicy
from apps.scheduling.slots import (
    availability_problem,
    available_days,
    available_ends,
    available_starts,
    business_tz,
    day_timeline,
)

from .forms import BookingDetailsForm
from .models import Booking, BookingEvent, block_label
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


def _parse_dt(value: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    if dt and timezone.is_naive(dt):
        dt = dt.replace(tzinfo=business_tz())
    return dt.astimezone(business_tz()) if dt else None


def _end_options(service: Service, start: datetime) -> list[dict]:
    options = []
    for end in available_ends(service, start):
        price = service.total_price(1, end - start)
        if service.price_type == Service.PriceType.PER_PERSON:
            price_text = f"{service.price_label}"
        elif price is None:
            price_text = ""
        else:
            price_text = f"€{price:,.0f}" if price == price.to_integral() else f"€{price:,.2f}"
        options.append(
            {
                "end": end,
                "length": format_duration(end - start),
                "price": price_text,
                "next_day": end.date() != start.date() and not (end.hour == 0 and end.minute == 0),
            }
        )
    return options


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


def _day_context(service: Service, day: date, start: datetime | None = None) -> dict:
    context = {
        "service": service,
        "day": day,
        "timeline": day_timeline(service, day),
        "starts": available_starts(service, day),
        "start": start,
    }
    if start:
        context["end_options"] = _end_options(service, start)
    return context


def choose_time(request, slug):
    service = _active_service(slug)
    start = _parse_dt(request.GET.get("start"))
    day = start.date() if start else _parse_day(request.GET.get("date"))
    month = _parse_month(request.GET.get("month")) if not day else day.replace(day=1)
    context = {"service": service, **_month_grid(service, month), "day": day}
    if day:
        context.update(_day_context(service, day, start))
    return render(request, "bookings/choose_time.html", context)


def day_panel(request, slug):
    """HTMX: hour strip and start times for one day."""
    service = _active_service(slug)
    day = _parse_day(request.GET.get("date"))
    if not day:
        return HttpResponseBadRequest("date required")
    return render(request, "bookings/_day.html", _day_context(service, day))


def end_options(request, slug):
    """HTMX: possible end times for a chosen start."""
    service = _active_service(slug)
    start = _parse_dt(request.GET.get("start"))
    if not start:
        return HttpResponseBadRequest("start required")
    return render(
        request,
        "bookings/_ends.html",
        {"service": service, "start": start, "end_options": _end_options(service, start)},
    )


@require_http_methods(["GET", "POST"])
def details(request, slug):
    service = _active_service(slug)
    start = _parse_dt(request.GET.get("start") or request.POST.get("start"))
    end = _parse_dt(request.GET.get("end") or request.POST.get("end"))
    if start is None or end is None:
        return redirect("book_time", slug=service.slug)

    form = BookingDetailsForm(request.POST or None, service=service)
    problem = None if request.method == "POST" else availability_problem(service, start, end)
    if request.method == "POST" and form.is_valid():
        try:
            booking = create_booking(
                service=service,
                start=start,
                end=end,
                client_data=form.client_data(),
                party_size=form.cleaned_data["party_size"],
                details=form.details(),
                comment=form.cleaned_data.get("comment", ""),
            )
        except SlotUnavailable as exc:
            problem = str(exc)
        except BookingError as exc:
            form.add_error(None, str(exc))
        else:
            return redirect("booking_manage", token=booking.manage_token)

    price = service.total_price(1, end - start)
    return render(
        request,
        "bookings/details.html",
        {
            "service": service,
            "start": start,
            "end": end,
            "when": block_label(start, end),
            "length": format_duration(end - start),
            "price": price if service.price_type != Service.PriceType.PER_PERSON else None,
            "form": form,
            "problem": problem,
        },
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
            "when": block_label(booking.start, booking.end),
            "length": format_duration(booking.end - booking.start),
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
