# models.py (UNIT-FIRST / NO FALLBACK)

from datetime import datetime

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.db import models


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


class Property(models.Model):
    """
    Property = container/listing (building/compound).
    Units are the actual rentable inventory.
    property_type/price here are defaults for unit creation + listing display.
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
    video = models.FileField(upload_to="property_videos/", null=True, blank=True)
    county = models.CharField(max_length=255, null=True, blank=True)
    town = models.CharField(max_length=255)

    # Defaults for unit generation / listing display
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

    def __str__(self):
        return self.name

    @property
    def whatsapp_number(self):
        import re

        digits = re.sub(r"\D", "", self.landlord_phone or "")
        if not digits:
            return ""
        if digits.startswith("254"):
            return digits
        if digits.startswith("0"):
            return "254" + digits[1:]
        if len(digits) == 9:
            return "254" + digits
        return digits

    @property
    def whatsapp_link(self):
        from urllib.parse import quote

        number = self.whatsapp_number
        if not number:
            return ""
        landlord_name = (self.landlord.get_full_name() or self.landlord.username) if self.landlord else ""
        message = f'Hi {landlord_name}, I\'m interested in your listing "{self.name}" (ID {self.id}).'
        return f"https://wa.me/{number}?text={quote(message)}"

    @property
    def total_units(self):
        return self.units.filter(is_active=True).count()

    @property
    def vacant_units(self):
        return self.units.filter(is_active=True, vacancy="vacant").count()


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
        unique_together = ("property", "unit_code")
        indexes = [
            models.Index(fields=["property", "vacancy"]),
            models.Index(fields=["property", "unit_type"]),
            models.Index(fields=["property", "is_active"]),
        ]
        ordering = ["property_id", "unit_code"]

    def __str__(self):
        return f"{self.property.name} - {self.unit_code}"


class PropertyImage(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="property_images/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_primary", "uploaded_at"]

    def __str__(self):
        return f"Image for {self.property.name}"


class TenantCollection(models.Model):
    tenant = models.ForeignKey(User, on_delete=models.CASCADE)
    property = models.ForeignKey(Property, on_delete=models.CASCADE)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("tenant", "property")

    def __str__(self):
        return f"{self.tenant.username} - {self.property.name}"


def get_current_month():
    return datetime.now().strftime("%Y-%m")


class Tenant(models.Model):
    """
    Occupancy/payment record (later: Tenancy/Lease).
    Unit is mandatory. Property is derived via unit_fk.property.
    """

    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=15)

    unit_fk = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name="tenancies",
    )

    date_occupied = models.DateField()
    rent_paid = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    date_paid = models.DateField()
    month_paid_for = models.CharField(max_length=7, default=get_current_month)

    monthly_rent = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, validators=[MinValueValidator(0)])
    rent_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    cumulative_rent_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])

    class Meta:
        indexes = [
            models.Index(fields=["unit_fk", "month_paid_for"]),
        ]

    def __str__(self):
        return self.name

    @property
    def property(self):
        return self.unit_fk.property


class ReceiptHistory(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    date_generated = models.DateTimeField(auto_now_add=True)

    rent_paid = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    date_paid = models.DateField()
    month_paid_for = models.CharField(max_length=20)

    monthly_rent = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    rent_balance = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    cumulative_rent_balance = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])

    def __str__(self):
        return f"Receipt for {self.tenant.name} on {self.date_generated}"


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
            models.Index(fields=["tenant", "unit", "status"]),
            models.Index(fields=["unit", "status"]),
        ]

    def __str__(self):
        return f"Booking: {self.tenant.username} -> {self.unit} ({self.status})"

    @property
    def property(self):
        return self.unit.property
