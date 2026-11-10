from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Service(models.Model):
    """A bookable service offered by the guide (ТЗ, раздел 4.1)."""

    class Kind(models.TextChoices):
        TOUR = "tour", "Tour"
        AIRPORT = "airport", "Airport meet & greet"
        REPRESENTATION = "representation", "Ground handling / representation"

    class PriceType(models.TextChoices):
        FIXED = "fixed", "Fixed price"
        PER_HOUR = "per_hour", "Per hour"
        PER_PERSON = "per_person", "Per person"
        ON_REQUEST = "on_request", "On request"

    class Confirmation(models.TextChoices):
        MANUAL = "manual", "Guide confirms manually"
        INSTANT = "instant", "Instant booking"

    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.TOUR)
    short_description = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    includes = models.TextField(blank=True, help_text="What's included, one item per line.")
    image = models.ForeignKey(
        "wagtailimages.Image",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    duration = models.DurationField(default=timedelta(hours=2))
    price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"))
    price_type = models.CharField(max_length=20, choices=PriceType.choices, default=PriceType.FIXED)
    max_group_size = models.PositiveSmallIntegerField(default=8, validators=[MinValueValidator(1)])

    # Travel/preparation time blocked around the service itself.
    buffer_before = models.DurationField(default=timedelta(0))
    buffer_after = models.DurationField(default=timedelta(minutes=30))

    confirmation_mode = models.CharField(
        max_length=10, choices=Confirmation.choices, default=Confirmation.MANUAL
    )
    deposit_percent = models.PositiveSmallIntegerField(
        default=0,
        validators=[MaxValueValidator(100)],
        help_text="0 = no online deposit; the booking goes straight to the guide.",
    )
    # Airport pickups follow flight times, which may fall outside working hours.
    respects_working_hours = models.BooleanField(default=True)

    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.duration <= timedelta(0):
            raise ValidationError({"duration": "Duration must be positive."})
        if self.buffer_before < timedelta(0) or self.buffer_after < timedelta(0):
            raise ValidationError("Buffers cannot be negative.")

    @property
    def includes_list(self) -> list[str]:
        return [line.strip() for line in self.includes.splitlines() if line.strip()]

    @property
    def duration_label(self) -> str:
        minutes = int(self.duration.total_seconds() // 60)
        hours, rest = divmod(minutes, 60)
        if hours and rest:
            return f"{hours} h {rest} min"
        return f"{hours} h" if hours else f"{rest} min"

    @property
    def price_label(self) -> str:
        if self.price_type == self.PriceType.ON_REQUEST:
            return "Price on request"
        amount = (
            f"€{self.price:,.0f}"
            if self.price == self.price.to_integral()
            else f"€{self.price:,.2f}"
        )
        suffix = {
            self.PriceType.PER_HOUR: " per hour",
            self.PriceType.PER_PERSON: " per person",
        }.get(self.price_type, "")
        return amount + suffix

    @property
    def requires_manual_confirmation(self) -> bool:
        # A price "on request" always needs the guide to look at it first.
        return (
            self.confirmation_mode == self.Confirmation.MANUAL
            or self.price_type == self.PriceType.ON_REQUEST
        )

    @property
    def takes_deposit(self) -> bool:
        return self.deposit_percent > 0 and self.price_type != self.PriceType.ON_REQUEST

    def total_price(self, party_size: int) -> Decimal | None:
        """Price for a booking; None when the price is agreed individually."""
        if self.price_type == self.PriceType.ON_REQUEST:
            return None
        if self.price_type == self.PriceType.PER_PERSON:
            return self.price * party_size
        if self.price_type == self.PriceType.PER_HOUR:
            hours = Decimal(self.duration.total_seconds()) / Decimal(3600)
            return (self.price * hours).quantize(Decimal("0.01"))
        return self.price
