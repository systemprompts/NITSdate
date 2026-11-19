import datetime

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from .images import process_image
from .profile_fields import GENDER_CHOICES


class OnboardingForm(forms.Form):
    name = forms.CharField(max_length=60, widget=forms.TextInput(attrs={"autocomplete": "name"}),
                           error_messages={"required": "Enter your name."})
    randomize_name = forms.BooleanField(required=False, initial=False,
                                        label="Show a randomized name to others instead of my real name")
    gender = forms.ChoiceField(choices=GENDER_CHOICES, widget=forms.RadioSelect,
                               error_messages={"required": "Choose your gender."})
    birth_date = forms.DateField(
        label="Date of birth",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        error_messages={
            "required": "Enter your exact date of birth. It's needed to create a profile.",
            "invalid": "Enter a real date, like 2005-08-17.",
        })
    likes = forms.MultipleChoiceField(choices=GENDER_CHOICES, widget=forms.CheckboxSelectMultiple, label="I like",
                                      error_messages={"required": "Pick at least one option."})
    profile_visible = forms.BooleanField(required=False, initial=True,
                                         label="Make my profile info visible to other users")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        self.fields["birth_date"].widget.attrs.update({
            "max": _years_before(today, settings.MIN_AGE).isoformat(),
            "min": _years_before(today, 100).isoformat(),
        })

    def clean_birth_date(self):
        born = self.cleaned_data["birth_date"]
        today = timezone.localdate()
        if born > today:
            raise ValidationError("Your date of birth can't be in the future.")
        if born > _years_before(today, settings.MIN_AGE):
            raise ValidationError(f"You must be {settings.MIN_AGE} or older to join.")
        if born < _years_before(today, 100):
            raise ValidationError("Check the year of your date of birth.")
        return born


def _years_before(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def clean_field_input(field, request):
    """Validate an inline edit from the profile page. Returns the new stored
    value (a ContentFile for photos, "" to clear)."""
    kind = field.kind
    if kind == "birth":
        raise ValidationError("Your age comes from your date of birth, which only a moderator can correct.")
    if kind == "image":
        if request.POST.get("remove"):
            return ""
        upload = request.FILES.get("value")
        if not upload:
            raise ValidationError("Choose a photo to upload.")
        return process_image(upload, max_side=1000)

    if kind == "multi":
        allowed = [c[0] for c in field.choices]
        picked = [v for v in allowed if v in request.POST.getlist("value")]
        if field.required and not picked:
            raise ValidationError(f"Pick at least one option for “{field.label}”.")
        return ",".join(picked)

    raw = request.POST.get("value", "").strip()
    if field.required and not raw:
        raise ValidationError(f"{field.label} can't be empty.")


    if kind == "choice":
        if raw and raw not in dict(field.choices):
            raise ValidationError("Pick one of the options.")
        return raw

    if len(raw) > field.max_length:
        raise ValidationError(f"{field.label} can be at most {field.max_length} characters.")

    if kind == "tags":
        seen, tags = set(), []
        for tag in raw.split(","):
            tag = " ".join(tag.split())[:40]
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                tags.append(tag)
        return ", ".join(tags[:25])
    return raw
