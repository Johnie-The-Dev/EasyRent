# listings/models.py (UNIT-FIRST / NO FALLBACK) — DJANGO 5.1 SAFE
from __future__ import annotations

import hashlib
import re
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


# ---------------- Users ----------------

class User(AbstractUser):
    ROLE_CHOICES = (
        ("tenant", "Tenant"),
        ("landlord", "Landlord"),
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="tenant")

    def is_tenant(self) -> bool:
        return self.role == "tenant"

    def is_landlord(self) -> bool:
        return self.role == "landlord"


# ---------------- Password Reset Codes ----------------
# DB-backed (works across restarts and multiple containers/workers)

class PasswordResetCode(models.Model):
    """
    Stores a one-time password reset code for an email address.

    - We store a SHA256 hash (not the raw code).
    - `expires_at` enforces TTL.
    - `used_at` marks the code as spent.
    """

    email = models.EmailField(db_index=True)
    code_hash = models.CharField(max_length=64)  # sha256 hex digest
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email", "used_at", "expires_at"]),
        ]

    def __str__(self) -> str:
        state = "used" if self.used_at else "active"
        return f"PasswordResetCode({self.email}, {state}, expires={self.expires_at:%Y-%m-%d %H:%M})"

    @staticmethod
    def hash_code(code: str) -> str:
        return hashlib.sha256((code or "").encode("utf-8")).hexdigest()

    def is_valid(self) -> bool:
        return (self.used_at is None) and (timezone.now() <= self.expires_at)


# ---------------- Notifications (navbar) ----------------

class Notification(models.Model):
    LEVEL_INFO = "info"
    LEVEL_SUCCESS = "success"
    LEVEL_WARNING = "warning"
    LEVEL_DANGER = "danger"

    LEVEL_CHOICES = [
        (LEVEL_INFO, "Info"),
        (LEVEL_SUCCESS, "Success"),
        (LEVEL_WARNING, "Warning"),
        (LEVEL_DANGER, "Danger"),
    ]

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications_sent",
    )

    message = models.CharField(max_length=255)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default=LEVEL_INFO)
    url = models.CharField(max_length=500, blank=True, default="")

    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"To {self.recipient.username}: {self.message}"


# ---------------- Property (container/listing) ----------------

class Property(models.Model):
    # ✅ Apartment removed (redundant)
    PROPERTY_TYPE_CHOICES = [
        ("Single Room", "Single Room"),
        ("Bed Sitter", "Bed Sitter"),
        ("One Bedroom", "One Bedroom"),
        ("Two Bedroom", "Two Bedroom"),
        ("Three Bedroom", "Three Bedroom"),
    ]

    name = models.CharField(max_length=255)

    video = models.FileField(
        upload_to="property_videos/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(["mp4", "mov", "m4v", "webm"])],
    )

    county = models.CharField(max_length=255, null=True, blank=True)
    town = models.CharField(max_length=255)

    property_type = models.CharField(max_length=50, choices=PROPERTY_TYPE_CHOICES)
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])

    landlord = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="listings_properties",
    )
    landlord_phone = models.CharField(max_length=20)
    description = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["landlord", "created_at"]),
            models.Index(fields=["county", "town"]),
            models.Index(fields=["property_type"]),
        ]

    def __str__(self) -> str:
        return self.name

    # ---- WhatsApp helpers ----
    @property
    def whatsapp_number(self) -> str:
        digits = re.sub(r"\D", "", self.landlord_phone or "")
        if not digits:
            return ""
        if digits.startswith("254") and len(digits) >= 12:
            return digits[:12]
        if digits.startswith("0") and len(digits) >= 10:
            return "254" + digits[1:10]
        if len(digits) == 9 and digits.startswith("7"):
            return "254" + digits
        return digits

    @property
    def whatsapp_link(self) -> str:
        number = self.whatsapp_number
        if not number:
            return ""
        landlord_name = (self.landlord.get_full_name() or self.landlord.username) if self.landlord else ""
        message = f'Hi {landlord_name}, I\'m interested in your listing "{self.name}" (ID {self.id}).'
        return f"https://wa.me/{number}?text={quote(message)}"

    @property
    def total_units(self) -> int:
        return self.units.filter(is_active=True).count()

    @property
    def vacant_units(self) -> int:
        return self.units.filter(is_active=True, vacancy=Unit.VACANT).count()


# ---------------- Unit (inventory) ----------------

class Unit(models.Model):
    # ✅ stays aligned with Property types (Apartment removed automatically)
    UNIT_TYPE_CHOICES = Property.PROPERTY_TYPE_CHOICES

    VACANT = "vacant"
    OCCUPIED = "occupied"
    BOOKED = "booked"
    MAINTENANCE = "maintenance"

    VACANCY_CHOICES = [
        (VACANT, "Vacant"),
        (OCCUPIED, "Occupied"),
        (BOOKED, "Booked"),
        (MAINTENANCE, "Maintenance"),
    ]

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="units")
    unit_code = models.CharField(max_length=50)  # e.g. A1, B2, 101
    unit_type = models.CharField(max_length=50, choices=UNIT_TYPE_CHOICES)

    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    deposit = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])

    vacancy = models.CharField(max_length=20, choices=VACANCY_CHOICES, default=VACANT)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["property", "unit_code"], name="uniq_unit_code_per_property"),
        ]
        indexes = [
            models.Index(fields=["property", "vacancy"]),
            models.Index(fields=["property", "unit_type"]),
            models.Index(fields=["property", "is_active"]),
        ]
        ordering = ["property_id", "unit_code"]

    def __str__(self) -> str:
        return f"{self.property.name} - {self.unit_code}"

    def clean(self):
        self.unit_code = (self.unit_code or "").strip().upper()
        if not self.unit_code:
            raise ValidationError({"unit_code": "Unit code is required (e.g. A1, B3)."})
        super().clean()

    def save(self, *args, **kwargs):
        if self.unit_code:
            self.unit_code = self.unit_code.strip().upper()
        return super().save(*args, **kwargs)


# ---------------- Property Images ----------------

class PropertyImage(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="property_images/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_primary", "uploaded_at"]
        indexes = [
            models.Index(fields=["property", "is_primary"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "is_primary"],
                condition=Q(is_primary=True),
                name="uniq_primary_image_per_property",
            )
        ]

    def __str__(self) -> str:
        return f"Image for {self.property.name}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_primary:
            # Ensure only one primary (guard against admin edits / race-y saves)
            PropertyImage.objects.filter(property=self.property, is_primary=True).exclude(pk=self.pk).update(
                is_primary=False
            )


# ---------------- Tenant collection (saved properties) ----------------

class TenantCollection(models.Model):
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="collections")
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="saved_by")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "property"], name="uniq_collection_tenant_property"),
        ]
        ordering = ["-added_at"]
        indexes = [
            models.Index(fields=["tenant", "added_at"]),
            models.Index(fields=["property", "added_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.tenant.username} - {self.property.name}"


# ---------------- Tenancy / payments ----------------

def get_current_month() -> str:
    return timezone.now().strftime("%Y-%m")


_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


class Tenant(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)

    unit_fk = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="tenancies")

    date_occupied = models.DateField()
    rent_paid = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    date_paid = models.DateField()

    month_paid_for = models.CharField(max_length=7, default=get_current_month)

    monthly_rent = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])

    rent_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cumulative_rent_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        indexes = [
            models.Index(fields=["unit_fk", "month_paid_for"]),
            models.Index(fields=["month_paid_for"]),
        ]
        ordering = ["-date_paid", "name"]

    def __str__(self) -> str:
        return self.name

    @property
    def property(self) -> Property:
        return self.unit_fk.property

    def clean(self):
        if self.month_paid_for and not _MONTH_RE.fullmatch(self.month_paid_for):
            raise ValidationError({"month_paid_for": "Month must be in YYYY-MM format."})
        super().clean()


class ReceiptHistory(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="receipts")
    date_generated = models.DateTimeField(auto_now_add=True)

    rent_paid = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    date_paid = models.DateField()

    month_paid_for = models.CharField(max_length=7)
    monthly_rent = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])

    rent_balance = models.DecimalField(max_digits=10, decimal_places=2)
    cumulative_rent_balance = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["-date_generated"]
        indexes = [
            models.Index(fields=["tenant", "date_generated"]),
            models.Index(fields=["month_paid_for"]),
        ]

    def __str__(self) -> str:
        return f"Receipt for {self.tenant.name} on {self.date_generated:%Y-%m-%d %H:%M}"

    def clean(self):
        if self.month_paid_for and not _MONTH_RE.fullmatch(self.month_paid_for):
            raise ValidationError({"month_paid_for": "Month must be in YYYY-MM format."})
        super().clean()


# ---------------- Deposits (separate from rent) ----------------

class DepositHistory(models.Model):
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deposit_records",
        help_text="Optional link to the tenant's user account (if applicable).",
    )

    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="deposit_records")

    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])

    date_received = models.DateField()
    reference = models.CharField(max_length=120, blank=True, default="")
    notes = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_received", "-created_at"]
        indexes = [
            models.Index(fields=["unit", "date_received"]),
            models.Index(fields=["tenant", "date_received"]),
        ]

    def __str__(self) -> str:
        u = getattr(self.unit, "unit_code", "—")
        p = getattr(getattr(self.unit, "property", None), "name", "—")
        return f"Deposit KES {self.amount} for {p} - {u} on {self.date_received:%Y-%m-%d}"

    def clean(self):
        if self.amount is not None and self.amount < 0:
            raise ValidationError({"amount": "Deposit amount cannot be negative."})
        super().clean()


# ---------------- Bookings ----------------

class Booking(models.Model):
    STATUS_PENDING = "pending"
    STATUS_CONFIRMED = "confirmed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="bookings")
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookings")

    move_in_date = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["unit", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "unit"],
                condition=Q(status__in=["pending", "confirmed"]),
                name="uniq_active_booking_per_tenant_unit",
            )
        ]

    def __str__(self) -> str:
        return f"Booking: {self.tenant.username} -> {self.unit} ({self.status})"

    @property
    def property(self) -> Property:
        return self.unit.property