from wagtail.admin.panels import FieldPanel, FieldRowPanel, MultiFieldPanel
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet

from .models import Service


class ServiceViewSet(SnippetViewSet):
    """Lets the guide edit services and prices in the same CMS as the pages."""

    model = Service
    icon = "list-ul"
    menu_label = "Services & prices"
    menu_order = 200
    add_to_admin_menu = True
    list_display = ["name", "duration_label", "price_label", "is_active"]
    list_filter = ["kind", "is_active"]
    search_fields = ["name"]

    panels = [
        MultiFieldPanel(
            [
                FieldPanel("name"),
                FieldPanel("slug"),
                FieldPanel("kind"),
                FieldPanel("short_description"),
                FieldPanel("description"),
                FieldPanel("includes"),
                FieldPanel("image"),
            ],
            heading="What clients see",
        ),
        MultiFieldPanel(
            [
                FieldRowPanel([FieldPanel("price"), FieldPanel("price_type")]),
                FieldRowPanel([FieldPanel("duration"), FieldPanel("max_group_size")]),
                FieldRowPanel([FieldPanel("buffer_before"), FieldPanel("buffer_after")]),
            ],
            heading="Price and timing",
        ),
        MultiFieldPanel(
            [
                FieldPanel("confirmation_mode"),
                FieldPanel("deposit_percent"),
                FieldPanel("respects_working_hours"),
            ],
            heading="Booking rules",
        ),
        FieldRowPanel([FieldPanel("is_active"), FieldPanel("order")]),
    ]


register_snippet(ServiceViewSet)
