from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from datetime import datetime

class User(AbstractUser):
    ROLE_CHOICES = (
        ('tenant', 'Tenant'),
        ('landlord', 'Landlord'),
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='tenant')

    def is_tenant(self):
        return self.role == 'tenant'

    def is_landlord(self):
        return self.role == 'landlord'

class Property(models.Model):
    name = models.CharField(max_length=255)
    image = models.ImageField(upload_to='property_images/')
    video = models.FileField(upload_to='property_videos/', null=True, blank=True)
    county = models.CharField(max_length=255, null=True)
    town = models.CharField(max_length=255)
    property_type = models.CharField(max_length=50, choices=[
        ('Single Room', 'Single Room'),
        ('Bed Sitter', 'Bed Sitter'),
        ('One Bedroom', 'One Bedroom'),
        ('Two Bedroom', 'Two Bedroom'),
        ('Three Bedroom', 'Three Bedroom'),
        ('Apartment', 'Apartment')
    ])
    price = models.DecimalField(max_digits=10, decimal_places=2)
    landlord = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="listings_properties")
    landlord_phone = models.CharField(max_length=20)
    description = models.TextField(null=True)
    vacancy = models.CharField(max_length=20, null=True, choices=[
        ('vacant', 'Vacant'),
        ('occupied', 'Occupied')
    ])
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class TenantCollection(models.Model):
    tenant = models.ForeignKey(User, on_delete=models.CASCADE)
    property = models.ForeignKey(Property, on_delete=models.CASCADE)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tenant', 'property')

    def __str__(self):
        return f"{self.tenant.username} - {self.property.name}"

def get_current_month():
    return datetime.now().strftime("%Y-%m")

class Tenant(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=15)
    property_name = models.CharField(max_length=255, null=True)
    unit = models.CharField(max_length=50)
    date_occupied = models.DateField()
    rent_paid = models.DecimalField(max_digits=10, decimal_places=2)
    date_paid = models.DateField()
    month_paid_for = models.CharField(max_length=7, default=get_current_month)
    monthly_rent = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    rent_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cumulative_rent_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return self.name

class ReceiptHistory(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    date_generated = models.DateTimeField(auto_now_add=True)
    rent_paid = models.DecimalField(max_digits=10, decimal_places=2)
    date_paid = models.DateField()
    month_paid_for = models.CharField(max_length=20)
    monthly_rent = models.DecimalField(max_digits=10, decimal_places=2)
    rent_balance = models.DecimalField(max_digits=10, decimal_places=2)
    cumulative_rent_balance = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"Receipt for {self.tenant.name} on {self.date_generated}"
