from django import forms
from django.conf import settings
from django.core.mail import send_mail
from django.db import models
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel
from wagtail.contrib.routable_page.models import RoutablePageMixin, path
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Orderable, Page

from apps.catalog.models import Service

from .blocks import BodyBlock, QuestionBlock, ReviewBlock

IMAGE_FK = dict(
    to="wagtailimages.Image", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
)


@register_setting(icon="cog")
class SiteSettings(BaseSiteSetting):
    """Contacts and brand details used in the header, footer and contact buttons."""

    brand_name = models.CharField(max_length=80, default="Your name, Malta guide")
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    whatsapp_number = models.CharField(
        max_length=20, blank=True, help_text="International format, digits only: 35699123456"
    )
    telegram_username = models.CharField(max_length=40, blank=True, help_text="Without @")
    service_area = models.CharField(max_length=120, default="Malta, Gozo and Comino")
    licence_note = models.CharField(
        max_length=160, blank=True, help_text="e.g. Licensed tourist guide, Malta Tourism Authority"
    )

    panels = [
        FieldPanel("brand_name"),
        MultiFieldPanel(
            [
                FieldPanel("email"),
                FieldPanel("phone"),
                FieldPanel("whatsapp_number"),
                FieldPanel("telegram_username"),
            ],
            heading="Contacts",
        ),
        FieldPanel("service_area"),
        FieldPanel("licence_note"),
    ]

    @property
    def whatsapp_url(self):
        return f"https://wa.me/{self.whatsapp_number}" if self.whatsapp_number else ""

    @property
    def telegram_url(self):
        return f"https://t.me/{self.telegram_username}" if self.telegram_username else ""


class HomePage(Page):
    hero_heading = models.CharField(max_length=120)
    hero_text = models.TextField(blank=True)
    hero_image = models.ForeignKey(**IMAGE_FK)
    services_heading = models.CharField(max_length=120, default="Tours and services")
    services_intro = models.TextField(blank=True)
    about_heading = models.CharField(max_length=120, blank=True)
    about_text = RichTextField(blank=True, features=["bold", "italic", "link"])
    about_page = models.ForeignKey(
        "wagtailcore.Page", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviews_heading = models.CharField(max_length=120, default="What guests say")
    reviews = StreamField([("review", ReviewBlock())], blank=True)

    max_count = 1
    parent_page_types = ["wagtailcore.Page"]
    content_panels = Page.content_panels + [
        MultiFieldPanel(
            [FieldPanel("hero_heading"), FieldPanel("hero_text"), FieldPanel("hero_image")],
            heading="Top of the page",
        ),
        MultiFieldPanel(
            [FieldPanel("services_heading"), FieldPanel("services_intro")],
            heading="Services list",
        ),
        MultiFieldPanel(
            [FieldPanel("about_heading"), FieldPanel("about_text"), FieldPanel("about_page")],
            heading="About teaser",
        ),
        MultiFieldPanel([FieldPanel("reviews_heading"), FieldPanel("reviews")], heading="Reviews"),
    ]

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["services"] = Service.objects.filter(is_active=True)
        context["services_page"] = ServicesIndexPage.objects.live().first()
        return context


class AboutFact(Orderable):
    page = ParentalKey("AboutPage", related_name="facts", on_delete=models.CASCADE)
    label = models.CharField(max_length=60, help_text="e.g. Languages")
    value = models.CharField(max_length=160, help_text="e.g. English, Russian, Italian")

    panels = [FieldPanel("label"), FieldPanel("value")]


class AboutPage(Page):
    intro = models.TextField(blank=True)
    photo = models.ForeignKey(**IMAGE_FK)
    body = StreamField(BodyBlock(), blank=True)

    max_count = 1
    parent_page_types = ["pages.HomePage"]
    content_panels = Page.content_panels + [
        FieldPanel("intro"),
        FieldPanel("photo"),
        InlinePanel("facts", heading="Key facts", label="Fact"),
        FieldPanel("body"),
    ]


class ServicesIndexPage(RoutablePageMixin, Page):
    """Lists services from the catalog; each service gets its own URL for SEO."""

    intro = models.TextField(blank=True)

    max_count = 1
    parent_page_types = ["pages.HomePage"]
    subpage_types = []
    content_panels = Page.content_panels + [FieldPanel("intro")]

    @path("")
    def index(self, request):
        return self.render(
            request, context_overrides={"services": Service.objects.filter(is_active=True)}
        )

    @path("<slug:slug>/")
    def service_detail(self, request, slug):
        service = get_object_or_404(Service, slug=slug, is_active=True)
        return self.render(
            request,
            template="pages/service_detail.html",
            context_overrides={"service": service, "page_title": service.name},
        )


class FAQPage(Page):
    intro = models.TextField(blank=True)
    questions = StreamField([("question", QuestionBlock())], blank=True)
    policies = RichTextField(
        blank=True,
        features=["h2", "h3", "bold", "italic", "link", "ul", "ol"],
        help_text="Cancellation, payment and other booking terms.",
    )

    max_count = 1
    parent_page_types = ["pages.HomePage"]
    content_panels = Page.content_panels + [
        FieldPanel("intro"),
        FieldPanel("questions"),
        FieldPanel("policies"),
    ]


class ContactMessage(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField()
    message = models.TextField(max_length=3000)
    created_at = models.DateTimeField(auto_now_add=True)
    handled = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.created_at:%Y-%m-%d})"


class ContactForm(forms.Form):
    name = forms.CharField(max_length=120)
    email = forms.EmailField()
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}), max_length=3000)
    privacy_consent = forms.BooleanField(
        label="I agree that my details are used to answer this message."
    )
    # Honeypot: real people never fill this hidden field.
    website = forms.CharField(required=False, widget=forms.HiddenInput)


class ContactPage(Page):
    intro = models.TextField(blank=True)
    thank_you_text = models.CharField(
        max_length=200, default="Thanks, your message is sent. I usually reply within a day."
    )

    max_count = 1
    parent_page_types = ["pages.HomePage"]
    content_panels = Page.content_panels + [FieldPanel("intro"), FieldPanel("thank_you_text")]

    def serve(self, request, *args, **kwargs):
        form = ContactForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            if not form.cleaned_data["website"]:
                msg = ContactMessage.objects.create(
                    name=form.cleaned_data["name"],
                    email=form.cleaned_data["email"],
                    message=form.cleaned_data["message"],
                )
                if settings.GUIDE_NOTIFY_EMAIL:
                    send_mail(
                        f"New message from {msg.name}",
                        f"{msg.message}\n\nReply to: {msg.email}",
                        settings.DEFAULT_FROM_EMAIL,
                        [settings.GUIDE_NOTIFY_EMAIL],
                        fail_silently=True,
                    )
            return redirect(self.url + "?sent=1")
        context = self.get_context(request, *args, **kwargs)
        context.update(form=form, sent=request.GET.get("sent") == "1")
        return TemplateResponse(request, self.get_template(request), context)


class StandardPage(Page):
    """Plain text page: privacy policy, terms and similar."""

    body = RichTextField(features=["h2", "h3", "bold", "italic", "link", "ol", "ul"])

    parent_page_types = ["pages.HomePage"]
    subpage_types = []
    content_panels = Page.content_panels + [FieldPanel("body")]
