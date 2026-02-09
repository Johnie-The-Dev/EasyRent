# views.py (UNIT-FIRST / NO FALLBACK)

from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import DecimalField, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.views import View

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A6
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .forms import LandlordProfileForm, PropertyForm, SignUpForm, TenantForm, TenantProfileForm
from .models import (
    Booking,
    Property,
    PropertyImage,
    ReceiptHistory,
    Tenant,
    TenantCollection,
    Unit,
)

User = get_user_model()


def signup_view(request):
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            if user.is_landlord():
                return redirect("landlord_dashboard")
            return redirect("tenant_feed")
    else:
        form = SignUpForm()
    return render(request, "signup.html", {"form": form})


@login_required
def landlord_dashboard(request):
    properties = Property.objects.filter(landlord=request.user)
    units = Unit.objects.filter(property__landlord=request.user, is_active=True)

    # Financial metrics
    total_monthly_rent = units.aggregate(total=Sum("price", output_field=DecimalField()))["total"] or 0

    # Current month rent collected (ReceiptHistory -> Tenant -> Unit -> Property -> Landlord)
    current_month = datetime.now().strftime("%Y-%m")
    rent_collected_month = (
        ReceiptHistory.objects.filter(
            tenant__unit_fk__property__landlord=request.user,
            month_paid_for=current_month,
        ).aggregate(total=Sum("rent_paid", output_field=DecimalField()))["total"]
        or 0
    )

    # Outstanding balance (Tenant records)
    outstanding_balance = (
        Tenant.objects.filter(unit_fk__property__landlord=request.user)
        .aggregate(total=Sum("rent_balance", output_field=DecimalField()))["total"]
        or 0
    )

    # Occupancy stats (units)
    total_units = units.count()
    occupied_units = units.filter(vacancy="occupied").count()
    vacant_units = units.filter(vacancy="vacant").count()
    occupancy_rate = (occupied_units / total_units * 100) if total_units > 0 else 0

    # Pending bookings (unit-based)
    pending_bookings = (
        Booking.objects.filter(unit__property__landlord=request.user, status="pending")
        .select_related("unit", "unit__property", "tenant")
        .order_by("-created_at")[:5]
    )
    pending_count = pending_bookings.count()

    # Recent rent payments
    recent_payments = (
        ReceiptHistory.objects.filter(tenant__unit_fk__property__landlord=request.user)
        .select_related("tenant")
        .order_by("-date_paid")[:8]
    )

    # Property performance summary
    property_performance = []
    for prop in properties:
        prop_units = units.filter(property=prop)
        prop_total_units = prop_units.count()
        prop_occupied = prop_units.filter(vacancy="occupied").count()
        prop_vacant = prop_units.filter(vacancy="vacant").count()

        prop_tenants = Tenant.objects.filter(unit_fk__property=prop)
        prop_balance = prop_tenants.aggregate(total=Sum("rent_balance", output_field=DecimalField()))["total"] or 0

        property_performance.append(
            {
                "property": prop,
                "total_units": prop_total_units,
                "occupied_units": prop_occupied,
                "vacant_units": prop_vacant,
                "tenant_count": prop_tenants.count(),
                "outstanding_balance": prop_balance,
            }
        )

    context = {
        "properties": properties,
        "total_monthly_rent": total_monthly_rent,
        "rent_collected_month": rent_collected_month,
        "outstanding_balance": outstanding_balance,
        "total_units": total_units,
        "occupied_units": occupied_units,
        "vacant_units": vacant_units,
        "occupancy_rate": round(occupancy_rate, 1),
        "pending_bookings": pending_bookings,
        "pending_count": pending_count,
        "recent_payments": recent_payments,
        "property_performance": property_performance,
    }
    return render(request, "landlord_dashboard.html", context)


def tenant_feed(request):
    properties_list = Property.objects.all()

    # Filters
    county = request.GET.get("county", "")
    town = request.GET.get("town", "")
    property_type = request.GET.get("property_type", "")
    min_price = request.GET.get("min_price", "")
    max_price = request.GET.get("max_price", "")

    if county:
        properties_list = properties_list.filter(county__icontains=county)
    if town:
        properties_list = properties_list.filter(town__icontains=town)

    # Apply filters to UNIT attributes
    if property_type:
        properties_list = properties_list.filter(units__unit_type=property_type)

    if min_price:
        try:
            min_price_val = float(min_price)
            properties_list = properties_list.filter(units__price__gte=min_price_val)
        except (ValueError, TypeError):
            pass

    if max_price:
        try:
            max_price_val = float(max_price)
            properties_list = properties_list.filter(units__price__lte=max_price_val)
        except (ValueError, TypeError):
            pass

    # Only show properties that have at least 1 vacant unit
    properties_list = properties_list.filter(units__vacancy="vacant", units__is_active=True).distinct()

    # Pagination
    paginator = Paginator(properties_list, 10)
    page_number = request.GET.get("page")
    properties = paginator.get_page(page_number)

    # Lightweight amenity extraction from free text
    AMENITY_KEYWORDS = {
        "WiFi": ["wifi", "wi-fi", "internet"],
        "Water (included)": ["water included", "water"],
        "Electricity (included)": ["electricity included", "electricity", "power"],
        "Furnished": ["furnished", "furniture"],
        "Parking": ["parking", "garage"],
        "Balcony": ["balcony"],
        "Hot Water": ["hot water", "geyser", "water heater"],
        "Security": ["security", "guard", "gated"],
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

    for prop in properties:
        prop.amenities = extract_amenities(prop.description)
        prop.total_units = prop.units.filter(is_active=True).count()
        prop.vacant_units = prop.units.filter(is_active=True, vacancy="vacant").count()

    context = {
        "properties": properties,
        "selected_county": county,
        "selected_town": town,
        "selected_type": property_type,
        "selected_min_price": min_price,
        "selected_max_price": max_price,
    }

    if request.user.is_authenticated:
        # Bookings are unit-based now. For property cards, compute property IDs with active bookings.
        booked_property_ids = list(
            Booking.objects.filter(
                tenant=request.user,
                status__in=["pending", "confirmed"],
            ).values_list("unit__property_id", flat=True).distinct()
        )
        saved_property_ids = list(TenantCollection.objects.filter(tenant=request.user).values_list("property_id", flat=True))
        context["user_bookings"] = booked_property_ids
        context["saved_properties"] = saved_property_ids
    else:
        context["saved_properties"] = []

    return render(request, "tenant_feed.html", context)


@login_required
def add_property(request):
    if request.method == "POST":
        form = PropertyForm(request.POST, request.FILES)
        if form.is_valid():
            prop = form.save(commit=False)
            prop.landlord = request.user
            prop.save()

            # Create a default unit so inventory exists
            Unit.objects.create(
                property=prop,
                unit_code="DEFAULT",
                unit_type=prop.property_type,
                price=prop.price,
                vacancy="vacant",
            )

            # Handle multiple image uploads
            images = request.FILES.getlist("property_images")
            for idx, image_file in enumerate(images):
                PropertyImage.objects.create(property=prop, image=image_file, is_primary=(idx == 0))

            return redirect("landlord_dashboard")
    else:
        form = PropertyForm()
    return render(request, "add_property.html", {"form": form})


@login_required
def edit_property(request, property_id):
    prop = get_object_or_404(Property, id=property_id, landlord=request.user)
    if request.method == "POST":
        form = PropertyForm(request.POST, request.FILES, instance=prop)
        if form.is_valid():
            form.save()

            images = request.FILES.getlist("property_images")
            for image_file in images:
                PropertyImage.objects.create(property=prop, image=image_file, is_primary=False)

            return redirect("landlord_dashboard")
    else:
        form = PropertyForm(instance=prop)
    return render(request, "edit_property.html", {"form": form})


@login_required
def delete_property(request, property_id):
    prop = get_object_or_404(Property, id=property_id, landlord=request.user)
    if request.method == "POST":
        prop.delete()
        return redirect("landlord_dashboard")
    return render(request, "delete_property.html", {"property": prop})


@login_required
def delete_property_image(request, image_id):
    image = get_object_or_404(PropertyImage, id=image_id)
    property_id = image.property.id
    if image.property.landlord != request.user:
        return redirect("landlord_dashboard")
    image.delete()
    messages.success(request, "Image deleted successfully.")
    return redirect("edit_property", property_id=property_id)


@login_required
def book_property(request, property_id):
    """
    Book a property by auto-assigning the first vacant active unit.
    Booking is unit-based (no Booking.property).
    """
    prop = get_object_or_404(Property, id=property_id)

    unit = Unit.objects.filter(property=prop, vacancy="vacant", is_active=True).order_by("id").first()
    if not unit:
        messages.error(request, "No vacant units available for this property.")
        return redirect("tenant_feed")

    # Avoid duplicates for same tenant+unit in active statuses
    if Booking.objects.filter(tenant=request.user, unit=unit, status__in=["pending", "confirmed"]).exists():
        messages.info(request, "You already have an active booking for this unit.")
        return redirect("tenant_feed")

    with transaction.atomic():
        Booking.objects.create(unit=unit, tenant=request.user, status="pending")
        unit.vacancy = "booked"
        unit.save(update_fields=["vacancy"])

    messages.success(
        request,
        f"Booking request sent! Landlord: {prop.landlord.get_full_name() or prop.landlord.username}",
    )
    return redirect("tenant_feed")


@login_required
def landlord_bookings(request):
    bookings = (
        Booking.objects.filter(unit__property__landlord=request.user)
        .select_related("tenant", "unit", "unit__property")
        .order_by("-created_at")
    )
    return render(request, "landlord_bookings.html", {"bookings": bookings})


@login_required
def tenant_bookings(request):
    bookings = (
        Booking.objects.filter(tenant=request.user)
        .select_related("unit", "unit__property", "unit__property__landlord")
        .order_by("-created_at")
    )
    return render(request, "tenant_bookings.html", {"bookings": bookings})


@login_required
def unbook_property(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id, tenant=request.user)
    property_name = booking.unit.property.name

    if booking.status in ["pending", "confirmed"]:
        booking.unit.vacancy = "vacant"
        booking.unit.save(update_fields=["vacancy"])

    booking.delete()
    messages.success(request, f"Successfully unbooked {property_name}.")
    return redirect("tenant_bookings")


@login_required
def update_booking_status(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)

    # Ownership check
    if booking.unit.property.landlord != request.user:
        return redirect("landlord_bookings")

    if request.method == "POST":
        new_status = request.POST.get("status")
        if new_status in ["confirmed", "cancelled", "pending"]:
            booking.status = new_status
            booking.save(update_fields=["status"])

            # Sync unit vacancy
            if new_status == "confirmed":
                booking.unit.vacancy = "occupied"
            elif new_status == "pending":
                booking.unit.vacancy = "booked"
            elif new_status == "cancelled":
                booking.unit.vacancy = "vacant"
            booking.unit.save(update_fields=["vacancy"])

            messages.success(request, f"Booking status updated to {new_status}.")
    return redirect("landlord_bookings")


def home(request):
    return render(request, "home.html")


@login_required
def add_to_collection(request, property_id):
    prop = get_object_or_404(Property, id=property_id)
    collection, created = TenantCollection.objects.get_or_create(tenant=request.user, property=prop)
    messages.success(request, "Property added to your collection!" if created else "Property is already in your collection.")
    return redirect("tenant_feed")


@login_required
def remove_from_collection(request, property_id):
    prop = get_object_or_404(Property, id=property_id)
    TenantCollection.objects.filter(tenant=request.user, property=prop).delete()
    return redirect("tenant_feed")


@login_required
def view_collection(request):
    collection = TenantCollection.objects.filter(tenant=request.user)
    booked_property_ids = list(
        Booking.objects.filter(tenant=request.user, status__in=["pending", "confirmed"])
        .values_list("unit__property_id", flat=True)
        .distinct()
    )
    return render(request, "collection.html", {"collection": collection, "user_bookings": booked_property_ids})


class CustomLoginView(LoginView):
    template_name = "login.html"


def custom_logout(request):
    logout(request)
    return redirect("login")


# Store codes temporarily (use cache or database in production)
reset_codes = {}


class RequestPasswordResetCodeView(View):
    def get(self, request):
        return render(request, "password_reset_request.html")

    def post(self, request):
        email = request.POST.get("email")
        user = User.objects.filter(email=email).first()

        if user:
            code = get_random_string(6, allowed_chars="0123456789")
            reset_codes[email] = code
            send_mail(
                "Your Password Reset Code",
                f"Your password reset code is: {code}",
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False,
            )
            messages.success(request, "A reset code has been sent to your email.")
            return redirect("password_reset_verify")

        messages.error(request, "No account found with this email.")
        return redirect("password_reset")


class VerifyResetCodeView(View):
    def get(self, request):
        return render(request, "password_reset_verify.html")

    def post(self, request):
        email = request.POST.get("email")
        code = request.POST.get("code")

        if reset_codes.get(email) == code:
            request.session["reset_email"] = email
            return redirect("password_reset_new_password")

        messages.error(request, "Invalid code. Please try again.")
        return redirect("password_reset_verify")


class SetNewPasswordView(View):
    def get(self, request):
        return render(request, "password_reset_new_password.html")

    def post(self, request):
        email = request.session.get("reset_email")
        password = request.POST.get("password")

        if email:
            user = User.objects.filter(email=email).first()
            if user:
                user.set_password(password)
                user.save()
                reset_codes.pop(email, None)
                messages.success(request, "Password reset successfully. You can now log in.")
                return redirect("login")

        messages.error(request, "Something went wrong. Please try again.")
        return redirect("password_reset")


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
    if request.method == "POST":
        form = TenantProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated successfully!")
            return redirect("tenant_profile")
    else:
        form = TenantProfileForm(instance=request.user)

    return render(request, "tenant_profile.html", {"form": form})


@login_required
def manage_tenants(request):
    """
    Requires TenantForm to expose unit_fk (Unit) instead of property/unit text.
    IMPORTANT: update your TenantForm accordingly.
    """
    landlord_units = Unit.objects.filter(property__landlord=request.user, is_active=True).select_related("property")

    if request.method == "POST":
        form = TenantForm(request.POST)
        if form.is_valid():
            tenant_obj = form.save(commit=False)

            # Permission check via unit_fk -> property -> landlord
            if tenant_obj.unit_fk.property.landlord != request.user:
                messages.error(request, "You can only add tenants to your own units.")
                return redirect(reverse("manage_tenants"))

            tenant_obj.rent_balance = tenant_obj.monthly_rent - tenant_obj.rent_paid
            tenant_obj.cumulative_rent_balance += tenant_obj.rent_balance
            tenant_obj.save()

            # Mark unit occupied
            tenant_obj.unit_fk.vacancy = "occupied"
            tenant_obj.unit_fk.save(update_fields=["vacancy"])

            messages.success(request, f"Tenant {tenant_obj.name} added successfully.")
            return redirect(reverse("tenant_list"))
    else:
        form = TenantForm()
        # Your TenantForm must have a unit_fk field for this to work:
        if "unit_fk" in form.fields:
            form.fields["unit_fk"].queryset = landlord_units

    return render(request, "manage_tenants.html", {"form": form})


@login_required
def edit_tenant(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id)

    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to edit this tenant.")
        return redirect(reverse("tenant_list"))

    landlord_units = Unit.objects.filter(property__landlord=request.user, is_active=True).select_related("property")

    if request.method == "POST":
        form = TenantForm(request.POST, instance=tenant_obj)
        if form.is_valid():
            updated = form.save(commit=False)

            if updated.unit_fk.property.landlord != request.user:
                messages.error(request, "You can only assign tenants to your own units.")
                return redirect(reverse("tenant_list"))

            updated.rent_balance = updated.monthly_rent - updated.rent_paid
            updated.cumulative_rent_balance += updated.rent_balance
            updated.save()

            messages.success(request, f"Tenant {updated.name} updated successfully.")
            return redirect(reverse("tenant_list"))
    else:
        form = TenantForm(instance=tenant_obj)
        if "unit_fk" in form.fields:
            form.fields["unit_fk"].queryset = landlord_units

    return render(request, "edit_tenant.html", {"form": form, "tenant": tenant_obj})


@login_required
def delete_tenant(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id)

    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to delete this tenant.")
        return redirect(reverse("tenant_list"))

    # Free the unit (optional rule)
    unit = tenant_obj.unit_fk
    tenant_name = tenant_obj.name
    tenant_obj.delete()

    unit.vacancy = "vacant"
    unit.save(update_fields=["vacancy"])

    messages.success(request, f"Tenant {tenant_name} deleted successfully.")
    return redirect(reverse("tenant_list"))


@login_required
def tenant_list(request):
    tenant_list_qs = Tenant.objects.filter(unit_fk__property__landlord=request.user).select_related("unit_fk", "unit_fk__property")
    return render(request, "tenant_list.html", {"tenant_list": tenant_list_qs})


@login_required
def generate_receipt(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id)

    # Permission check
    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to generate a receipt for this tenant.")
        return redirect(reverse("tenant_list"))

    ReceiptHistory.objects.create(
        tenant=tenant_obj,
        rent_paid=tenant_obj.rent_paid,
        date_paid=tenant_obj.date_paid,
        month_paid_for=tenant_obj.month_paid_for,
        monthly_rent=tenant_obj.monthly_rent,
        rent_balance=tenant_obj.rent_balance,
        cumulative_rent_balance=tenant_obj.cumulative_rent_balance,
    )

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="receipt_{tenant_obj.name}.pdf"'

    p = canvas.Canvas(response, pagesize=A6)
    width, height = A6

    p.setFillColor(HexColor("#f5f5f5"))
    p.rect(0, 0, width, height, fill=1)

    p.setFillColor(HexColor("#6a11cb"))
    p.rect(0, height - 20 * mm, width, 20 * mm, fill=1)
    p.setFont("Helvetica-Bold", 14)
    p.setFillColor(HexColor("#ffffff"))
    p.drawString(10 * mm, height - 10 * mm, "EasyRent Receipt")

    p.setFont("Helvetica", 10)
    p.setFillColor(HexColor("#000000"))
    y = height - 30 * mm
    line_height = 5 * mm

    unit = tenant_obj.unit_fk
    prop = unit.property

    p.drawString(10 * mm, y, f"Tenant: {tenant_obj.name}"); y -= line_height
    p.drawString(10 * mm, y, f"Phone: {tenant_obj.phone}"); y -= line_height
    p.drawString(10 * mm, y, f"Property: {prop.name}"); y -= line_height
    p.drawString(10 * mm, y, f"Unit: {unit.unit_code}"); y -= line_height

    p.drawString(10 * mm, y, f"Date Occupied: {tenant_obj.date_occupied}"); y -= line_height
    p.drawString(10 * mm, y, f"Rent Paid: {tenant_obj.rent_paid}"); y -= line_height
    p.drawString(10 * mm, y, f"Date Paid: {tenant_obj.date_paid}"); y -= line_height
    p.drawString(10 * mm, y, f"Month Paid For: {tenant_obj.month_paid_for}"); y -= line_height
    p.drawString(10 * mm, y, f"Monthly Rent: {tenant_obj.monthly_rent}"); y -= line_height
    p.drawString(10 * mm, y, f"Rent Balance: {tenant_obj.rent_balance}"); y -= line_height
    p.drawString(10 * mm, y, f"Cumulative Rent Balance: {tenant_obj.cumulative_rent_balance}")

    p.setFont("Helvetica-Bold", 10)
    p.setFillColor(HexColor("#6a11cb"))
    p.drawString(10 * mm, y - 2 * line_height, "Thank you for your payment!")

    p.setStrokeColor(HexColor("#6a11cb"))
    p.setLineWidth(1)
    p.rect(5 * mm, 5 * mm, width - 10 * mm, height - 10 * mm)

    p.showPage()
    p.save()
    return response


@login_required
def receipt_history(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id)

    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to view this receipt history.")
        return redirect(reverse("tenant_list"))

    history = ReceiptHistory.objects.filter(tenant=tenant_obj).order_by("-date_generated")
    return render(request, "receipt_history.html", {"tenant": tenant_obj, "history": history})
