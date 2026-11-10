from django.contrib import admin

from .models import ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "created_at", "handled")
    list_filter = ("handled",)
    list_editable = ("handled",)
    search_fields = ("name", "email", "message")
