from django.contrib import admin

from .models import Service


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "kind",
        "duration",
        "price",
        "price_type",
        "confirmation_mode",
        "deposit_percent",
        "is_active",
        "order",
    )
    list_editable = ("is_active", "order")
    list_filter = ("kind", "is_active", "confirmation_mode")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)
