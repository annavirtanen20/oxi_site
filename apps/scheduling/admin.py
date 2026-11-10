from django.contrib import admin

from .models import BookingPolicy, TimeOff, WorkingHours


@admin.register(BookingPolicy)
class BookingPolicyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not BookingPolicy.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WorkingHours)
class WorkingHoursAdmin(admin.ModelAdmin):
    list_display = ("weekday", "start", "end")
    list_filter = ("weekday",)


@admin.register(TimeOff)
class TimeOffAdmin(admin.ModelAdmin):
    list_display = ("start", "end", "reason", "source")
    list_filter = ("source",)
    date_hierarchy = "start"
