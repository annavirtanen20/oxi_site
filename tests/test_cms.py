"""The guide's workspace in Wagtail (/cms/)."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.bookings.models import Booking
from apps.bookings.services import create_booking
from apps.catalog.models import Service
from apps.scheduling.models import BookingPolicy
from apps.scheduling.slots import available_slots

pytestmark = pytest.mark.django_db


@pytest.fixture
def guide(client, db):
    user = get_user_model().objects.create_superuser("guide", "g@example.com", "pw")
    client.force_login(user)
    return user


@pytest.fixture
def booking(site):
    tour = Service.objects.get(slug="valletta-walking-tour")
    day = timezone.localdate() + timedelta(days=3)
    while not available_slots(tour, day):
        day += timedelta(days=1)
    return create_booking(
        service=tour,
        start=available_slots(tour, day)[0],
        client_data={"name": "Jane", "email": "jane@example.com", "privacy_consent": True},
        party_size=2,
    )


@pytest.fixture
def site(db):
    from django.core.management import call_command

    call_command("seed_demo", stdout=open("/dev/null", "w"))


def test_cms_pages_open(guide, client, booking):
    for name in ["booking:index", "client:index", "workinghours:index", "timeoff:index"]:
        assert client.get(reverse(name)).status_code == 200, name
    assert client.get(reverse("booking:inspect", args=[booking.pk])).status_code == 200
    assert client.get(reverse("client:inspect", args=[booking.client_id])).status_code == 200


def test_booking_list_links_to_card(guide, client, booking):
    html = client.get(reverse("booking:index")).content.decode()
    assert reverse("booking:inspect", args=[booking.pk]) in html
    assert "Jane" in html


def test_card_shows_allowed_actions(guide, client, booking):
    html = client.get(reverse("booking:inspect", args=[booking.pk])).content.decode()
    assert reverse("booking:transition", args=[booking.pk, "confirmed"]) in html
    assert reverse("booking:transition", args=[booking.pk, "rejected"]) in html
    assert reverse("booking:transition", args=[booking.pk, "completed"]) not in html


def test_guide_confirms_from_cms(guide, client, booking):
    url = reverse("booking:transition", args=[booking.pk, "confirmed"])
    assert client.get(url).status_code == 405  # POST only
    response = client.post(url, follow=True)
    booking.refresh_from_db()
    assert booking.status == "confirmed"
    assert booking.events.last().actor == "guide"
    assert b"Booking confirmed" in response.content


def test_invalid_transition_is_refused(guide, client, booking):
    client.post(reverse("booking:transition", args=[booking.pk, "completed"]), follow=True)
    booking.refresh_from_db()
    assert booking.status == "pending"


def test_bookings_cannot_be_added_or_deleted_in_cms(guide, client, booking):
    assert client.get("/cms/booking/new/").status_code in (302, 403, 404)
    client.post(f"/cms/booking/delete/{booking.pk}/")
    assert Booking.objects.filter(pk=booking.pk).exists()


def test_transition_requires_login(client, booking):
    response = client.post(reverse("booking:transition", args=[booking.pk, "confirmed"]))
    booking.refresh_from_db()
    assert booking.status == "pending"
    assert response.status_code in (302, 403)


def test_policy_menu_opens_edit_form(guide, client, site):
    response = client.get(reverse("bookingpolicy:index"))
    assert response.status_code == 302
    assert response["Location"] == reverse("bookingpolicy:edit", args=[BookingPolicy.get().pk])


def test_dashboard_lists_pending_requests(guide, client, booking):
    html = client.get("/cms/").content.decode()
    assert "Needs your answer (1)" in html
    assert reverse("booking:inspect", args=[booking.pk]) in html
