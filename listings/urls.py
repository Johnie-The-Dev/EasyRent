from django.urls import path
from django.contrib.auth import views as auth_views
from django.views.generic import TemplateView
from .views import (
    signup_view, landlord_dashboard, tenant_feed, add_property, edit_property, delete_property,
    CustomLoginView, home, add_to_collection, remove_from_collection, view_collection, custom_logout,
    RequestPasswordResetCodeView, VerifyResetCodeView, SetNewPasswordView, landlord_profile, tenant_profile,
    manage_tenants, tenant_list, edit_tenant, delete_tenant, generate_receipt, receipt_history, delete_property_image
)

urlpatterns = [
    path('', home, name='home'),
    path('signup/', signup_view, name='signup'),
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', custom_logout, name='logout'),
    path('landlord/dashboard/', landlord_dashboard, name='landlord_dashboard'),
    path('tenant/feed/', tenant_feed, name='tenant_feed'),
    path('property/add/', add_property, name='add_property'),
    path('property/edit/<int:property_id>/', edit_property, name='edit_property'),
    path('property/delete/<int:property_id>/', delete_property, name='delete_property'),
    path('property/image/delete/<int:image_id>/', delete_property_image, name='delete_property_image'),
    path('collection/add/<int:property_id>/', add_to_collection, name='add_to_collection'),
    path('collection/remove/<int:property_id>/', remove_from_collection, name='remove_from_collection'),
    path('collection/', view_collection, name='view_collection'),
    path('about/', TemplateView.as_view(template_name="about.html"), name='about'),
    path('password-reset/', RequestPasswordResetCodeView.as_view(), name='password_reset'),
    path('password-reset/verify/', VerifyResetCodeView.as_view(), name='password_reset_verify'),
    path('password-reset/new-password/', SetNewPasswordView.as_view(), name='password_reset_new_password'),
    path('landlord/profile/', landlord_profile, name='landlord_profile'),
    path('tenant/profile/', tenant_profile, name='tenant_profile'),
    path('manage_tenants/', manage_tenants, name='manage_tenants'),
    path('tenant_list/', tenant_list, name='tenant_list'),
    path('edit_tenant/<int:tenant_id>/', edit_tenant, name='edit_tenant'),
    path('delete_tenant/<int:tenant_id>/', delete_tenant, name='delete_tenant'),
    path('generate_receipt/<int:tenant_id>/', generate_receipt, name='generate_receipt'),
    path('receipt_history/<int:tenant_id>/', receipt_history, name='receipt_history'),
]
