from django.urls import path
from django.contrib.auth import views as auth_views
from django.views.generic import TemplateView

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("signup/", views.signup_view, name="signup"),

    # Auth (keeping your existing template)
    path("login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", views.custom_logout, name="logout"),

    # Dashboards / feeds
    path("landlord/dashboard/", views.landlord_dashboard, name="landlord_dashboard"),
    path("tenant/feed/", views.tenant_feed, name="tenant_feed"),

    # Properties
    path("property/add/", views.add_property, name="add_property"),
    path("property/edit/<int:property_id>/", views.edit_property, name="edit_property"),
    path("property/delete/<int:property_id>/", views.delete_property, name="delete_property"),
    path("property/image/delete/<int:image_id>/", views.delete_property_image, name="delete_property_image"),

    # Collection
    path("collection/add/<int:property_id>/", views.add_to_collection, name="add_to_collection"),
    path("collection/remove/<int:property_id>/", views.remove_from_collection, name="remove_from_collection"),
    path("collection/", views.view_collection, name="view_collection"),

    # Static
    path("about/", TemplateView.as_view(template_name="about.html"), name="about"),

    # Password reset
    path("password-reset/", views.RequestPasswordResetCodeView.as_view(), name="password_reset"),
    path("password-reset/verify/", views.VerifyResetCodeView.as_view(), name="password_reset_verify"),
    path("password-reset/new-password/", views.SetNewPasswordView.as_view(), name="password_reset_new_password"),

    # Profiles
    path("landlord/profile/", views.landlord_profile, name="landlord_profile"),
    path("tenant/profile/", views.tenant_profile, name="tenant_profile"),

    # Tenants
    path("manage_tenants/", views.manage_tenants, name="manage_tenants"),
    path("tenant_list/", views.tenant_list, name="tenant_list"),
    path("edit_tenant/<int:tenant_id>/", views.edit_tenant, name="edit_tenant"),
    path("delete_tenant/<int:tenant_id>/", views.delete_tenant, name="delete_tenant"),

    # Receipts
    path("generate_receipt/<int:tenant_id>/", views.generate_receipt, name="generate_receipt"),
    path("receipt_history/<int:tenant_id>/", views.receipt_history, name="receipt_history"),

    # Bookings
    path("property/book/<int:property_id>/", views.book_property, name="book_property"),
    path("landlord/bookings/", views.landlord_bookings, name="landlord_bookings"),
    path("booking/update/<int:booking_id>/", views.update_booking_status, name="update_booking_status"),
    path("tenant/bookings/", views.tenant_bookings, name="tenant_bookings"),
    path("booking/unbook/<int:booking_id>/", views.unbook_property, name="unbook_property"),

    # ✅ Clear cancelled booking (tenant-only)
    path("booking/clear/<int:booking_id>/", views.clear_booking, name="clear_booking"),
]
