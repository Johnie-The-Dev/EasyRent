from django.contrib.auth.models import AbstractUser
from django.db import models
from django.contrib.auth.models import User

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
    county = models.CharField(max_length=255,null=True)  # Added county field
    town = models.CharField(max_length=255)  # Changed from location to town
    property_type = models.CharField(max_length=50, choices=[
        ('Single Room', 'Single Room'),
        ('Bed Sitter', 'Bed Sitter'),
        ('One Bedroom', 'One Bedroom'),
        ('Two Bedroom', 'Two Bedroom'),
        ('Three Bedroom', 'Three Bedroom'),
        ('Apartment', 'Apartment')
    ])
    price = models.DecimalField(max_digits=10, decimal_places=2)
    landlord = models.ForeignKey(User, on_delete=models.CASCADE)
    landlord_phone = models.CharField(max_length=20)
    description = models.TextField(null=True)
    vacancy = models.CharField(max_length=20,null=True, choices=[
        ('vacant', 'Vacant'),
        ('occupied', 'Occupied')
    ])
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
    
class TenantCollection(models.Model):
    tenant = models.ForeignKey(User, on_delete=models.CASCADE)
    property = models.ForeignKey('Property', on_delete=models.CASCADE)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tenant', 'property')

    def __str__(self):
        return f"{self.tenant.username} - {self.property.name}"
