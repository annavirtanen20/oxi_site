from django.contrib import admin, messages

from .models import Booking, BookingEvent, Client
from .services import BookingError, transition

S = Booking.Status
Actor = BookingEvent.Actor


class BookingEventInline(admin.TabularInline):
    model = BookingEvent
    extra = 0
    can_delete = False
    readonly_fields = ("created_at", "from_status", "to_status", "actor", "note")

    def has_add_permission(self, request, obj=None):
        return False


def _bulk_transition(modeladmin, request, queryset, to_status):
    done = 0
    for booking in queryset:
        try:
            transition(booking, to_status, actor=Actor.GUIDE)
            done += 1
        except BookingError as exc:
            modeladmin.message_user(request, f"#{booking.pk}: {exc}", messages.WARNING)
    if done:
        modeladmin.message_user(request, f"Updated {done} booking(s).", messages.SUCCESS)


@admin.action(description="Confirm selected requests")
def confirm(modeladmin, request, queryset):
    _bulk_transition(modeladmin, request, queryset, S.CONFIRMED)


@admin.action(description="Reject selected requests")
def reject(modeladmin, request, queryset):
    _bulk_transition(modeladmin, request, queryset, S.REJECTED)


@admin.action(description="Cancel selected bookings")
def cancel(modeladmin, request, queryset):
    _bulk_transition(modeladmin, request, queryset, S.CANCELLED)


@admin.action(description="Mark selected as completed")
def complete(modeladmin, request, queryset):
    _bulk_transition(modeladmin, request, queryset, S.COMPLETED)


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "start", "service", "client", "party_size", "status", "price_total")
    list_filter = ("status", "service")
    search_fields = ("client__name", "client__email", "comment")
    date_hierarchy = "start"
    actions = [confirm, reject, cancel, complete]
    inlines = [BookingEventInline]
    raw_id_fields = ("client",)
    # Status changes only through actions, so every change is validated and logged.
    readonly_fields = (
        "status",
        "manage_token",
        "google_event_id",
        "expires_at",
        "confirmed_at",
        "closed_at",
        "created_at",
        "updated_at",
    )


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "messenger", "kind", "created_at")
    list_filter = ("kind", "messenger")
    search_fields = ("name", "email", "phone", "company")
