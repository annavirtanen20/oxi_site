"""Create the page tree, sample services and working hours for a fresh install.

    python manage.py seed_demo

Texts in square brackets are placeholders for the guide to replace in the CMS.
Safe to run once; it refuses to run if a HomePage already exists.
"""

from datetime import time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from wagtail.models import Page, Site

from apps.catalog.models import Service
from apps.pages.models import (
    AboutFact,
    AboutPage,
    ContactPage,
    FAQPage,
    HomePage,
    ServicesIndexPage,
    SiteSettings,
    StandardPage,
)
from apps.scheduling.models import BookingPolicy, WorkingHours

# Prices are placeholders: set real rates in the CMS (Services & prices).
SERVICES = [
    dict(
        name="Airport meet & greet for groups",
        slug="airport-meet-and-greet",
        kind=Service.Kind.AIRPORT,
        short_description="I meet your group in the arrivals hall with a name sign, "
        "check everyone in and see them onto the coach.",
        description="[Describe a typical arrival: flight tracking, waiting for delayed flights, "
        "headcount, luggage, handover to the driver or hotel.]",
        includes="Flight tracking\nName sign in the arrivals hall\nHeadcount and lost-luggage help\n"
        "Handover to the coach driver or hotel",
        min_duration=timedelta(hours=4),
        duration_step=timedelta(hours=1),
        buffer_after=timedelta(hours=1),
        price=Decimal("35"),
        price_type=Service.PriceType.PER_HOUR,
        max_group_size=60,
        respects_working_hours=False,
        order=1,
    ),
    dict(
        name="Conference & event hostess",
        slug="event-hostess",
        kind=Service.Kind.EVENT,
        short_description="Registration desk, guest welcome and guidance at conferences, "
        "exhibitions and corporate events.",
        description="[Describe what you do at events: registration, badges, guiding guests, "
        "speaker support, languages you work in.]",
        includes="Registration and badges\nWelcoming and directing guests\n"
        "Support for speakers and organisers",
        min_duration=timedelta(hours=4),
        duration_step=timedelta(hours=1),
        price=Decimal("30"),
        price_type=Service.PriceType.PER_HOUR,
        max_group_size=500,
        order=2,
    ),
    dict(
        name="Group escort & ground handling",
        slug="group-escort",
        kind=Service.Kind.GROUP,
        short_description="On-site representative for agencies and incentive groups: "
        "transfers, hotel check-in, excursions.",
        description="[Describe what you handle for agencies and DMCs.]",
        min_duration=timedelta(hours=4),
        duration_step=timedelta(hours=1),
        buffer_before=timedelta(minutes=30),
        price=Decimal("0"),
        price_type=Service.PriceType.ON_REQUEST,
        max_group_size=100,
        order=3,
    ),
]

# Services created by earlier versions of this command; hidden when reloading services.
OLD_DEMO_SLUGS = ["valletta-walking-tour", "mdina-rabat-half-day", "group-representation"]


def load_services():
    for data in SERVICES:
        Service.objects.update_or_create(slug=data["slug"], defaults=data)
    Service.objects.filter(slug__in=OLD_DEMO_SLUGS).update(is_active=False)


class Command(BaseCommand):
    help = "Create demo pages, services and working hours."

    def add_arguments(self, parser):
        parser.add_argument(
            "--services-only",
            action="store_true",
            help="Only (re)load the demo services; keep existing pages.",
        )

    @transaction.atomic
    def handle(self, *args, services_only=False, **options):
        if services_only:
            load_services()
            self.stdout.write(self.style.SUCCESS("Demo services loaded."))
            return
        if HomePage.objects.exists():
            raise CommandError("A home page already exists; nothing to do.")

        root = Page.get_first_root_node()
        # Remove Wagtail's default welcome page so our home page can take its place.
        for child in root.get_children():
            if child.specific_class is Page:
                child.delete()
        root.refresh_from_db()

        home = root.add_child(
            instance=HomePage(
                title="Home",
                slug="home",
                hero_heading="Your person on the ground in Malta, from the 5 am flight to the evening gala",
                hero_text=(
                    "Airport meet & greet for groups and hostess work at conferences and events. "
                    "Pick the hours you need and I'll confirm personally."
                ),
                services_heading="Services and rates",
                services_intro="Book any block from 4 hours. Two bookings on one day are fine, "
                "for example an early airport pickup and an evening event.",
                about_heading="[A sentence about who you are]",
                about_text="<p>[Two or three sentences: your experience with groups and events, "
                "the languages you work in, the kind of clients you work with.]</p>",
            )
        )
        site = Site.objects.filter(is_default_site=True).first()
        if site:
            site.root_page = home
            site.site_name = "Oksana Gritsun"
            site.save()
        else:
            site = Site.objects.create(
                hostname="localhost", port=80, root_page=home, is_default_site=True
            )

        about = home.add_child(
            instance=AboutPage(
                title="About me",
                slug="about",
                show_in_menus=True,
                intro="[Your story in two sentences: how you started working with groups "
                "and events in Malta.]",
            )
        )
        for label, value in [
            ("Experience", "[e.g. Groups and events in Malta since 2016]"),
            ("Languages", "[e.g. English, Russian, Italian]"),
            ("Events", "[e.g. Conferences, incentive trips, exhibitions]"),
        ]:
            AboutFact.objects.create(page=about, label=label, value=value)
        about.save_revision().publish()
        home.about_page = about
        home.save_revision().publish()

        home.add_child(
            instance=ServicesIndexPage(
                title="Services",
                slug="services",
                show_in_menus=True,
                intro="For agencies, DMCs and event organisers across Malta and Gozo.",
            )
        )
        home.add_child(
            instance=FAQPage(
                title="Questions",
                slug="faq",
                show_in_menus=True,
                intro="Short answers to what organisers ask most often.",
                questions=[
                    (
                        "question",
                        {
                            "question": "How do I book?",
                            "answer": "<p>Pick a service, a day, the start time and how many hours you need, then send the request. The time is held for you and I confirm by email, usually within a few hours.</p>",
                        },
                    ),
                    (
                        "question",
                        {
                            "question": "Can I cancel?",
                            "answer": "<p>[Your cancellation terms, e.g. free up to 48 hours before the start.]</p>",
                        },
                    ),
                    (
                        "question",
                        {
                            "question": "Can you do an early pickup and an evening event on the same day?",
                            "answer": "<p>Yes. Each booking is at least 4 hours, and you can book several on one day as long as they don't overlap. I keep an hour free after airport pickups for travel.</p>",
                        },
                    ),
                ],
                policies="<h2>Booking terms</h2><p>[Payment, invoicing for companies, cancellation policy.]</p>",
            )
        )
        home.add_child(
            instance=ContactPage(
                title="Contact",
                slug="contact",
                show_in_menus=True,
                intro="Planning a group arrival or an event? Write, I usually answer within a few hours.",
            )
        )
        home.add_child(
            instance=StandardPage(
                title="Privacy policy",
                slug="privacy",
                body="<p>[Privacy policy template. Have it reviewed for GDPR before launch: "
                "what data is collected in the booking form, why, how long it is kept, "
                "which processors are used, and how to request deletion.]</p>",
            )
        )
        home.add_child(
            instance=StandardPage(
                title="Booking terms",
                slug="terms",
                body="<p>[Booking terms: confirmation, payment, cancellation, changes, liability.]</p>",
            )
        )

        settings_obj = SiteSettings.for_site(site)
        settings_obj.brand_name = "Oksana Gritsun"
        settings_obj.email = "hello@example.com"
        settings_obj.whatsapp_number = "35600000000"
        settings_obj.save()

        load_services()

        if not WorkingHours.objects.exists():
            # Round the clock; days off and busy time are closed in the CMS.
            for weekday in range(7):
                WorkingHours.objects.create(weekday=weekday, start=time(0), end=time(0))
        BookingPolicy.get()

        self.stdout.write(self.style.SUCCESS("Demo content created. Open / and /cms/."))
