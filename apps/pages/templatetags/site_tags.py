from django import template
from wagtail.models import Site

register = template.Library()


@register.simple_tag(takes_context=True)
def menu_pages(context):
    """Live top-level pages marked 'Show in menus' in Wagtail."""
    request = context.get("request")
    site = Site.find_for_request(request) if request else None
    if site is None:
        return []
    return site.root_page.get_children().live().in_menu().specific()


@register.simple_tag(takes_context=True)
def home_url(context):
    request = context.get("request")
    site = Site.find_for_request(request) if request else None
    return site.root_page.url if site else "/"


@register.simple_tag(takes_context=True)
def page_url_by_type(context, model_name):
    """URL of the first live page of a given type, e.g. 'standardpage:privacy'."""
    from wagtail.models import Page

    name, _, slug = model_name.partition(":")
    qs = Page.objects.live().filter(content_type__model=name.lower())
    if slug:
        qs = qs.filter(slug=slug)
    page = qs.first()
    return page.url if page else ""
