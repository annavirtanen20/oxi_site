from django import forms
from django.core.validators import MaxValueValidator

from apps.catalog.models import Service

from .models import Client


class BookingDetailsForm(forms.Form):
    """Contact details plus the fields a specific kind of service needs.

    The time block itself (start and end) is chosen in the previous step and
    passed as hidden fields.
    """

    name = forms.CharField(max_length=120, label="Your name")
    company = forms.CharField(max_length=120, required=False, label="Company or agency")
    email = forms.EmailField(help_text="Confirmation and your booking link go here.")
    phone = forms.CharField(max_length=40, required=False, label="Phone")
    messenger = forms.ChoiceField(
        choices=[("", "Email is fine")] + list(Client.Messenger.choices)[1:],
        required=False,
        label="Preferred messenger",
    )
    party_size = forms.IntegerField(min_value=1, initial=10, label="Group size")
    comment = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        required=False,
        max_length=2000,
        label="Anything else I should know?",
    )
    privacy_consent = forms.BooleanField(label="I accept the privacy policy and booking terms.")

    # Airport meet & greet
    flight_number = forms.CharField(max_length=12, required=False, label="Flight number")
    arrival_time = forms.TimeField(
        required=False,
        label="Scheduled landing (Malta time)",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    name_on_sign = forms.CharField(max_length=80, required=False, label="Name for the sign")
    destination = forms.CharField(
        max_length=200, required=False, label="Where the group goes next (hotel, venue)"
    )
    # Events and other services
    event_name = forms.CharField(max_length=160, required=False, label="Event name")
    venue = forms.CharField(max_length=200, required=False, label="Venue or meeting point")
    dress_code = forms.CharField(max_length=120, required=False, label="Dress code")

    AIRPORT_FIELDS = ["flight_number", "arrival_time", "name_on_sign", "destination"]
    EVENT_FIELDS = ["event_name", "venue", "dress_code"]

    def __init__(self, *args, service: Service, **kwargs):
        super().__init__(*args, **kwargs)
        self.service = service
        party = self.fields["party_size"]
        party.max_value = service.max_group_size
        party.validators.append(MaxValueValidator(service.max_group_size))
        party.widget.attrs["max"] = service.max_group_size
        party.help_text = f"Up to {service.max_group_size} people."
        drop = self.EVENT_FIELDS if self.is_airport else self.AIRPORT_FIELDS
        for name in drop:
            del self.fields[name]
        if self.is_airport:
            self.fields["flight_number"].required = True
        else:
            self.fields["venue"].required = True

    @property
    def is_airport(self) -> bool:
        return self.service.kind == Service.Kind.AIRPORT

    @property
    def service_fields(self):
        names = self.AIRPORT_FIELDS if self.is_airport else self.EVENT_FIELDS
        return [self[name] for name in names]

    def details(self) -> dict:
        keys = self.AIRPORT_FIELDS if self.is_airport else self.EVENT_FIELDS
        out = {}
        for k in keys:
            v = self.cleaned_data.get(k)
            if v:
                out[k] = v.strftime("%H:%M") if hasattr(v, "strftime") else v
        return out

    def client_data(self) -> dict:
        d = self.cleaned_data
        return {
            "name": d["name"],
            "email": d["email"],
            "company": d.get("company", ""),
            "phone": d.get("phone", ""),
            "messenger": d.get("messenger", ""),
            "privacy_consent": d["privacy_consent"],
        }
