from django.urls import path
from django.contrib.auth import views as auth_views
from .views import signup_view
from .views import landlord_dashboard, tenant_feed
from .views import add_property, edit_property, delete_property
from .views import CustomLoginView
from .views import home  # Import your home view
from .views import add_to_collection, remove_from_collection, view_collection
from .views import custom_logout
from django.views.generic import TemplateView
from .views import RequestPasswordResetCodeView, VerifyResetCodeView, SetNewPasswordView

urlpatterns = [
    path('', home, name='home'),  # ✅ Add this line for the homepage
    path('signup/', signup_view, name='signup'),
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', custom_logout, name='logout'),
    path('landlord/dashboard/', landlord_dashboard, name='landlord_dashboard'),
    path('tenant/feed/', tenant_feed, name='tenant_feed'),
    path('property/add/', add_property, name='add_property'),
    path('property/edit/<int:property_id>/', edit_property, name='edit_property'),
    path('property/delete/<int:property_id>/', delete_property, name='delete_property'),
    path('collection/add/<int:property_id>/', add_to_collection, name='add_to_collection'),
    path('collection/remove/<int:property_id>/', remove_from_collection, name='remove_from_collection'),
    path('collection/', view_collection, name='view_collection'),
    path('about/', TemplateView.as_view(template_name="about.html"), name='about'),

    path('password-reset/', RequestPasswordResetCodeView.as_view(), name='password_reset'),
    path('password-reset/verify/', VerifyResetCodeView.as_view(), name='password_reset_verify'),
    path('password-reset/new-password/', SetNewPasswordView.as_view(), name='password_reset_new_password'),
]






