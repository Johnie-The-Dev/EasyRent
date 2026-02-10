# listings/forms.py (UNIT-FIRST / DJANGO 5.1 SAFE) — UPDATED (FORMSET + VALIDATION)

import calendar
import re
from datetime import datetime

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db.models import Q
from django.forms import inlineformset_factory, BaseInlineFormSet

from .models import Property, Tenant, Unit, User


# ---------- Auth ----------

class SignUpForm(UserCreationForm):
    role = forms.ChoiceField(
        choices=User.ROLE_CHOICES,
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    class Meta:
        model = User
        fields = ("username", "email", "role", "password1", "password2")
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "password1": forms.PasswordInput(attrs={"class": "form-control"}),
            "password2": forms.PasswordInput(attrs={"class": "form-control"}),
        }


# ---------- Helpers ----------

def _normalize_kenyan_phone(phone_raw: str) -> str:
    """Normalize Kenyan mobile inputs into +2547XXXXXXXX format."""
    if not phone_raw:
        return ""
    digits = re.sub(r"\D", "", phone_raw)
    if not digits:
        return ""
    if digits.startswith("254"):
        return "+" + digits
    if digits.startswith("0"):
        return "+254" + digits[1:]
    if len(digits) == 9 and digits.startswith("7"):
        return "+254" + digits
    return "+" + digits


class MultiFileInput(forms.ClearableFileInput):
    """Django 5.1+ supports allow_multiple_selected on FileInput widgets."""
    allow_multiple_selected = True


# ---------- Property ----------

class PropertyForm(forms.ModelForm):
    # Multiple images uploaded; saved to PropertyImage in the view
    property_images = forms.FileField(
        required=False,
        label="Property Images (multiple allowed)",
        help_text="Hold Ctrl/⌘ to select multiple images",
        widget=MultiFileInput(attrs={"class": "form-control", "multiple": True}),
    )

    # Optional: validate video extensions at the form layer too (user-friendly errors)
    video = forms.FileField(
        required=False,
        validators=[FileExtensionValidator(["mp4", "mov", "m4v", "webm"])],
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Property
        fields = [
            "name",
            "county",
            "town",
            "property_type",
            "price",
            "landlord_phone",
            "description",
            "video",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "county": forms.TextInput(attrs={"class": "form-control"}),
            "town": forms.TextInput(attrs={"class": "form-control"}),
            "property_type": forms.Select(attrs={"class": "form-control"}),
            "price": forms.NumberInput(attrs={"class": "form-control"}),
            "landlord_phone": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "07XXXXXXXX or +2547XXXXXXXX or 7XXXXXXXX",
                    "pattern": r"^(\+?254|0)?7\d{8}$",
                    "title": "Kenyan mobile formats accepted: 07XXXXXXXX, 7XXXXXXXX, or +2547XXXXXXXX",
                }
            ),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        }

    def clean_landlord_phone(self):
        phone = (self.cleaned_data.get("landlord_phone") or "").strip()
        normalized = _normalize_kenyan_phone(phone)
        digits = re.sub(r"\D", "", normalized)

        # +2547XXXXXXXX => 12 digits
        if not digits or len(digits) != 12 or not digits.startswith("2547"):
            raise forms.ValidationError("Enter a valid Kenyan mobile number (e.g. 07XXXXXXXX).")

        return normalized

    def clean_property_images(self):
        """
        Validate multiple uploaded images (type + reasonable size).
        Note: Images are saved in the view to PropertyImage model.
        """
        files = self.files.getlist("property_images")
        if not files:
            return None

        max_file_size_mb = 8  # per image
        allowed_content_prefix = "image/"

        for f in files:
            ctype = getattr(f, "content_type", "") or ""
            if ctype and not ctype.startswith(allowed_content_prefix):
                raise ValidationError("Only image files are allowed for property images.")

            if f.size and f.size > max_file_size_mb * 1024 * 1024:
                raise ValidationError(f"Each image must be under {max_file_size_mb}MB.")

        return files

    def clean_video(self):
        """Optional: enforce a size limit for video uploads."""
        f = self.cleaned_data.get("video")
        if not f:
            return None

        max_video_size_mb = 50
        if f.size and f.size > max_video_size_mb * 1024 * 1024:
            raise ValidationError(f"Video must be under {max_video_size_mb}MB.")

        return f


# ---------- Profiles ----------

class LandlordProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "email"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
        }


class TenantProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "email"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
        }


# ---------- Tenancy / Payments ----------

class TenantForm(forms.ModelForm):
    """
    Unit-first tenant/occupancy record.

    In views:
        form = TenantForm(request.POST or None, user=request.user, instance=...)
    """

    current_year = datetime.now().year
    current_month = datetime.now().month
    current_month_str = f"{current_year}-{current_month:02}"

    MONTH_CHOICES = [
        (f"{year}-{month:02}", f"{calendar.month_name[month]} {year}")
        for year in range(2020, 2031)
        for month in range(1, 13)
    ]

    month_paid_for = forms.ChoiceField(
        choices=MONTH_CHOICES,
        label="Month Paid For",
        initial=current_month_str,
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    unit_fk = forms.ModelChoiceField(
        queryset=Unit.objects.none(),
        label="Unit",
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text="Select the specific unit the tenant occupies.",
    )

    class Meta:
        model = Tenant
        fields = [
            "name",
            "phone",
            "unit_fk",
            "date_occupied",
            "rent_paid",
            "date_paid",
            "month_paid_for",
            "monthly_rent",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "placeholder": "07XXXXXXXX"}),
            "date_occupied": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "rent_paid": forms.NumberInput(attrs={"class": "form-control"}),
            "date_paid": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "monthly_rent": forms.NumberInput(attrs={"class": "form-control"}),
        }

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        digits = re.sub(r"\D", "", phone)
        if digits and len(digits) < 9:
            raise ValidationError("Enter a valid phone number.")
        return phone

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        qs = Unit.objects.filter(is_active=True).select_related("property")

        # If landlord, show only their units
        if user and getattr(user, "is_authenticated", False) and hasattr(user, "is_landlord") and user.is_landlord():
            qs = qs.filter(property__landlord=user)

        # Hide occupied units by default; keep current unit visible when editing
        if self.instance and self.instance.pk and getattr(self.instance, "unit_fk_id", None):
            qs = qs.filter(Q(vacancy__in=["vacant", "booked", "maintenance"]) | Q(pk=self.instance.unit_fk_id))
        else:
            qs = qs.filter(vacancy__in=["vacant", "booked", "maintenance"])

        self.fields["unit_fk"].queryset = qs
        self.fields["unit_fk"].label_from_instance = (
            lambda u: f"{u.property.name} — {u.unit_code} ({u.unit_type}) | KES {u.price}"
        )


# ---------- Units (Inline Formset) ----------

class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["unit_code", "unit_type", "price", "deposit", "vacancy", "is_active"]
        widgets = {
            "unit_code": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. A1"}),
            "unit_type": forms.Select(attrs={"class": "form-control"}),
            "price": forms.NumberInput(attrs={"class": "form-control"}),
            "deposit": forms.NumberInput(attrs={"class": "form-control"}),
            "vacancy": forms.Select(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_unit_code(self):
        code = (self.cleaned_data.get("unit_code") or "").strip().upper()
        if not code:
            raise forms.ValidationError("Unit code is required (e.g. A1, B3).")
        return code


class BaseUnitFormSet(BaseInlineFormSet):
    """
    Prevent duplicate unit codes in the same submit (case-insensitive),
    even before hitting the DB UniqueConstraint.
    """

    def clean(self):
        super().clean()

        seen = set()
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if form.cleaned_data.get("DELETE"):
                continue

            code = (form.cleaned_data.get("unit_code") or "").strip().upper()
            if not code:
                continue

            key = code.lower()
            if key in seen:
                raise ValidationError(f"Duplicate unit code '{code}' in this property. Unit codes must be unique.")
            seen.add(key)


# Inline formset: manage units under a property
UnitFormSet = inlineformset_factory(
    parent_model=Property,
    model=Unit,
    form=UnitForm,
    formset=BaseUnitFormSet,
    extra=3,          # show 3 blank rows to add units
    can_delete=True,  # allow deleting units from edit page (optional)
)
