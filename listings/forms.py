# listings/forms.py (UNIT-FIRST / DJANGO 5.1 SAFE)
# UPDATED: SINGLE IMAGE + MODEL VIDEO + BOOKED UNITS ALLOWED IN TENANT FORM
# FIXED: first unit not selectable (remove empty "---------" choice via empty_label=None)
# IMPROVED: unit labels show status tag; stronger unit validation
# ✅ Deposit fields on TenantForm (extra/non-model fields; used ONLY when adding a new tenant)
# ✅ Deposit is NOT rent (does not affect rent fields; views handle saving to DepositHistory)

import calendar
import re
from datetime import datetime

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db.models import Q
from django.forms import inlineformset_factory

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


VIDEO_EXT_VALIDATOR = FileExtensionValidator(["mp4", "mov", "m4v", "webm"])


def _unit_status_values():
    vacant = getattr(Unit, "VACANT", "vacant")
    booked = getattr(Unit, "BOOKED", "booked")
    maintenance = getattr(Unit, "MAINTENANCE", "maintenance")
    occupied = getattr(Unit, "OCCUPIED", "occupied")
    return vacant, booked, maintenance, occupied


def _money_label(val) -> str:
    try:
        return f"{float(val):,.0f}"
    except (TypeError, ValueError):
        return str(val)


# ---------- Property ----------

class PropertyForm(forms.ModelForm):
    """
    - property_image is NOT a model field; you save it into PropertyImage in the view.
    - video IS a model field (Property.video). Keep the name "video" so templates/views match.
    """

    property_image = forms.ImageField(
        required=False,
        label="Property Image (optional)",
        help_text="Upload one image (max 8MB).",
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
            "property_type": forms.Select(attrs={"class": "form-control"}),  # ✅ uses updated choices from model
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
            "video": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

    def clean_landlord_phone(self):
        phone = (self.cleaned_data.get("landlord_phone") or "").strip()
        normalized = _normalize_kenyan_phone(phone)
        digits = re.sub(r"\D", "", normalized)

        if not digits or len(digits) != 12 or not digits.startswith("2547"):
            raise forms.ValidationError("Enter a valid Kenyan mobile number (e.g. 07XXXXXXXX).")

        return normalized

    def clean_property_image(self):
        f = self.files.get("property_image")
        if not f:
            return None

        max_file_size_mb = 8
        ctype = getattr(f, "content_type", "") or ""

        if ctype and not ctype.startswith("image/"):
            raise ValidationError("Only image files are allowed for the property image.")

        if f.size and f.size > max_file_size_mb * 1024 * 1024:
            raise ValidationError(f"Image must be under {max_file_size_mb}MB.")

        return f

    def clean_video(self):
        f = self.cleaned_data.get("video")
        if not f:
            return None

        VIDEO_EXT_VALIDATOR(f)

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
    Deposit fields are extra (non-model) fields:
    - displayed/posted from manage_tenants UI
    - view reads and creates DepositHistory
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
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text="Select the specific unit the tenant occupies (vacant/booked/maintenance only).",
    )

    deposit_amount = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=12,
        label="Deposit Amount (optional)",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "e.g. 10000"}),
        help_text="Deposit is NOT rent. It will be recorded separately when adding a new tenant.",
    )
    deposit_date = forms.DateField(
        required=False,
        label="Deposit Date (optional)",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    deposit_reference = forms.CharField(
        required=False,
        max_length=120,
        label="Deposit Reference (optional)",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "MPESA code / bank ref"}),
    )
    deposit_notes = forms.CharField(
        required=False,
        max_length=255,
        label="Deposit Notes (optional)",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
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

    def clean_unit_fk(self):
        unit = self.cleaned_data.get("unit_fk")
        if not unit:
            return unit

        current_unit_id = getattr(self.instance, "unit_fk_id", None)
        if self.instance and self.instance.pk and current_unit_id == unit.id:
            return unit

        if Tenant.objects.filter(unit_fk=unit).exists():
            raise ValidationError("This unit already has a tenant record. Choose another unit.")

        return unit

    def clean_deposit_reference(self):
        return (self.cleaned_data.get("deposit_reference") or "").strip()

    def clean_deposit_notes(self):
        return (self.cleaned_data.get("deposit_notes") or "").strip()

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        vacant, booked, maintenance, _occupied = _unit_status_values()
        allowed_statuses = [vacant, booked, maintenance]

        qs = Unit.objects.filter(is_active=True).select_related("property")

        if user and getattr(user, "is_authenticated", False) and hasattr(user, "is_landlord") and user.is_landlord():
            qs = qs.filter(property__landlord=user)

        if self.instance and self.instance.pk and getattr(self.instance, "unit_fk_id", None):
            qs = qs.filter(Q(vacancy__in=allowed_statuses) | Q(pk=self.instance.unit_fk_id))
        else:
            qs = qs.filter(vacancy__in=allowed_statuses)

        self.fields["unit_fk"].queryset = qs

        def _label(u: Unit) -> str:
            prop_name = getattr(getattr(u, "property", None), "name", "—")
            code = getattr(u, "unit_code", "—")
            utype = getattr(u, "unit_type", "—")
            price = _money_label(getattr(u, "price", 0))
            status = (getattr(u, "vacancy", "") or "").upper() or "—"
            return f"{prop_name} — {code} ({utype}) | KES {price} [{status}]"

        self.fields["unit_fk"].label_from_instance = _label


# ---------- Units ----------

class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["unit_code", "unit_type", "price", "deposit", "vacancy", "is_active"]
        widgets = {
            "unit_code": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. A1"}),
            "unit_type": forms.Select(attrs={"class": "form-control"}),  # ✅ uses updated choices from model
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


UnitFormSet = inlineformset_factory(
    parent_model=Property,
    model=Unit,
    form=UnitForm,
    extra=3,
    can_delete=True,
)