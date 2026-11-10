import secrets

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db import models
from django.db.backends.postgresql.psycopg_any import DateTimeTZRange
from django.db.models.functions import Lower

from apps.catalog.models import Service


class Client(models.Model):
    class Kind(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        AGENCY = "agency", "Agency / company"

    class Messenger(models.TextChoices):
        NONE = "", "—"
        WHATSAPP = "whatsapp", "WhatsApp"
        TELEGRAM = "telegram", "Telegram"
        VIBER = "viber", "Viber"

    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    messenger = models.CharField(max_length=10, choices=Messenger.choices, blank=True)
    messenger_handle = models.CharField(max_length=80, blank=True)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.INDIVIDUAL)
    company = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True, help_text="Private notes, visible to the guide only.")
    # GDPR: when the client accepted the privacy policy and terms.
    privacy_consent_at = models.DateTimeField(null=True, blank=True)
    telegram_chat_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("email"), name="client_email_ci_unique"),
        ]

    def __str__(self):
        return f"{self.name} <{self.email}>"


class Booking(models.Model):
    class Status(models.TextChoices):
        AWAITING_PAYMENT = "awaiting_payment", "Awaiting deposit"
        PENDING = "pending", "Pending confirmation"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        EXPIRED = "expired", "Expired"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    # Statuses that hold the slot. Must match the exclusion constraint below.
    ACTIVE_STATUSES = (Status.AWAITING_PAYMENT, Status.PENDING, Status.CONFIRMED)

    service = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="bookings")
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="bookings")
    status = models.CharField(max_length=20, choices=Status.choices, db_index=True)

    start = models.DateTimeField()
    end = models.DateTimeField()
    # [start - buffer_before, end + buffer_after): what the booking actually blocks.
    blocked_period = DateTimeRangeField(editable=False)

    party_size = models.PositiveSmallIntegerField(default=1)
    # Service-specific fields, e.g. {"flight_number": "KM101", "meeting_point": "..."}.
    details = models.JSONField(default=dict, blank=True)
    comment = models.TextField(blank=True)

    price_total = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    deposit_amount = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)

    manage_token = models.CharField(max_length=64, unique=True, editable=False)
    google_event_id = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(start__lt=models.F("end")), name="booking_start_before_end"
            ),
            # The database itself refuses two active bookings whose blocked periods overlap.
            ExclusionConstraint(
                name="booking_no_overlap",
                expressions=[("blocked_period", RangeOperators.OVERLAPS)],
                condition=models.Q(status__in=["awaiting_payment", "pending", "confirmed"]),
            ),
        ]

    def __str__(self):
        return f"#{self.pk} {self.service} {self.start:%Y-%m-%d %H:%M} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.manage_token:
            self.manage_token = secrets.token_urlsafe(32)
        self.blocked_period = self.compute_blocked_period()
        super().save(*args, **kwargs)

    def compute_blocked_period(self) -> DateTimeTZRange:
        return DateTimeTZRange(
            self.start - self.service.buffer_before,
            self.end + self.service.buffer_after,
            "[)",
        )

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

    def status_label(self) -> str:
        return self.get_status_display()

    status_label.short_description = "Status"

    def when(self) -> str:
        from django.utils import timezone

        start, end = timezone.localtime(self.start), timezone.localtime(self.end)
        return f"{start:%a %d %b %Y, %H:%M}–{end:%H:%M}"

    when.short_description = "When"


class BookingEvent(models.Model):
    """Audit log of every status change (ТЗ, раздел 4.3)."""

    class Actor(models.TextChoices):
        CLIENT = "client", "Client"
        GUIDE = "guide", "Guide"
        SYSTEM = "system", "System"

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="events")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    actor = models.CharField(max_length=10, choices=Actor.choices)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.from_status or '∅'} → {self.to_status} by {self.actor}"
