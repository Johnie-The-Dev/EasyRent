from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.contrib import messages
from django.views import View
from django.utils.crypto import get_random_string
from django.conf import settings
from django.core.paginator import Paginator
from django.urls import reverse
from django.http import HttpResponse
from django.db.models import Sum, DecimalField, Q
from django.utils import timezone
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import A6
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor

from .forms import SignUpForm, PropertyForm, LandlordProfileForm, TenantProfileForm, TenantForm
from .models import Property, TenantCollection, Tenant, ReceiptHistory, PropertyImage, Booking

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
    
    # Financial metrics
    total_monthly_rent = properties.aggregate(
        total=Sum('price', output_field=DecimalField())
    )['total'] or 0
    
    # Current month rent collected - using Property FK relationship
    current_month = datetime.now().strftime("%Y-%m")
    rent_collected_month = ReceiptHistory.objects.filter(
        tenant__property__landlord=request.user,
        month_paid_for=current_month
    ).aggregate(total=Sum('rent_paid', output_field=DecimalField()))['total'] or 0
    
    # Total outstanding rent balance - using Property FK relationship
    outstanding_balance = Tenant.objects.filter(
        property__landlord=request.user
    ).aggregate(total=Sum('rent_balance', output_field=DecimalField()))['total'] or 0
    
    # Occupancy stats
    total_properties = properties.count()
    occupied_properties = properties.filter(vacancy='occupied').count()
    vacant_properties = properties.filter(vacancy='vacant').count()
    occupancy_rate = (occupied_properties / total_properties * 100) if total_properties > 0 else 0
    
    # Pending bookings
    pending_bookings = Booking.objects.filter(
        property__landlord=request.user,
        status='pending'
    ).select_related('property', 'tenant').order_by('-created_at')[:5]
    pending_count = pending_bookings.count()
    
    # Recent rent payments - using Property FK relationship
    recent_payments = ReceiptHistory.objects.filter(
        tenant__property__landlord=request.user
    ).select_related('tenant').order_by('-date_paid')[:8]
    
    # Property performance data - using Property FK relationship
    property_performance = []
    for prop in properties:
        current_tenants = Tenant.objects.filter(property=prop)
        total_balance = current_tenants.aggregate(
            total=Sum('rent_balance', output_field=DecimalField())
        )['total'] or 0
        
        property_performance.append({
            'property': prop,
            'occupancy_status': prop.vacancy or 'unknown',
            'tenant_count': current_tenants.count(),
            'outstanding_balance': total_balance,
        })
    
    context = {
        'properties': properties,
        'total_monthly_rent': total_monthly_rent,
        'rent_collected_month': rent_collected_month,
        'outstanding_balance': outstanding_balance,
        'total_properties': total_properties,
        'occupied_properties': occupied_properties,
        'vacant_properties': vacant_properties,
        'occupancy_rate': round(occupancy_rate, 1),
        'pending_bookings': pending_bookings,
        'pending_count': pending_count,
        'recent_payments': recent_payments,
        'property_performance': property_performance,
    }
    
    return render(request, 'landlord_dashboard.html', context)

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
        try:
            min_price_val = float(min_price)
            properties_list = properties_list.filter(price__gte=min_price_val)
        except (ValueError, TypeError):
            pass
    if max_price:
        try:
            max_price_val = float(max_price)
            properties_list = properties_list.filter(price__lte=max_price_val)
        except (ValueError, TypeError):
            pass

    # Pagination setup (10 properties per page)
    paginator = Paginator(properties_list, 10)
    page_number = request.GET.get("page")
    properties = paginator.get_page(page_number)

    # Lightweight amenity extraction from free-text description (MVP)
    AMENITY_KEYWORDS = {
        'WiFi': ['wifi', 'wi-fi', 'internet'],
        'Water (included)': ['water included', 'water'],
        'Electricity (included)': ['electricity included', 'electricity', 'power'],
        'Furnished': ['furnished', 'furniture'],
        'Parking': ['parking', 'garage'],
        'Balcony': ['balcony'],
        'Hot Water': ['hot water', 'geyser', 'water heater'],
        'Security': ['security', 'guard', 'gated'],
    }

    def extract_amenities(text):
        if not text:
            return []
        found = []
        lower = text.lower()
        for label, keys in AMENITY_KEYWORDS.items():
            for k in keys:
                if k in lower:
                    found.append(label)
                    break
        return found

    # Attach amenities list to each property on the current page
    for prop in properties:
        try:
            prop.amenities = extract_amenities(prop.description)
        except Exception:
            prop.amenities = []

    context = {
        "properties": properties,
        "selected_county": county,
        "selected_town": town,
        "selected_type": property_type,
        "selected_min_price": min_price,
        "selected_max_price": max_price,
    }
    
    # Add user's bookings and saved properties if authenticated
    if request.user.is_authenticated:
        user_bookings = Booking.objects.filter(tenant=request.user, status__in=['pending', 'confirmed']).values_list('property_id', flat=True)
        saved_properties = TenantCollection.objects.filter(tenant=request.user).values_list('property_id', flat=True)
        context['user_bookings'] = list(user_bookings)
        context['saved_properties'] = list(saved_properties)
    else:
        context['saved_properties'] = []

    return render(request, "tenant_feed.html", context)

@login_required
def add_property(request):
    if request.method == "POST":
        form = PropertyForm(request.POST, request.FILES)
        if form.is_valid():
            property = form.save(commit=False)
            property.landlord = request.user
            property.save()
            
            # Handle multiple image uploads
            images = request.FILES.getlist('property_images')
            for idx, image_file in enumerate(images):
                PropertyImage.objects.create(
                    property=property,
                    image=image_file,
                    is_primary=(idx == 0)  # First image is primary
                )
            
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
            
            # Handle multiple image uploads (add to existing images)
            images = request.FILES.getlist('property_images')
            for image_file in images:
                PropertyImage.objects.create(
                    property=property,
                    image=image_file,
                    is_primary=False
                )
            
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

@login_required
def delete_property_image(request, image_id):
    """Delete a single property image."""
    image = get_object_or_404(PropertyImage, id=image_id)
    property_id = image.property.id
    # Verify landlord owns the property
    if image.property.landlord != request.user:
        return redirect('landlord_dashboard')
    image.delete()
    messages.success(request, "Image deleted successfully.")
    return redirect('edit_property', property_id=property_id)

@login_required
def book_property(request, property_id):
    """Allow a tenant to book a property."""
    property = get_object_or_404(Property, id=property_id)
    
    # Create booking
    booking = Booking.objects.create(
        property=property,
        tenant=request.user,
        status='pending'
    )
    
    messages.success(request, f"Booking request sent! Landlord: {property.landlord.get_full_name() or property.landlord.username}")
    return redirect('tenant_feed')

@login_required
def landlord_bookings(request):
    """Display all bookings for a landlord's properties."""
    properties = Property.objects.filter(landlord=request.user)
    bookings = Booking.objects.filter(property__in=properties).select_related('tenant', 'property')
    return render(request, 'landlord_bookings.html', {'bookings': bookings})

@login_required
def tenant_bookings(request):
    """Display all bookings made by a tenant."""
    bookings = Booking.objects.filter(tenant=request.user).select_related('property', 'property__landlord')
    return render(request, 'tenant_bookings.html', {'bookings': bookings})

@login_required
def unbook_property(request, booking_id):
    """Allow a tenant to cancel/unbook a property."""
    booking = get_object_or_404(Booking, id=booking_id, tenant=request.user)
    property_name = booking.property.name
    booking.delete()
    messages.success(request, f"Successfully unbooked {property_name}.")
    return redirect('tenant_bookings')

@login_required
def update_booking_status(request, booking_id):
    """Update booking status (confirm/cancel)."""
    booking = get_object_or_404(Booking, id=booking_id)
    
    # Verify landlord owns the property being booked
    if booking.property.landlord != request.user:
        return redirect('landlord_bookings')
    
    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in ['confirmed', 'cancelled', 'pending']:
            booking.status = new_status
            booking.save()
            messages.success(request, f"Booking status updated to {new_status}.")
    
    return redirect('landlord_bookings')

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
@login_required
def view_collection(request):
    collection = TenantCollection.objects.filter(tenant=request.user)
    # Get user's bookings
    user_bookings = Booking.objects.filter(tenant=request.user, status__in=['pending', 'confirmed']).values_list('property_id', flat=True)
    return render(request, 'collection.html', {
        'collection': collection,
        'user_bookings': list(user_bookings)
    })

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

@login_required
def manage_tenants(request):
    landlord_properties = Property.objects.filter(landlord=request.user)
    
    if request.method == 'POST':
        form = TenantForm(request.POST)
        if form.is_valid():
            tenant = form.save(commit=False)
            # Verify property belongs to current landlord
            if tenant.property.landlord != request.user:
                messages.error(request, "You can only add tenants to your own properties.")
                return redirect(reverse('manage_tenants'))
            tenant.rent_balance = tenant.monthly_rent - tenant.rent_paid
            tenant.cumulative_rent_balance += tenant.rent_balance
            tenant.save()
            messages.success(request, f"Tenant {tenant.name} added successfully.")
            return redirect(reverse('tenant_list'))
    else:
        form = TenantForm()
        # Filter property field to show only current landlord's properties
        form.fields['property'].queryset = landlord_properties
    return render(request, 'manage_tenants.html', {'form': form})

@login_required
def edit_tenant(request, tenant_id):
    tenant = get_object_or_404(Tenant, id=tenant_id)
    landlord_properties = Property.objects.filter(landlord=request.user)
    
    # Verify tenant belongs to current landlord's property
    if tenant.property and tenant.property.landlord != request.user:
        messages.error(request, "You don't have permission to edit this tenant.")
        return redirect(reverse('tenant_list'))
    
    if request.method == 'POST':
        form = TenantForm(request.POST, instance=tenant)
        if form.is_valid():
            tenant = form.save(commit=False)
            # Verify property still belongs to landlord
            if tenant.property.landlord != request.user:
                messages.error(request, "You can only edit tenants in your own properties.")
                return redirect(reverse('tenant_list'))
            tenant.rent_balance = tenant.monthly_rent - tenant.rent_paid
            tenant.cumulative_rent_balance += tenant.rent_balance
            tenant.save()
            messages.success(request, f"Tenant {tenant.name} updated successfully.")
            return redirect(reverse('tenant_list'))
    else:
        form = TenantForm(instance=tenant)
        # Filter property field to show only current landlord's properties
        form.fields['property'].queryset = landlord_properties
    return render(request, 'edit_tenant.html', {'form': form, 'tenant': tenant})

@login_required
def delete_tenant(request, tenant_id):
    tenant = get_object_or_404(Tenant, id=tenant_id)
    # Verify tenant belongs to current landlord's property
    if tenant.property and tenant.property.landlord != request.user:
        messages.error(request, "You don't have permission to delete this tenant.")
        return redirect(reverse('tenant_list'))
    
    tenant_name = tenant.name
    tenant.delete()
    messages.success(request, f"Tenant {tenant_name} deleted successfully.")
    return redirect(reverse('tenant_list'))

@login_required
def tenant_list(request):
    # Only show tenants for current landlord's properties
    landlord_properties = Property.objects.filter(landlord=request.user)
    tenant_list = Tenant.objects.filter(property__in=landlord_properties)
    return render(request, 'tenant_list.html', {'tenant_list': tenant_list})

@login_required
def generate_receipt(request, tenant_id):
    """
    Generate a PDF receipt for a tenant and save the receipt history.
    This view function generates a PDF receipt for a tenant based on their payment details
    and saves the receipt history in the database. The generated PDF is returned as an HTTP response
    with the appropriate content type and disposition for download.
    Args:
        request (HttpRequest): The HTTP request object.
        tenant_id (int): The ID of the tenant for whom the receipt is being generated.
    Returns:
        HttpResponse: An HTTP response with the generated PDF receipt as an attachment.
    Raises:
        Http404: If the tenant with the given ID does not exist.
    Decorators:
        login_required: Ensures that the user is authenticated before accessing this view.
    """
    tenant = get_object_or_404(Tenant, id=tenant_id)
    
    # Save receipt history
    ReceiptHistory.objects.create(
        tenant=tenant,
        rent_paid=tenant.rent_paid,
        date_paid=tenant.date_paid,
        month_paid_for=tenant.month_paid_for,
        monthly_rent=tenant.monthly_rent,
        rent_balance=tenant.rent_balance,
        cumulative_rent_balance=tenant.cumulative_rent_balance
    )
    
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
    p.drawString(10 * mm, y, f"Property: {tenant.property.name if tenant.property else 'N/A'}")
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

@login_required
def receipt_history(request, tenant_id):
    tenant = get_object_or_404(Tenant, id=tenant_id)
    history = ReceiptHistory.objects.filter(tenant=tenant).order_by('-date_generated')
    return render(request, 'receipt_history.html', {'tenant': tenant, 'history': history})
