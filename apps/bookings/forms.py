from datetime import datetime

from django import forms
from django.core.validators import MaxValueValidator

from apps.catalog.models import Service
from apps.scheduling.slots import business_tz

from .models import Client


class BookingDetailsForm(forms.Form):
    """Contact details plus the fields a specific kind of service needs."""

    name = forms.CharField(max_length=120, label="Your name")
    email = forms.EmailField(help_text="Confirmation and your booking link go here.")
    phone = forms.CharField(max_length=40, required=False, label="Phone (optional)")
    messenger = forms.ChoiceField(
        choices=[("", "Email is fine")] + list(Client.Messenger.choices)[1:],
        required=False,
        label="Preferred messenger",
    )
    party_size = forms.IntegerField(min_value=1, initial=2, label="Number of people")
    comment = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        required=False,
        max_length=2000,
        label="Anything I should know?",
        help_text="Interests, mobility needs, children's ages.",
    )
    privacy_consent = forms.BooleanField(
        label="I accept the privacy policy and booking terms."
    )

    # Tours
    meeting_point = forms.CharField(
        max_length=200, required=False, label="Hotel or meeting point (optional)"
    )
    # Airport meet & greet
    flight_number = forms.CharField(max_length=12, required=False, label="Flight number")
    arrival_date = forms.DateField(
        required=False, label="Arrival date", widget=forms.DateInput(attrs={"type": "date"})
    )
    arrival_time = forms.TimeField(
        required=False,
        label="Scheduled arrival (Malta time)",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    name_on_sign = forms.CharField(max_length=80, required=False, label="Name for the sign")
    hotel = forms.CharField(max_length=200, required=False, label="Hotel or address in Malta")

    TOUR_FIELDS = ["meeting_point"]
    AIRPORT_FIELDS = ["flight_number", "arrival_date", "arrival_time", "name_on_sign", "hotel"]

    def __init__(self, *args, service: Service, **kwargs):
        super().__init__(*args, **kwargs)
        self.service = service
        party = self.fields["party_size"]
        party.max_value = service.max_group_size
        party.validators.append(MaxValueValidator(service.max_group_size))
        party.widget.attrs["max"] = service.max_group_size
        self.fields["party_size"].help_text = f"Up to {service.max_group_size}."
        if self.is_airport:
            for f in self.TOUR_FIELDS:
                del self.fields[f]
            for f in ("flight_number", "arrival_date", "arrival_time"):
                self.fields[f].required = True
        else:
            for f in self.AIRPORT_FIELDS:
                del self.fields[f]

    @property
    def is_airport(self) -> bool:
        return not self.service.respects_working_hours

    def clean(self):
        data = super().clean()
        if self.is_airport and data.get("arrival_date") and data.get("arrival_time"):
            data["start"] = datetime.combine(
                data["arrival_date"], data["arrival_time"], tzinfo=business_tz()
            )
        return data

    def details(self) -> dict:
        keys = self.AIRPORT_FIELDS if self.is_airport else self.TOUR_FIELDS
        out = {}
        for k in keys:
            v = self.cleaned_data.get(k)
            if v:
                out[k] = v.isoformat() if hasattr(v, "isoformat") else v
        return out

    def client_data(self) -> dict:
        d = self.cleaned_data
        return {
            "name": d["name"],
            "email": d["email"],
            "phone": d.get("phone", ""),
            "messenger": d.get("messenger", ""),
            "privacy_consent": d["privacy_consent"],
        }
