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
            'landlord_phone': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '07XXXXXXXX or +2547XXXXXXXX or 7XXXXXXXX',
                'pattern': '^(\\+?254|0)?7\\d{8}$',
                'title': 'Kenyan mobile formats accepted: 07XXXXXXXX, 7XXXXXXXX, or +2547XXXXXXXX'
            }),
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

    def clean(self):
        # No phone on the user model; keep for future extension.
        return super().clean()


    
def _normalize_kenyan_phone(phone_raw: str) -> str:
    """Normalize various Kenyan phone inputs into +2547XXXXXXXX format."""
    import re
    if not phone_raw:
        return ''
    digits = re.sub(r'\D', '', phone_raw)
    if not digits:
        return ''
    if digits.startswith('254'):
        return '+' + digits
    if digits.startswith('0'):
        return '+254' + digits[1:]
    if len(digits) == 9 and digits.startswith('7'):
        return '+254' + digits
    return '+' + digits


class PropertyForm(PropertyForm):
    def clean_landlord_phone(self):
        phone = self.cleaned_data.get('landlord_phone', '')
        normalized = _normalize_kenyan_phone(phone)
        if not normalized or len(re.sub(r'\D', '', normalized)) < 9:
            raise forms.ValidationError('Enter a valid Kenyan mobile number (e.g. 07XXXXXXXX).')
        return normalized


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
