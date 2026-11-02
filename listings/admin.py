from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm
from django.db import transaction

from .models import (
    Booking,
    DepositHistory,
    ManagerAuditEvent,
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

    def save_model(self, request, obj, form, change):
        was_active = None
        if change:
            was_active = User.objects.filter(pk=obj.pk).values_list("is_active", flat=True).first()
        with transaction.atomic(using=obj._state.db or "default"):
            super().save_model(request, obj, form, change)
            if was_active is not None and was_active != obj.is_active:
                ManagerAuditEvent.objects.create(
                    actor=request.user,
                    action=(
                        ManagerAuditEvent.ACTION_REACTIVATED
                        if obj.is_active
                        else ManagerAuditEvent.ACTION_SUSPENDED
                    ),
                    target_username=obj.username,
                    target_role=obj.role,
                    details=f"Account status changed in Django admin to {'active' if obj.is_active else 'suspended'}.",
                )

    def delete_model(self, request, obj):
        with transaction.atomic():
            super().delete_model(request, obj)
            ManagerAuditEvent.objects.create(
                actor=request.user,
                action=ManagerAuditEvent.ACTION_DELETED,
                target_username=obj.username,
                target_role=obj.role,
                details="Account deleted in Django admin.",
            )

    def delete_queryset(self, request, queryset):
        audit_details = list(queryset.values_list("username", "role"))
        with transaction.atomic():
            super().delete_queryset(request, queryset)
            ManagerAuditEvent.objects.bulk_create(
                [
                    ManagerAuditEvent(
                        actor=request.user,
                        action=ManagerAuditEvent.ACTION_DELETED,
                        target_username=username,
                        target_role=role,
                        details="Account deleted in a Django admin bulk action.",
                    )
                    for username, role in audit_details
                ]
            )


@admin.register(ManagerAuditEvent)
class ManagerAuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "get_action_display", "target_username", "target_role")
    list_filter = ("action", "created_at")
    search_fields = ("target_username", "actor__username", "details")
    readonly_fields = ("actor", "action", "target_username", "target_role", "details", "created_at")

    def has_add_permission(self, request):
        return False

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
