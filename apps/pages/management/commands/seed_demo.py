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

SERVICES = [
    dict(
        name="Valletta walking tour",
        slug="valletta-walking-tour",
        short_description="The capital's streets, harbour views and the stories behind them.",
        description="[Describe the route, the main stops and what makes your tour different.]",
        includes="Private guide for your group\nRoute adapted to your pace\nCoffee stop recommendations",
        duration=timedelta(hours=3),
        price=Decimal("150"),
        price_type=Service.PriceType.FIXED,
        max_group_size=8,
        order=1,
    ),
    dict(
        name="Mdina and Rabat half day",
        slug="mdina-rabat-half-day",
        short_description="The Silent City and the catacombs, at a quiet morning pace.",
        description="[Describe the half-day itinerary.]",
        includes="Private guide\nPickup from your hotel (transport by taxi, not included)",
        duration=timedelta(hours=4),
        price=Decimal("45"),
        price_type=Service.PriceType.PER_PERSON,
        max_group_size=10,
        order=2,
    ),
    dict(
        name="Airport meet and greet",
        slug="airport-meet-and-greet",
        kind=Service.Kind.AIRPORT,
        short_description="I meet you in the arrivals hall with a name sign and see you to your transfer.",
        description="[Describe what happens on arrival: waiting time, help with luggage, SIM cards, taxi.]",
        includes="Flight tracking\nName sign in the arrivals hall\nHelp with transfer and first questions",
        duration=timedelta(minutes=90),
        price=Decimal("60"),
        price_type=Service.PriceType.FIXED,
        max_group_size=12,
        respects_working_hours=False,
        buffer_before=timedelta(minutes=30),
        order=3,
    ),
    dict(
        name="Group and event representation",
        slug="group-representation",
        kind=Service.Kind.REPRESENTATION,
        short_description="Local representative for agencies, incentive groups and conferences.",
        description="[Describe what you take care of for agencies and event organisers.]",
        duration=timedelta(hours=8),
        price=Decimal("0"),
        price_type=Service.PriceType.ON_REQUEST,
        max_group_size=60,
        order=4,
    ),
]


class Command(BaseCommand):
    help = "Create demo pages, services and working hours."

    @transaction.atomic
    def handle(self, *args, **options):
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
                hero_heading="Malta with a local guide, from the arrivals hall to the last ferry",
                hero_text=(
                    "Private tours, airport meet and greet, and on-the-ground support "
                    "for groups. Pick a free date and I'll confirm personally."
                ),
                services_intro="Every tour is private: just you, your people and me.",
                about_heading="[A sentence about who you are]",
                about_text="<p>[Two or three sentences: how long you have been guiding, "
                "what you love showing, which languages you speak.]</p>",
            )
        )
        site = Site.objects.filter(is_default_site=True).first()
        if site:
            site.root_page = home
            site.site_name = "Malta guide"
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
                intro="[Your story in two sentences: why Malta, why guiding.]",
            )
        )
        for label, value in [
            ("Experience", "[e.g. Guiding in Malta since 2015]"),
            ("Languages", "[e.g. English, Russian]"),
            ("Licence", "[e.g. Licensed tourist guide, Malta Tourism Authority]"),
        ]:
            AboutFact.objects.create(page=about, label=label, value=value)
        about.save_revision().publish()
        home.about_page = about
        home.save_revision().publish()

        home.add_child(
            instance=ServicesIndexPage(
                title="Tours and prices",
                slug="services",
                show_in_menus=True,
                intro="Private tours and services across Malta, Gozo and Comino.",
            )
        )
        home.add_child(
            instance=FAQPage(
                title="Questions",
                slug="faq",
                show_in_menus=True,
                intro="Short answers to what guests ask most often.",
                questions=[
                    (
                        "question",
                        {
                            "question": "How do I book?",
                            "answer": "<p>Pick a service, choose a free time and send the request. I confirm by email, usually within a few hours.</p>",
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
                            "question": "Are entrance tickets included?",
                            "answer": "<p>[Explain what is and isn't included.]</p>",
                        },
                    ),
                ],
                policies="<h2>Booking terms</h2><p>[Payment, deposit, cancellation and weather policy.]</p>",
            )
        )
        home.add_child(
            instance=ContactPage(
                title="Contact",
                slug="contact",
                show_in_menus=True,
                intro="Questions about dates, groups or something special? Write, I usually answer within a day.",
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
        settings_obj.brand_name = "[Your name], Malta guide"
        settings_obj.email = "hello@example.com"
        settings_obj.whatsapp_number = "35600000000"
        settings_obj.save()

        for data in SERVICES:
            Service.objects.update_or_create(slug=data["slug"], defaults=data)

        if not WorkingHours.objects.exists():
            for weekday in range(6):  # Monday to Saturday
                WorkingHours.objects.create(weekday=weekday, start=time(9), end=time(18))
        BookingPolicy.get()

        self.stdout.write(self.style.SUCCESS("Demo content created. Open / and /cms/."))
