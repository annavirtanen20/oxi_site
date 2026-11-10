"""Everything the guide manages lives in Wagtail (/cms/): bookings, clients, schedule.

Status changes go through apps.bookings.services, exactly as on the public site,
so every change is validated and written to the booking's history.
"""

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from wagtail import hooks
from wagtail.admin.ui.components import Component
from wagtail.admin.views.generic import IndexView, InspectView
from wagtail.admin.viewsets.base import ViewSetGroup
from wagtail.admin.viewsets.model import ModelViewSet
from wagtail.permissions import ModelPermissionPolicy

from apps.scheduling.models import BookingPolicy, TimeOff, WorkingHours

from .models import Booking, BookingEvent, Client
from .services import TRANSITIONS, BookingError, transition

S = Booking.Status
Actor = BookingEvent.Actor

# Button labels and styles for the transitions the guide can trigger.
GUIDE_ACTIONS = {
    S.CONFIRMED: ("Confirm", "button"),
    S.REJECTED: ("Reject", "button button-secondary no"),
    S.COMPLETED: ("Mark as completed", "button button-secondary"),
    S.CANCELLED: ("Cancel booking", "button button-secondary no"),
}


def guide_actions(booking):
    allowed = TRANSITIONS.get(booking.status, {})
    return [
        {"status": status, "label": label, "classes": classes}
        for status, (label, classes) in GUIDE_ACTIONS.items()
        if Actor.GUIDE in allowed.get(status, set())
    ]


class RestrictedPermissionPolicy(ModelPermissionPolicy):
    """Model permissions minus the actions listed in `disabled`."""

    def __init__(self, model, disabled=()):
        super().__init__(model)
        self.disabled = set(disabled)

    def user_has_permission(self, user, action):
        return action not in self.disabled and super().user_has_permission(user, action)

    def user_has_any_permission(self, user, actions):
        return any(self.user_has_permission(user, a) for a in actions)


# --- Bookings -----------------------------------------------------------------


class BookingIndexView(IndexView):
    def get_edit_url(self, instance):
        # Rows open the booking card (with status buttons), not the small edit form.
        return self.get_inspect_url(instance)


class BookingInspectView(InspectView):
    template_name = "bookings/admin/booking_inspect.html"
    transition_url_name = None  # passed in by BookingViewSet

    def get_page_subtitle(self):
        return f"{self.object.service}, {self.object.client.name}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        booking = self.object
        context.update(
            booking=booking,
            actions=guide_actions(booking),
            events=booking.events.all(),
            transition_url_name=self.transition_url_name,
            client_url=reverse("client:inspect", args=[booking.client_id]),
        )
        return context


class BookingViewSet(ModelViewSet):
    model = Booking
    icon = "date"
    menu_label = "Bookings"
    menu_order = 100
    index_view_class = BookingIndexView
    inspect_view_class = BookingInspectView
    inspect_view_enabled = True
    copy_view_enabled = False
    list_display = ["when", "service", "client", "party_size", "status_label"]
    list_filter = ["status", "service"]
    search_fields = ["client__name", "client__email", "comment"]
    ordering = ["-start"]
    # Time and status change only through the card's buttons; these are safe to edit.
    form_fields = ["party_size", "price_total", "comment"]

    @property
    def permission_policy(self):
        # Bookings come from the website and are never deleted (they are the audit trail).
        return RestrictedPermissionPolicy(self.model, disabled={"add", "delete"})

    def get_inspect_view_kwargs(self, **kwargs):
        return super().get_inspect_view_kwargs(
            transition_url_name=self.get_url_name("transition"), **kwargs
        )

    def get_urlpatterns(self):
        return super().get_urlpatterns() + [
            path("<str:pk>/status/<str:to_status>/", self.transition_view, name="transition"),
        ]

    @property
    def transition_view(self):
        viewset = self

        @require_POST
        def view(request, pk, to_status):
            if not viewset.permission_policy.user_has_permission(request.user, "change"):
                raise PermissionDenied
            booking = get_object_or_404(Booking, pk=pk)
            try:
                transition(
                    booking, to_status, actor=Actor.GUIDE, note=request.POST.get("note", "")[:255]
                )
                messages.success(request, f"Booking {S(to_status).label.lower()}.")
            except BookingError as exc:
                messages.error(request, str(exc))
            return redirect(viewset.get_url_name("inspect"), pk)

        return view


# --- Clients ------------------------------------------------------------------


class ClientInspectView(InspectView):
    template_name = "bookings/admin/client_inspect.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["bookings"] = self.object.bookings.select_related("service").order_by("-start")
        context["booking_url_name"] = "booking:inspect"
        return context


class ClientViewSet(ModelViewSet):
    model = Client
    icon = "user"
    menu_label = "Clients"
    menu_order = 110
    inspect_view_class = ClientInspectView
    inspect_view_enabled = True
    copy_view_enabled = False
    list_display = ["name", "email", "phone", "kind", "created_at"]
    list_filter = ["kind"]
    search_fields = ["name", "email", "phone", "company"]
    ordering = ["name"]
    form_fields = ["name", "email", "phone", "messenger", "messenger_handle", "kind", "company", "notes"]
    inspect_view_fields = [
        "name", "email", "phone", "messenger", "messenger_handle", "kind", "company",
        "notes", "privacy_consent_at", "created_at",
    ]

    @property
    def permission_policy(self):
        return RestrictedPermissionPolicy(self.model, disabled={"add"})


# --- Schedule -----------------------------------------------------------------


class WorkingHoursViewSet(ModelViewSet):
    model = WorkingHours
    icon = "time"
    menu_label = "Working hours"
    menu_order = 120
    copy_view_enabled = True
    list_display = ["weekday", "start", "end"]
    form_fields = ["weekday", "start", "end"]
    ordering = ["weekday", "start"]


class TimeOffViewSet(ModelViewSet):
    model = TimeOff
    icon = "pick"
    menu_label = "Days off and busy time"
    menu_order = 130
    copy_view_enabled = False
    list_display = ["start", "end", "reason", "source"]
    list_filter = ["source"]
    form_fields = ["start", "end", "reason"]
    ordering = ["-start"]


class PolicyIndexView(IndexView):
    """There is exactly one policy: the menu item opens its edit form directly."""

    def get(self, request, *args, **kwargs):
        return redirect(self.edit_url_name, BookingPolicy.get().pk)


class BookingPolicyViewSet(ModelViewSet):
    model = BookingPolicy
    icon = "cog"
    menu_label = "Booking rules"
    menu_order = 140
    index_view_class = PolicyIndexView
    copy_view_enabled = False
    form_fields = [
        "slot_step", "min_notice", "horizon", "pending_ttl", "payment_ttl", "free_cancellation",
    ]

    @property
    def permission_policy(self):
        return RestrictedPermissionPolicy(self.model, disabled={"add", "delete"})


class BookingsGroup(ViewSetGroup):
    menu_label = "Bookings"
    menu_icon = "date"
    menu_order = 50
    items = (
        BookingViewSet("booking"),
        ClientViewSet("client"),
        WorkingHoursViewSet("workinghours"),
        TimeOffViewSet("timeoff"),
        BookingPolicyViewSet("bookingpolicy"),
    )


@hooks.register("register_admin_viewset")
def register_bookings_group():
    return BookingsGroup()


# --- CMS home page panel ------------------------------------------------------


class BookingsPanel(Component):
    """'Needs your answer' and 'Coming up' lists on the /cms/ dashboard."""

    name = "bookings"
    order = 50
    template_name = "bookings/admin/home_panel.html"

    def get_context_data(self, parent_context):
        now = timezone.now()
        base = Booking.objects.select_related("service", "client")
        pending = list(base.filter(status=S.PENDING).order_by("start")[:20])
        return {
            "pending": pending,
            "pending_count": str(len(pending)),
            "upcoming": base.filter(
                status=S.CONFIRMED, start__gte=now, start__lt=now + timedelta(days=7)
            ).order_by("start"),
            "index_url": reverse("booking:index"),
        }


@hooks.register("construct_homepage_panels")
def add_bookings_panel(request, panels):
    if request.user.has_perm("bookings.view_booking"):
        panels.insert(0, BookingsPanel())
