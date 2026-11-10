"""Public pages and the booking wizard, end to end through HTTP."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.bookings.models import Booking
from apps.catalog.models import Service
from apps.pages.models import ContactMessage
from apps.scheduling.slots import available_slots

pytestmark = pytest.mark.django_db


@pytest.fixture
def site(db):
    call_command("seed_demo", stdout=open("/dev/null", "w"))


def first_free_slot(service):
    day = timezone.localdate() + timedelta(days=2)
    for _ in range(14):
        slots = available_slots(service, day)
        if slots:
            return slots[0]
        day += timedelta(days=1)
    raise AssertionError("no free slot found")


@pytest.mark.parametrize(
    "url",
    [
        "/",
        "/about/",
        "/services/",
        "/services/valletta-walking-tour/",
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
    Service.objects.filter(slug="mdina-rabat-half-day").update(is_active=False)
    html = client.get("/").content.decode()
    assert "Valletta walking tour" in html
    assert "Mdina and Rabat" not in html


def test_unknown_service_page_is_404(site, client):
    assert client.get("/services/no-such-tour/").status_code == 404
    assert client.get("/book/no-such-tour/").status_code == 404


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
    tour = Service.objects.get(slug="valletta-walking-tour")
    start = first_free_slot(tour)

    page = client.get(reverse("book_time", args=[tour.slug]))
    assert page.status_code == 200 and b"day--free" in page.content

    slots = client.get(reverse("book_slots", args=[tour.slug]), {"date": start.date().isoformat()})
    assert start.strftime("%H:%M").encode() in slots.content

    details_url = reverse("book_details", args=[tour.slug])
    assert client.get(details_url, {"start": start.isoformat()}).status_code == 200

    response = client.post(
        details_url,
        {
            "start": start.isoformat(),
            "name": "Guest",
            "email": "guest@example.com",
            "party_size": 2,
            "privacy_consent": "on",
        },
    )
    booking = Booking.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("booking_manage", args=[booking.manage_token])
    assert booking.status == "pending" and booking.start == start

    manage = client.get(response["Location"])
    assert b"Request sent" in manage.content

    # The slot is gone for the next visitor.
    slots = client.get(reverse("book_slots", args=[tour.slug]), {"date": start.date().isoformat()})
    assert f">{start:%H:%M}<".encode() not in slots.content


def test_wizard_reports_taken_slot(site, client):
    tour = Service.objects.get(slug="valletta-walking-tour")
    start = first_free_slot(tour)
    data = {
        "start": start.isoformat(),
        "name": "A",
        "email": "a@example.com",
        "party_size": 1,
        "privacy_consent": "on",
    }
    client.post(reverse("book_details", args=[tour.slug]), data)
    response = client.post(
        reverse("book_details", args=[tour.slug]), {**data, "email": "b@example.com"}
    )
    assert response.status_code == 200
    assert b"already taken" in response.content
    assert Booking.objects.count() == 1


def test_wizard_requires_consent_and_valid_group(site, client):
    tour = Service.objects.get(slug="valletta-walking-tour")
    start = first_free_slot(tour)
    response = client.post(
        reverse("book_details", args=[tour.slug]),
        {"start": start.isoformat(), "name": "A", "email": "a@example.com", "party_size": 99},
    )
    assert response.status_code == 200
    assert not Booking.objects.exists()
    assert response.context["form"].errors.keys() >= {"party_size", "privacy_consent"}


def test_airport_booking_uses_flight_time(site, client):
    airport = Service.objects.get(slug="airport-meet-and-greet")
    assert client.get(reverse("book_time", args=[airport.slug])).status_code == 302
    arrival = timezone.localdate() + timedelta(days=5)
    response = client.post(
        reverse("book_details", args=[airport.slug]),
        {
            "flight_number": "KM101",
            "arrival_date": arrival.isoformat(),
            "arrival_time": "23:40",
            "name": "Late Flyer",
            "email": "late@example.com",
            "party_size": 2,
            "privacy_consent": "on",
        },
    )
    assert response.status_code == 302
    booking = Booking.objects.get()
    assert timezone.localtime(booking.start).strftime("%H:%M") == "23:40"
    assert booking.details["flight_number"] == "KM101"


def test_client_can_cancel_from_booking_page(site, client):
    tour = Service.objects.get(slug="valletta-walking-tour")
    start = first_free_slot(tour)
    client.post(
        reverse("book_details", args=[tour.slug]),
        {
            "start": start.isoformat(),
            "name": "A",
            "email": "a@example.com",
            "party_size": 1,
            "privacy_consent": "on",
        },
    )
    booking = Booking.objects.get()
    response = client.post(reverse("booking_cancel", args=[booking.manage_token]), follow=True)
    booking.refresh_from_db()
    assert booking.status == "cancelled"
    assert b"Booking cancelled" in response.content


def test_unknown_booking_token_is_404(site, client):
    assert client.get(reverse("booking_manage", args=["nope"])).status_code == 404
