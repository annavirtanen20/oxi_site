from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models


class BookingPolicy(models.Model):
    """Guide-editable booking rules (ТЗ, раздел 4.4). Singleton: always pk=1."""

    slot_step = models.DurationField(
        default=timedelta(minutes=30), help_text="Grid for offered start times."
    )
    min_notice = models.DurationField(
        default=timedelta(hours=24), help_text="How far in advance a booking must be made."
    )
    horizon = models.DurationField(
        default=timedelta(days=180), help_text="How far ahead clients can book."
    )
    pending_ttl = models.DurationField(
        default=timedelta(hours=24),
        help_text="Unconfirmed requests expire after this time and free the slot.",
    )
    payment_ttl = models.DurationField(
        default=timedelta(minutes=30),
        help_text="Time a client has to pay the deposit before the slot is released.",
    )
    free_cancellation = models.DurationField(
        default=timedelta(hours=48),
        help_text="Clients can cancel or reschedule for free up to this long before start.",
    )

    class Meta:
        verbose_name = "booking policy"
        verbose_name_plural = "booking policy"

    def __str__(self):
        return "Booking policy"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def clean(self):
        if self.slot_step <= timedelta(0):
            raise ValidationError({"slot_step": "Must be positive."})

    @property
    def free_cancellation_label(self) -> str:
        hours = int(self.free_cancellation.total_seconds() // 3600)
        return f"{hours} hours"

    @classmethod
    def get(cls) -> "BookingPolicy":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class WorkingHours(models.Model):
    """Regular weekly availability. Several intervals per day are allowed."""

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    start = models.TimeField()
    end = models.TimeField()

    class Meta:
        ordering = ["weekday", "start"]
        verbose_name_plural = "working hours"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(start__lt=models.F("end")),
                name="working_hours_start_before_end",
            )
        ]

    def __str__(self):
        return f"{self.get_weekday_display()} {self.start:%H:%M}–{self.end:%H:%M}"


class TimeOff(models.Model):
    """Blocked time: vacations, personal plans, busy slots imported from Google."""

    class Source(models.TextChoices):
        MANUAL = "manual", "Added by the guide"
        GOOGLE = "google", "Google Calendar"

    start = models.DateTimeField()
    end = models.DateTimeField()
    reason = models.CharField(max_length=200, blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    # Google event id for imported busy blocks, so sync can update/delete them.
    external_id = models.CharField(max_length=255, blank=True, db_index=True)

    class Meta:
        ordering = ["start"]
        verbose_name_plural = "time off"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(start__lt=models.F("end")),
                name="time_off_start_before_end",
            ),
            models.UniqueConstraint(
                fields=["source", "external_id"],
                condition=~models.Q(external_id=""),
                name="time_off_unique_external",
            ),
        ]

    def __str__(self):
        return f"{self.start:%Y-%m-%d %H:%M} – {self.end:%Y-%m-%d %H:%M} {self.reason}".strip()
