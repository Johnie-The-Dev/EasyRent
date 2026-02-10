# models.py (UNIT-FIRST / NO FALLBACK) — DJANGO 5.1 SAFE (UPDATED)

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.db.models import Q


# ---------------- Users ----------------

class User(AbstractUser):
    ROLE_CHOICES = (
        ("tenant", "Tenant"),
        ("landlord", "Landlord"),
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="tenant")

    def is_tenant(self):
        return self.role == "tenant"

    def is_landlord(self):
        return self.role == "landlord"


# ---------------- Property (container/listing) ----------------

class Property(models.Model):
    """
    Property = container/listing (building/compound).
    Units are the actual rentable inventory.
    property_type/price are defaults for unit creation + listing display.
    """

    PROPERTY_TYPE_CHOICES = [
        ("Single Room", "Single Room"),
        ("Bed Sitter", "Bed Sitter"),
        ("One Bedroom", "One Bedroom"),
        ("Two Bedroom", "Two Bedroom"),
        ("Three Bedroom", "Three Bedroom"),
        ("Apartment", "Apartment"),
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

    # Defaults for unit creation + listing display
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

    def __str__(self):
        return self.name

    # ---- WhatsApp helpers ----

    @property
    def whatsapp_number(self) -> str:
        """
        Return Kenyan number digits only in 2547XXXXXXXX format, or "" if missing.
        Assumes landlord_phone already normalized by PropertyForm, but remains defensive.
        """
        digits = re.sub(r"\D", "", self.landlord_phone or "")
        if not digits:
            return ""
        if digits.startswith("254") and len(digits) >= 12:
            return digits
        if digits.startswith("0") and len(digits) >= 10:
            return "254" + digits[1:]
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

    # NOTE: READ-ONLY convenience properties.
    # Do not annotate/assign into these names in views.
    @property
    def total_units(self) -> int:
        return self.units.filter(is_active=True).count()

    @property
    def vacant_units(self) -> int:
        return self.units.filter(is_active=True, vacancy="vacant").count()


# ---------------- Unit (inventory) ----------------

class Unit(models.Model):
    """
    Unit = actual rentable inventory item.
    Bookings and occupancy attach here.
    """

    UNIT_TYPE_CHOICES = Property.PROPERTY_TYPE_CHOICES

    STATUS_CHOICES = [
        ("vacant", "Vacant"),
        ("occupied", "Occupied"),
        ("booked", "Booked"),
        ("maintenance", "Maintenance"),
    ]

    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="units")

    unit_code = models.CharField(max_length=50)  # e.g. A1, B2, 101, Shop-03
    unit_type = models.CharField(max_length=50, choices=UNIT_TYPE_CHOICES)

    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    deposit = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])

    vacancy = models.CharField(max_length=20, choices=STATUS_CHOICES, default="vacant")
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

    def __str__(self):
        return f"{self.property.name} - {self.unit_code}"

    def clean(self):
        # Ensure unit_code exists and is normalized
        if self.unit_code:
            self.unit_code = self.unit_code.strip().upper()
        if not self.unit_code:
            raise ValidationError({"unit_code": "Unit code is required (e.g. A1, B3)."})

    def save(self, *args, **kwargs):
        # Normalize consistently even if saved outside forms/views
        if self.unit_code:
            self.unit_code = self.unit_code.strip().upper()
        super().save(*args, **kwargs)


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
            # Ensure at most one primary image per property
            models.UniqueConstraint(
                fields=["property"],
                condition=Q(is_primary=True),
                name="uniq_primary_image_per_property",
            )
        ]

    def __str__(self):
        return f"Image for {self.property.name}"

    def save(self, *args, **kwargs):
        """
        If marking one image as primary, ensure others for the same property are unset.
        This keeps UX clean even if you don't implement a dedicated "Set primary" view yet.
        """
        super().save(*args, **kwargs)
        if self.is_primary:
            PropertyImage.objects.filter(property=self.property).exclude(pk=self.pk).update(is_primary=False)


# ---------------- Tenant collection (saved properties) ----------------

class TenantCollection(models.Model):
    tenant = models.ForeignKey(User, on_delete=models.CASCADE, related_name="collections")
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

    def __str__(self):
        return f"{self.tenant.username} - {self.property.name}"


# ---------------- Tenancy / payments ----------------

def get_current_month() -> str:
    return datetime.now().strftime("%Y-%m")


class Tenant(models.Model):
    """
    Occupancy/payment record (later: Tenancy/Lease).
    Unit is mandatory. Property is derived via unit_fk.property.
    """

    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)

    unit_fk = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name="tenancies",
    )

    date_occupied = models.DateField()
    rent_paid = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    date_paid = models.DateField()

    # YYYY-MM
    month_paid_for = models.CharField(max_length=7, default=get_current_month)

    monthly_rent = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])

    # balances can be negative (overpayment)
    rent_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cumulative_rent_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        indexes = [
            models.Index(fields=["unit_fk", "month_paid_for"]),
            models.Index(fields=["month_paid_for"]),
        ]
        ordering = ["-date_paid", "name"]

    def __str__(self):
        return self.name

    @property
    def property(self):
        return self.unit_fk.property

    def clean(self):
        # enforce YYYY-MM format strictly
        if self.month_paid_for:
            if not re.fullmatch(r"\d{4}-\d{2}", self.month_paid_for):
                raise ValidationError({"month_paid_for": "Month must be in YYYY-MM format."})


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

    def __str__(self):
        return f"Receipt for {self.tenant.name} on {self.date_generated:%Y-%m-%d %H:%M}"

    def clean(self):
        if self.month_paid_for and not re.fullmatch(r"\d{4}-\d{2}", self.month_paid_for):
            raise ValidationError({"month_paid_for": "Month must be in YYYY-MM format."})


# ---------------- Bookings ----------------

class Booking(models.Model):
    """
    Booking targets a Unit (required). Property is derived via unit.property.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("cancelled", "Cancelled"),
    ]

    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="bookings")
    tenant = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bookings")

    move_in_date = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
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

    def __str__(self):
        return f"Booking: {self.tenant.username} -> {self.unit} ({self.status})"

    @property
    def property(self):
        # READ ONLY: do not assign booking.property anywhere
        return self.unit.property
