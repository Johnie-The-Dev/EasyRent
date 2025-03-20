# Create your views here.
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from .forms import SignUpForm, PropertyForm
from .models import Property, TenantCollection
from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.contrib import messages
from django.views import View
from django.utils.crypto import get_random_string
from django.conf import settings
from django.core.paginator import Paginator
from .forms import LandlordProfileForm
from .forms import TenantProfileForm




User = get_user_model()




def signup_view(request):
    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            if user.is_landlord():
                return redirect('landlord_dashboard')
            else:
                return redirect('tenant_feed')
    else:
        form = SignUpForm()
    return render(request, 'signup.html', {'form': form})

@login_required
def landlord_dashboard(request):
    properties = Property.objects.filter(landlord=request.user)
    return render(request, 'landlord_dashboard.html', {'properties': properties})

def tenant_feed(request):
    properties_list = Property.objects.all()

    # Filters (if applied)
    county = request.GET.get('county', '')
    town = request.GET.get('town', '')
    property_type = request.GET.get('property_type', '')
    min_price = request.GET.get('min_price', '')
    max_price = request.GET.get('max_price', '')

    if county:
        properties_list = properties_list.filter(county__icontains=county)
    if town:
        properties_list = properties_list.filter(town__icontains=town)
    if property_type:
        properties_list = properties_list.filter(property_type=property_type)
    if min_price:
        properties_list = properties_list.filter(price__gte=min_price)
    if max_price:
        properties_list = properties_list.filter(price__lte=max_price)

    # Pagination setup (10 properties per page)
    paginator = Paginator(properties_list, 10)
    page_number = request.GET.get("page")
    properties = paginator.get_page(page_number)

    context = {
        "properties": properties,
        "selected_county": county,
        "selected_town": town,
        "selected_type": property_type,
        "selected_min_price": min_price,
        "selected_max_price": max_price,
    }

    return render(request, "tenant_feed.html", context)
@login_required
def add_property(request):
    if request.method == "POST":
        form = PropertyForm(request.POST, request.FILES)
        if form.is_valid():
            property = form.save(commit=False)
            property.landlord = request.user
            property.save()
            return redirect('landlord_dashboard')
    else:
        form = PropertyForm()
    return render(request, 'add_property.html', {'form': form})

@login_required
def edit_property(request, property_id):
    property = get_object_or_404(Property, id=property_id, landlord=request.user)
    if request.method == "POST":
        form = PropertyForm(request.POST, request.FILES, instance=property)
        if form.is_valid():
            form.save()
            return redirect('landlord_dashboard')
    else:
        form = PropertyForm(instance=property)
    return render(request, 'edit_property.html', {'form': form})

@login_required
def delete_property(request, property_id):
    property = get_object_or_404(Property, id=property_id, landlord=request.user)
    if request.method == "POST":
        property.delete()
        return redirect('landlord_dashboard')
    return render(request, 'delete_property.html', {'property': property})

def home(request):
    return render(request, 'home.html')

@login_required
def add_to_collection(request, property_id):
    property = get_object_or_404(Property, id=property_id)
    collection, created = TenantCollection.objects.get_or_create(tenant=request.user, property=property)
    
    if created:
        message = "Property added to your collection!"
    else:
        message = "Property is already in your collection."
    
    return redirect('tenant_feed')

@login_required
def remove_from_collection(request, property_id):
    property = get_object_or_404(Property, id=property_id)
    TenantCollection.objects.filter(tenant=request.user, property=property).delete()
    return redirect('tenant_feed')

@login_required
def view_collection(request):
    collection = TenantCollection.objects.filter(tenant=request.user)
    return render(request, 'collection.html', {'collection': collection})

class CustomLoginView(LoginView):
    template_name = 'login.html'

def custom_logout(request):
    logout(request)
    return redirect('login')  # Ensure 'login' matches your URL pattern name


# Store codes temporarily (use cache or database in production)
reset_codes = {}

class RequestPasswordResetCodeView(View):
    def get(self, request):
        return render(request, 'password_reset_request.html')

    def post(self, request):
        email = request.POST.get('email')
        user = User.objects.filter(email=email).first()
        
        if user:
            code = get_random_string(6, allowed_chars='0123456789')  # 6-digit code
            reset_codes[email] = code  # Store the code
            send_mail(
                'Your Password Reset Code',
                f'Your password reset code is: {code}',
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False,
            )
            messages.success(request, "A reset code has been sent to your email.")
            return redirect('password_reset_verify')

        messages.error(request, "No account found with this email.")
        return redirect('password_reset')

class VerifyResetCodeView(View):
    def get(self, request):
        return render(request, 'password_reset_verify.html')

    def post(self, request):
        email = request.POST.get('email')
        code = request.POST.get('code')

        if reset_codes.get(email) == code:
            request.session['reset_email'] = email  # Store email for next step
            return redirect('password_reset_new_password')

        messages.error(request, "Invalid code. Please try again.")
        return redirect('password_reset_verify')

class SetNewPasswordView(View):
    def get(self, request):
        return render(request, 'password_reset_new_password.html')

    def post(self, request):
        email = request.session.get('reset_email')
        password = request.POST.get('password')

        if email:
            user = User.objects.filter(email=email).first()
            if user:
                user.set_password(password)
                user.save()
                reset_codes.pop(email, None)  # Clear the used code
                messages.success(request, "Password reset successfully. You can now log in.")
                return redirect('login')

        messages.error(request, "Something went wrong. Please try again.")
        return redirect('password_reset')


@login_required
def landlord_profile(request):
    if request.method == "POST":
        form = LandlordProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated successfully!")
            return redirect("landlord_profile")
    else:
        form = LandlordProfileForm(instance=request.user)

    return render(request, "landlord_profile.html", {"form": form})


@login_required
def tenant_profile(request):
    if request.method == 'POST':
        form = TenantProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated successfully!")
            return redirect('tenant_profile')
    else:
        form = TenantProfileForm(instance=request.user)

    return render(request, 'tenant_profile.html', {'form': form})


#property management

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import HttpResponse
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from .models import Tenant
from .forms import TenantForm

def manage_tenants(request):
    if request.method == 'POST':
        form = TenantForm(request.POST)
        if form.is_valid():
            tenant = form.save(commit=False)
            tenant.rent_balance = tenant.monthly_rent - tenant.rent_paid
            tenant.cumulative_rent_balance += tenant.rent_balance
            tenant.save()
            return redirect(reverse('tenant_list'))
    else:
        form = TenantForm()
    return render(request, 'manage_tenants.html', {'form': form})

def edit_tenant(request, tenant_id):
    tenant = get_object_or_404(Tenant, id=tenant_id)
    if request.method == 'POST':
        form = TenantForm(request.POST, instance=tenant)
        if form.is_valid():
            tenant = form.save(commit=False)
            tenant.rent_balance = tenant.monthly_rent - tenant.rent_paid
            tenant.cumulative_rent_balance += tenant.rent_balance
            tenant.save()
            return redirect(reverse('tenant_list'))
    else:
        form = TenantForm(instance=tenant)
    return render(request, 'edit_tenant.html', {'form': form, 'tenant': tenant})

def delete_tenant(request, tenant_id):
    tenant = get_object_or_404(Tenant, id=tenant_id)
    tenant.delete()
    return redirect(reverse('tenant_list'))

def tenant_list(request):
    tenant_list = Tenant.objects.all()
    return render(request, 'tenant_list.html', {'tenant_list': tenant_list})

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from reportlab.lib.pagesizes import A6
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from .models import Tenant

def generate_receipt(request, tenant_id):
    tenant = get_object_or_404(Tenant, id=tenant_id)
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="receipt_{tenant.name}.pdf"'
    
    p = canvas.Canvas(response, pagesize=A6)
    width, height = A6
    
    # Background color
    p.setFillColor(HexColor("#f5f5f5"))
    p.rect(0, 0, width, height, fill=1)
    
    # Header
    p.setFillColor(HexColor("#6a11cb"))
    p.rect(0, height - 20 * mm, width, 20 * mm, fill=1)
    p.setFont("Helvetica-Bold", 14)
    p.setFillColor(HexColor("#ffffff"))
    p.drawString(10 * mm, height - 10 * mm, "EasyRent Receipt")
    
    # Content
    p.setFont("Helvetica", 10)
    p.setFillColor(HexColor("#000000"))
    y = height - 30 * mm
    line_height = 5 * mm
    
    p.drawString(10 * mm, y, f"Tenant: {tenant.name}")
    y -= line_height
    p.drawString(10 * mm, y, f"Phone: {tenant.phone}")
    y -= line_height
    p.drawString(10 * mm, y, f"Property: {tenant.property_name}")
    y -= line_height
    p.drawString(10 * mm, y, f"Unit: {tenant.unit}")
    y -= line_height
    p.drawString(10 * mm, y, f"Date Occupied: {tenant.date_occupied}")
    y -= line_height
    p.drawString(10 * mm, y, f"Rent Paid: {tenant.rent_paid}")
    y -= line_height
    p.drawString(10 * mm, y, f"Date Paid: {tenant.date_paid}")
    y -= line_height
    p.drawString(10 * mm, y, f"Month Paid For: {tenant.month_paid_for}")
    y -= line_height
    p.drawString(10 * mm, y, f"Monthly Rent: {tenant.monthly_rent}")
    y -= line_height
    p.drawString(10 * mm, y, f"Rent Balance: {tenant.rent_balance}")
    y -= line_height
    p.drawString(10 * mm, y, f"Cumulative Rent Balance: {tenant.cumulative_rent_balance}")
    
    # Footer
    p.setFont("Helvetica-Bold", 10)
    p.setFillColor(HexColor("#6a11cb"))
    p.drawString(10 * mm, y - 2 * line_height, "Thank you for your payment!")
    
    # Border
    p.setStrokeColor(HexColor("#6a11cb"))
    p.setLineWidth(1)
    p.rect(5 * mm, 5 * mm, width - 10 * mm, height - 10 * mm)
    
    p.showPage()
    p.save()
    
    return response