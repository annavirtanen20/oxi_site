"""Public pages and the booking wizard, end to end through HTTP."""

import os
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.bookings.models import Booking
from apps.catalog.models import Service
from apps.pages.models import ContactMessage
from apps.scheduling.slots import available_ends, available_starts

pytestmark = pytest.mark.django_db

HOSTESS = "event-hostess"
AIRPORT = "airport-meet-and-greet"


@pytest.fixture
def site(db):
    with open(os.devnull, "w") as devnull:
        call_command("seed_demo", stdout=devnull)


def first_block(service, hours=4):
    """First free start in a few days and the end `hours` later."""
    day = timezone.localdate() + timedelta(days=3)
    for _ in range(14):
        for start in available_starts(service, day):
            ends = [
                e for e in available_ends(service, start) if e - start == timedelta(hours=hours)
            ]
            if ends:
                return start, ends[0]
        day += timedelta(days=1)
    raise AssertionError("no free block found")


def details_data(start, end, **extra):
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "name": "Guest",
        "company": "Island Events",
        "email": "guest@example.com",
        "party_size": 20,
        "venue": "Mediterranean Conference Centre",
        "privacy_consent": "on",
        **extra,
    }


@pytest.mark.parametrize(
    "url",
    [
        "/",
        "/about/",
        "/services/",
        f"/services/{HOSTESS}/",
        "/faq/",
        "/contact/",
        "/privacy/",
        "/terms/",
        "/book/",
    ],
)
def test_public_pages_render(site, client, url):
    response = client.get(url)
    assert response.status_code == 200
    assert b"Book a date" in response.content


def test_home_lists_active_services_only(site, client):
    Service.objects.filter(slug="group-escort").update(is_active=False)
    html = client.get("/").content.decode()
    assert "Conference &amp; event hostess" in html
    assert "Group escort" not in html


def test_home_shows_next_free_dates(site, client):
    html = client.get("/").content.decode()
    assert "Next free dates" in html
    assert html.count('class="free-day"') == 3


def test_home_hides_free_dates_without_services(site, client):
    Service.objects.update(is_active=False)
    assert "Next free dates" not in client.get("/").content.decode()


def test_unknown_service_page_is_404(site, client):
    assert client.get("/services/no-such-service/").status_code == 404
    assert client.get("/book/no-such-service/").status_code == 404


def test_contact_form_saves_message(site, client):
    response = client.post(
        "/contact/",
        {"name": "Ann", "email": "ann@example.com", "message": "Hello", "privacy_consent": "on"},
    )
    assert response.status_code == 302
    assert ContactMessage.objects.filter(email="ann@example.com").exists()
    assert b"message is sent" in client.get(response["Location"]).content


def test_contact_honeypot_drops_spam(site, client):
    client.post(
        "/contact/",
        {
            "name": "Bot",
            "email": "bot@example.com",
            "message": "spam",
            "privacy_consent": "on",
            "website": "http://spam",
        },
    )
    assert not ContactMessage.objects.exists()


def test_booking_wizard_happy_path(site, client):
    hostess = Service.objects.get(slug=HOSTESS)
    start, end = first_block(hostess, hours=5)

    page = client.get(reverse("book_time", args=[hostess.slug]))
    assert page.status_code == 200 and b"day--free" in page.content

    day = client.get(reverse("book_day", args=[hostess.slug]), {"date": start.date().isoformat()})
    assert start.strftime("%H:%M").encode() in day.content
    assert b"hours__cell--free" in day.content

    ends = client.get(reverse("book_ends", args=[hostess.slug]), {"start": start.isoformat()})
    assert end.strftime("%H:%M").encode() in ends.content
    assert b"5 h" in ends.content and "€150".encode() in ends.content  # 5 h x €30

    details_url = reverse("book_details", args=[hostess.slug])
    page = client.get(details_url, {"start": start.isoformat(), "end": end.isoformat()})
    assert page.status_code == 200 and b"5 h" in page.content

    response = client.post(details_url, details_data(start, end))
    booking = Booking.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("booking_manage", args=[booking.manage_token])
    assert (booking.start, booking.end) == (start, end)
    assert booking.details == {"venue": "Mediterranean Conference Centre"}
    assert booking.client.company == "Island Events"
    assert b"Request sent" in client.get(response["Location"]).content

    # The hours are gone for the next visitor.
    assert start not in available_starts(hostess, start.date())


def test_no_js_flow_shows_day_and_ends_on_full_page(site, client):
    hostess = Service.objects.get(slug=HOSTESS)
    start, end = first_block(hostess)
    url = reverse("book_time", args=[hostess.slug])
    assert b"Start at" in client.get(url, {"date": start.date().isoformat()}).content
    page = client.get(url, {"start": start.isoformat()})
    assert b"Until" in page.content and end.strftime("%H:%M").encode() in page.content


def test_wizard_reports_taken_block(site, client):
    hostess = Service.objects.get(slug=HOSTESS)
    start, end = first_block(hostess)
    url = reverse("book_details", args=[hostess.slug])
    client.post(url, details_data(start, end, email="a@example.com"))
    response = client.post(url, details_data(start, end, email="b@example.com"))
    assert response.status_code == 200
    assert b"already taken" in response.content
    assert Booking.objects.count() == 1


def test_wizard_refuses_too_short_block(site, client):
    hostess = Service.objects.get(slug=HOSTESS)
    start, _ = first_block(hostess)
    response = client.get(
        reverse("book_details", args=[hostess.slug]),
        {"start": start.isoformat(), "end": (start + timedelta(hours=2)).isoformat()},
    )
    assert b"at least 4 h" in response.content


def test_wizard_requires_consent_and_valid_group(site, client):
    hostess = Service.objects.get(slug=HOSTESS)
    start, end = first_block(hostess)
    data = details_data(start, end, party_size=9999)
    del data["privacy_consent"]
    response = client.post(reverse("book_details", args=[hostess.slug]), data)
    assert response.status_code == 200
    assert not Booking.objects.exists()
    assert response.context["form"].errors.keys() >= {"party_size", "privacy_consent"}


def test_airport_booking_at_five_am_with_flight(site, client):
    airport = Service.objects.get(slug=AIRPORT)
    day = timezone.localdate() + timedelta(days=5)
    start = next(s for s in available_starts(airport, day) if s.hour == 5)
    end = start + timedelta(hours=4)
    response = client.post(
        reverse("book_details", args=[airport.slug]),
        details_data(
            start,
            end,
            flight_number="KM101",
            arrival_time="05:40",
            name_on_sign="Island Events group",
        ),
    )
    assert response.status_code == 302
    booking = Booking.objects.get()
    assert timezone.localtime(booking.start).strftime("%H:%M") == "05:00"
    assert booking.details["flight_number"] == "KM101"
    assert booking.details["arrival_time"] == "05:40"
    # The airport's 1 h travel buffer keeps 09:00 closed, 10:00 is free again.
    starts = [s.hour for s in available_starts(Service.objects.get(slug=HOSTESS), day)]
    assert 9 not in starts and 10 in starts


def test_client_can_cancel_from_booking_page(site, client):
    hostess = Service.objects.get(slug=HOSTESS)
    start, end = first_block(hostess)
    client.post(reverse("book_details", args=[hostess.slug]), details_data(start, end))
    booking = Booking.objects.get()
    response = client.post(reverse("booking_cancel", args=[booking.manage_token]), follow=True)
    booking.refresh_from_db()
    assert booking.status == "cancelled"
    assert b"Booking cancelled" in response.content


def test_unknown_booking_token_is_404(site, client):
    assert client.get(reverse("booking_manage", args=["nope"])).status_code == 404
