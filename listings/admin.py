from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm

from .models import (
    Booking,
    DepositHistory,
    Notification,
    PasswordResetCode,
    Property,
    PropertyImage,
    ReceiptHistory,
    RentPayment,
    Tenant,
    TenantCollection,
    Unit,
    User,
)


class ManagerUserCreationForm(AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = ("username", "email", "first_name", "last_name", "role")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (("EasyRent", {"fields": ("role",)}),)
    add_form = ManagerUserCreationForm
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("EasyRent", {"fields": ("email", "first_name", "last_name", "role")}),
    )
    list_display = ("username", "email", "first_name", "last_name", "role", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff", "is_superuser")
    search_fields = ("username", "email", "first_name", "last_name")


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("name", "town", "county", "landlord", "created_at")
    list_filter = ("county", "town", "property_type")
    search_fields = ("name", "town", "county", "landlord__username")
    list_select_related = ("landlord",)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("unit_code", "property", "unit_type", "vacancy", "is_active", "price")
    list_filter = ("vacancy", "is_active", "unit_type")
    search_fields = ("unit_code", "property__name", "property__landlord__username")
    list_select_related = ("property", "property__landlord")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "unit", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("tenant__username", "unit__unit_code", "unit__property__name")
    list_select_related = ("tenant", "unit", "unit__property")


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "phone", "unit_fk", "is_active", "monthly_rent", "rent_balance")
    list_filter = ("is_active", "month_paid_for")
    search_fields = ("name", "phone", "user__username", "unit_fk__unit_code", "unit_fk__property__name")
    list_select_related = ("unit_fk", "unit_fk__property")


@admin.register(RentPayment)
class RentPaymentAdmin(admin.ModelAdmin):
    list_display = ("tenant", "amount", "date_paid", "month_paid_for", "created_at")
    list_filter = ("month_paid_for", "date_paid", "is_legacy_snapshot")
    search_fields = ("tenant__name", "tenant__phone")
    list_select_related = ("tenant",)


@admin.register(ReceiptHistory)
class ReceiptHistoryAdmin(admin.ModelAdmin):
    list_display = ("tenant", "rent_paid", "date_paid", "month_paid_for", "date_generated")
    list_filter = ("month_paid_for", "date_paid")
    search_fields = ("tenant__name", "tenant__phone")
    list_select_related = ("tenant",)


@admin.register(DepositHistory)
class DepositHistoryAdmin(admin.ModelAdmin):
    list_display = ("unit", "tenant", "tenancy", "amount", "date_received", "reference")
    list_filter = ("date_received",)
    search_fields = ("unit__property__name", "tenant__username", "reference")
    list_select_related = ("unit", "tenant", "tenancy")


@admin.register(PropertyImage)
class PropertyImageAdmin(admin.ModelAdmin):
    list_display = ("property", "is_primary", "uploaded_at")
    list_filter = ("is_primary", "uploaded_at")
    search_fields = ("property__name",)
    list_select_related = ("property",)


@admin.register(TenantCollection)
class TenantCollectionAdmin(admin.ModelAdmin):
    list_display = ("tenant", "property", "added_at")
    list_filter = ("added_at",)
    search_fields = ("tenant__username", "property__name")
    list_select_related = ("tenant", "property")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "actor", "level", "is_read", "created_at")
    list_filter = ("level", "is_read", "created_at")
    search_fields = ("recipient__username", "actor__username", "message")
    list_select_related = ("recipient", "actor")


@admin.register(PasswordResetCode)
class PasswordResetCodeAdmin(admin.ModelAdmin):
    list_display = ("email", "created_at", "expires_at", "used_at")
    list_filter = ("created_at", "expires_at", "used_at")
    search_fields = ("email",)
    readonly_fields = ("email", "code_hash", "created_at", "expires_at", "used_at")
