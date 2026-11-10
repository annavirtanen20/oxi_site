from wagtail import blocks
from wagtail.images.blocks import ImageChooserBlock


class ReviewBlock(blocks.StructBlock):
    quote = blocks.TextBlock(help_text="Only real reviews, published with the author's consent.")
    author = blocks.CharBlock(max_length=80)
    origin = blocks.CharBlock(max_length=80, required=False, help_text="e.g. Family from Lyon")

    class Meta:
        icon = "openquote"
        template = "pages/blocks/review.html"


class QuestionBlock(blocks.StructBlock):
    question = blocks.CharBlock(max_length=200)
    answer = blocks.RichTextBlock(features=["bold", "italic", "link", "ul"])

    class Meta:
        icon = "help"
        template = "pages/blocks/question.html"


class ImageBlock(blocks.StructBlock):
    image = ImageChooserBlock()
    caption = blocks.CharBlock(required=False, max_length=200)

    class Meta:
        icon = "image"
        template = "pages/blocks/image.html"


class BodyBlock(blocks.StreamBlock):
    paragraph = blocks.RichTextBlock(features=["h2", "h3", "bold", "italic", "link", "ol", "ul"])
    image = ImageBlock()
    quote = blocks.BlockQuoteBlock()
