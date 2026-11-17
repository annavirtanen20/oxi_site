from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def format_duration(value: timedelta) -> str:
    """'4 h', '1 h 30 min', '45 min'."""
    minutes = int(value.total_seconds() // 60)
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} h {rest} min"
    return f"{hours} h" if hours else f"{rest} min"


class Service(models.Model):
    """A bookable service. Clients book a block of time: at least `min_duration`,
    growing in steps of `duration_step` (ТЗ, раздел 4)."""

    class Kind(models.TextChoices):
        AIRPORT = "airport", "Airport meet & greet"
        EVENT = "event", "Event & conference hostess"
        GROUP = "group", "Group escort & ground handling"
        OTHER = "other", "Other"

    class PriceType(models.TextChoices):
        PER_HOUR = "per_hour", "Per hour"
        FIXED = "fixed", "Fixed price per booking"
        PER_PERSON = "per_person", "Per person"
        ON_REQUEST = "on_request", "On request"

    class Confirmation(models.TextChoices):
        MANUAL = "manual", "Confirmed manually"
        INSTANT = "instant", "Instant booking"

    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.EVENT)
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

    # Length of a booking: min_duration, then +duration_step, up to max_duration.
    min_duration = models.DurationField(
        default=timedelta(hours=4), help_text="Shortest block a client can book, e.g. 4:00:00."
    )
    max_duration = models.DurationField(
        null=True, blank=True, help_text="Longest block in one booking. Empty = up to 12 hours."
    )
    duration_step = models.DurationField(
        default=timedelta(hours=1),
        help_text="Blocks start on this grid and grow in these steps, e.g. 1:00:00.",
    )

    price = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"))
    price_type = models.CharField(
        max_length=20, choices=PriceType.choices, default=PriceType.PER_HOUR
    )
    max_group_size = models.PositiveSmallIntegerField(default=50, validators=[MinValueValidator(1)])

    # Time kept free around every booking of this service (travel, preparation).
    buffer_before = models.DurationField(
        default=timedelta(0), help_text="Kept free before each booking, e.g. 1:00:00."
    )
    buffer_after = models.DurationField(
        default=timedelta(0), help_text="Kept free after each booking, e.g. 1:00:00."
    )

    confirmation_mode = models.CharField(
        max_length=10, choices=Confirmation.choices, default=Confirmation.MANUAL
    )
    deposit_percent = models.PositiveSmallIntegerField(
        default=0,
        validators=[MaxValueValidator(100)],
        help_text="0 = no online deposit; the request goes straight to confirmation.",
    )
    respects_working_hours = models.BooleanField(
        default=True,
        verbose_name="Only within working hours",
        help_text="Untick to allow this service at any hour (e.g. night flights).",
    )

    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    def clean(self):
        errors = {}
        if self.duration_step <= timedelta(0):
            errors["duration_step"] = "Must be positive."
        if self.min_duration <= timedelta(0):
            errors["min_duration"] = "Must be positive."
        elif self.duration_step > timedelta(0) and self.min_duration % self.duration_step:
            errors["min_duration"] = "Must be a whole number of steps."
        if self.max_duration is not None and self.max_duration < self.min_duration:
            errors["max_duration"] = "Can't be shorter than the minimum."
        if self.buffer_before < timedelta(0) or self.buffer_after < timedelta(0):
            errors["buffer_after"] = "Buffers can't be negative."
        if errors:
            raise ValidationError(errors)

    @property
    def includes_list(self) -> list[str]:
        return [line.strip() for line in self.includes.splitlines() if line.strip()]

    @property
    def min_duration_label(self) -> str:
        return format_duration(self.min_duration)

    @property
    def step_label(self) -> str:
        return format_duration(self.duration_step)

    @property
    def duration_label(self) -> str:
        """'4 h minimum' or '4–8 h'."""
        if self.max_duration and self.max_duration != self.min_duration:
            return f"{format_duration(self.min_duration)} to {format_duration(self.max_duration)}"
        if self.max_duration:
            return format_duration(self.min_duration)
        return f"from {format_duration(self.min_duration)}"

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
        # A price "on request" always needs a human to look at it first.
        return (
            self.confirmation_mode == self.Confirmation.MANUAL
            or self.price_type == self.PriceType.ON_REQUEST
        )

    @property
    def takes_deposit(self) -> bool:
        return self.deposit_percent > 0 and self.price_type != self.PriceType.ON_REQUEST

    def allowed_duration(self, duration: timedelta) -> bool:
        if duration < self.min_duration or duration % self.duration_step:
            return False
        return self.max_duration is None or duration <= self.max_duration

    def total_price(self, party_size: int, duration: timedelta) -> Decimal | None:
        """Price of one booking; None when the price is agreed individually."""
        if self.price_type == self.PriceType.ON_REQUEST:
            return None
        if self.price_type == self.PriceType.PER_PERSON:
            return self.price * party_size
        if self.price_type == self.PriceType.PER_HOUR:
            hours = Decimal(duration.total_seconds()) / Decimal(3600)
            return (self.price * hours).quantize(Decimal("0.01"))
        return self.price
