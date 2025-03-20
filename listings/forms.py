from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import User, Property, Tenant
import calendar
from datetime import datetime


class SignUpForm(UserCreationForm):
    role = forms.ChoiceField(choices=User.ROLE_CHOICES, widget=forms.Select(attrs={'class': 'form-control'}))

    class Meta:
        model = User
        fields = ('username', 'email', 'role', 'password1', 'password2')
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'password1': forms.PasswordInput(attrs={'class': 'form-control'}),
            'password2': forms.PasswordInput(attrs={'class': 'form-control'}),
        }


class PropertyForm(forms.ModelForm):
    VACANCY_CHOICES = [
        ('vacant', 'Vacant'),
        ('occupied', 'Occupied'),
    ]

    vacancy = forms.ChoiceField(choices=VACANCY_CHOICES, widget=forms.Select(attrs={'class': 'form-control'}))

    class Meta:
        model = Property
        fields = ['name', 'county', 'town', 'property_type', 'price', 'landlord_phone', 'description', 'vacancy', 'image', 'video']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'county': forms.TextInput(attrs={'class': 'form-control'}),
            'town': forms.TextInput(attrs={'class': 'form-control'}),
            'property_type': forms.Select(attrs={'class': 'form-control'}),
            'price': forms.NumberInput(attrs={'class': 'form-control'}),
            'landlord_phone': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control'}),
            'image': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'video': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }


class LandlordProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }


class TenantProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }


class TenantForm(forms.ModelForm):
    current_year = datetime.now().year
    current_month = datetime.now().month
    current_month_str = f"{current_year}-{current_month:02}"

    MONTH_CHOICES = [(f"{year}-{month:02}", f"{calendar.month_name[month]} {year}") for year in range(2020, 2031) for month in range(1, 13)]

    month_paid_for = forms.ChoiceField(choices=MONTH_CHOICES, label="Month Paid For", initial=current_month_str)

    class Meta:
        model = Tenant
        fields = ['name', 'phone', 'property_name', 'unit', 'date_occupied', 'rent_paid', 'date_paid', 'month_paid_for', 'monthly_rent']
