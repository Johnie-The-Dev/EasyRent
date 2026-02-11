# views.py (UNIT-FIRST / NO FALLBACK) — UPDATED (SINGLE IMAGE + MODEL VIDEO) ✅

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DecimalField, Min, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views import View

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A6
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .forms import (
    LandlordProfileForm,
    PropertyForm,
    SignUpForm,
    TenantForm,
    TenantProfileForm,
    UnitFormSet,
)
from .models import Booking, Property, PropertyImage, ReceiptHistory, Tenant, TenantCollection, Unit

User = get_user_model()


# ---------------- Helpers ----------------

def _formset_has_any_unit(formset) -> bool:
    """
    Ensure at least one non-deleted unit_code exists.
    Works after formset.is_valid() is True.
    """
    for f in formset.forms:
        cd = getattr(f, "cleaned_data", None) or {}
        code = (cd.get("unit_code") or "").strip()
        deleting = bool(cd.get("DELETE", False))
        if code and not deleting:
            return True
    return False


# ---------------- Auth ----------------

def signup_view(request):
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("landlord_dashboard" if user.is_landlord() else "tenant_feed")
    else:
        form = SignUpForm()
    return render(request, "signup.html", {"form": form})


class CustomLoginView(LoginView):
    template_name = "login.html"


def custom_logout(request):
    logout(request)
    return redirect("login")


# ---------------- Dashboard ----------------

@login_required
def landlord_dashboard(request):
    properties_qs = Property.objects.filter(landlord=request.user)
    units_qs = Unit.objects.filter(property__landlord=request.user, is_active=True)

    total_units = units_qs.count()
    occupied_units = units_qs.filter(vacancy="occupied").count()
    vacant_units = units_qs.filter(vacancy="vacant").count()
    booked_units = units_qs.filter(vacancy="booked").count()
    maintenance_units = units_qs.filter(vacancy="maintenance").count()
    occupancy_rate = (occupied_units / total_units * 100) if total_units else 0

    total_monthly_rent = units_qs.aggregate(total=Sum("price", output_field=DecimalField()))["total"] or 0

    current_month = timezone.now().strftime("%Y-%m")
    rent_collected_month = (
        ReceiptHistory.objects.filter(
            tenant__unit_fk__property__landlord=request.user,
            month_paid_for=current_month,
        )
        .aggregate(total=Sum("rent_paid", output_field=DecimalField()))["total"]
        or 0
    )
    rent_remaining_month = (total_monthly_rent or 0) - (rent_collected_month or 0)

    outstanding_balance = (
        Tenant.objects.filter(unit_fk__property__landlord=request.user)
        .aggregate(total=Sum("rent_balance", output_field=DecimalField()))["total"]
        or 0
    )

    pending_bookings = (
        Booking.objects.filter(unit__property__landlord=request.user, status="pending")
        .select_related("tenant", "unit", "unit__property")
        .order_by("-created_at")[:8]
    )
    pending_count = pending_bookings.count()

    recent_tenants = (
        Tenant.objects.filter(unit_fk__property__landlord=request.user)
        .select_related("unit_fk", "unit_fk__property")
        .order_by("-date_paid")[:8]
    )

    recent_payments = (
        ReceiptHistory.objects.filter(tenant__unit_fk__property__landlord=request.user)
        .select_related("tenant", "tenant__unit_fk", "tenant__unit_fk__property")
        .order_by("-date_generated")[:8]
    )

    active_units_filter = Q(units__is_active=True)
    vacant_units_filter = Q(units__is_active=True, units__vacancy="vacant")
    occupied_units_filter = Q(units__is_active=True, units__vacancy="occupied")
    booked_units_filter = Q(units__is_active=True, units__vacancy="booked")

    properties = (
        properties_qs.annotate(
            total_units_count=Count("units", filter=active_units_filter, distinct=True),
            vacant_units_count=Count("units", filter=vacant_units_filter, distinct=True),
            occupied_units_count=Count("units", filter=occupied_units_filter, distinct=True),
            booked_units_count=Count("units", filter=booked_units_filter, distinct=True),
            expected_monthly_rent=Sum("units__price", filter=active_units_filter, output_field=DecimalField()),
        )
        .prefetch_related("images")
        .order_by("-created_at")
    )

    context = {
        "total_monthly_rent": total_monthly_rent,
        "rent_collected_month": rent_collected_month,
        "rent_remaining_month": rent_remaining_month,
        "outstanding_balance": outstanding_balance,
        "total_units": total_units,
        "occupied_units": occupied_units,
        "vacant_units": vacant_units,
        "booked_units": booked_units,
        "maintenance_units": maintenance_units,
        "occupancy_rate": round(occupancy_rate, 1),
        "pending_bookings": pending_bookings,
        "pending_count": pending_count,
        "recent_tenants": recent_tenants,
        "recent_payments": recent_payments,
        "properties": properties,
        "total_properties": properties.count(),
    }
    return render(request, "landlord_dashboard.html", context)


# ---------------- Tenant Feed ----------------

def tenant_feed(request):
    # Prefetch images + avoid N+1 for landlord access in templates
    properties_list = Property.objects.all().select_related("landlord").prefetch_related("images")

    county = request.GET.get("county", "")
    town = request.GET.get("town", "")
    property_type = request.GET.get("property_type", "")
    min_price = request.GET.get("min_price", "")
    max_price = request.GET.get("max_price", "")

    if county:
        properties_list = properties_list.filter(county__icontains=county)
    if town:
        properties_list = properties_list.filter(town__icontains=town)

    unit_filter = Q(units__is_active=True, units__vacancy="vacant")

    if property_type:
        unit_filter &= Q(units__unit_type=property_type)

    if min_price:
        try:
            unit_filter &= Q(units__price__gte=float(min_price))
        except (ValueError, TypeError):
            pass

    if max_price:
        try:
            unit_filter &= Q(units__price__lte=float(max_price))
        except (ValueError, TypeError):
            pass

    properties_list = (
        properties_list.filter(unit_filter)
        .distinct()
        .annotate(
            vacant_units_filtered=Count("units", filter=unit_filter, distinct=True),
            min_vacant_price=Min("units__price", filter=unit_filter),
            total_units_active=Count("units", filter=Q(units__is_active=True), distinct=True),
        )
    )

    paginator = Paginator(properties_list, 10)
    page_number = request.GET.get("page")
    properties = paginator.get_page(page_number)

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
        lower = text.lower()
        return [label for label, keys in AMENITY_KEYWORDS.items() if any(k in lower for k in keys)]

    # Avoid mutating model fields; add display-only attrs
    for prop in properties:
        prop.amenities = extract_amenities(getattr(prop, "description", ""))
        prop.vacant_units_display = getattr(prop, "vacant_units_filtered", 0) or 0

    context = {
        "properties": properties,
        "selected_county": county,
        "selected_town": town,
        "selected_type": property_type,
        "selected_min_price": min_price,
        "selected_max_price": max_price,
    }

    if request.user.is_authenticated:
        context["user_bookings"] = list(
            Booking.objects.filter(tenant=request.user, status__in=["pending", "confirmed"])
            .values_list("unit__property_id", flat=True)
            .distinct()
        )
        context["saved_properties"] = list(
            TenantCollection.objects.filter(tenant=request.user).values_list("property_id", flat=True)
        )
    else:
        context["user_bookings"] = []
        context["saved_properties"] = []

    return render(request, "tenant_feed.html", context)


# ---------------- Properties (ADD / EDIT) ----------------

@login_required
def add_property(request):
    if request.method == "POST":
        form = PropertyForm(request.POST, request.FILES)
        formset = UnitFormSet(request.POST, request.FILES)

        if form.is_valid() and formset.is_valid():
            if not _formset_has_any_unit(formset):
                messages.error(request, "Please add at least one unit (Unit Code is required).")
                return render(request, "add_property.html", {"form": form, "formset": formset})

            with transaction.atomic():
                prop = form.save(commit=False)
                prop.landlord = request.user
                prop.save()  # ✅ saves model field "video" too

                # units
                formset.instance = prop
                units = formset.save(commit=False)
                for u in units:
                    if not (u.unit_code or "").strip():
                        continue
                    if not u.unit_type:
                        u.unit_type = prop.property_type
                    if u.price in [None, ""]:
                        u.price = prop.price
                    if not u.vacancy:
                        u.vacancy = "vacant"
                    if u.is_active is None:
                        u.is_active = True
                    u.save()

                for obj in formset.deleted_objects:
                    obj.delete()

                # ✅ SINGLE image upload: property_image (non-model field)
                image_file = form.cleaned_data.get("property_image")
                if image_file:
                    PropertyImage.objects.create(property=prop, image=image_file, is_primary=True)

            messages.success(request, "Property created successfully.")
            return redirect("landlord_dashboard")

        messages.error(request, "Please fix the errors below.")
    else:
        form = PropertyForm()
        formset = UnitFormSet()

    return render(request, "add_property.html", {"form": form, "formset": formset})


@login_required
def edit_property(request, property_id):
    prop = get_object_or_404(Property, id=property_id, landlord=request.user)

    if request.method == "POST":
        form = PropertyForm(request.POST, request.FILES, instance=prop)
        unit_formset = UnitFormSet(request.POST, request.FILES, instance=prop)

        if form.is_valid() and unit_formset.is_valid():
            with transaction.atomic():
                prop = form.save()  # ✅ saves model field "video" if newly uploaded

                units = unit_formset.save(commit=False)
                for u in units:
                    if not (u.unit_code or "").strip():
                        continue
                    u.save()

                for obj in unit_formset.deleted_objects:
                    if obj.vacancy == "occupied":
                        messages.error(
                            request,
                            f"Cannot delete occupied unit {obj.unit_code}. Mark inactive instead."
                        )
                        continue
                    obj.delete()

                # ✅ SINGLE image upload: property_image
                image_file = form.cleaned_data.get("property_image")
                if image_file:
                    has_primary = PropertyImage.objects.filter(property=prop, is_primary=True).exists()
                    PropertyImage.objects.create(
                        property=prop,
                        image=image_file,
                        is_primary=(not has_primary),
                    )

            messages.success(request, "Property updated successfully.")
            return redirect("edit_property", property_id=prop.id)

        messages.error(request, "Please fix the errors below.")
    else:
        form = PropertyForm(instance=prop)
        unit_formset = UnitFormSet(instance=prop)

    return render(
        request,
        "edit_property.html",
        {"form": form, "unit_formset": unit_formset, "property": prop},
    )


@login_required
def delete_property(request, property_id):
    prop = get_object_or_404(Property, id=property_id, landlord=request.user)
    if request.method == "POST":
        prop.delete()
        messages.success(request, "Property deleted.")
        return redirect("landlord_dashboard")
    return render(request, "delete_property.html", {"property": prop})


@login_required
def delete_property_image(request, image_id):
    image = get_object_or_404(PropertyImage, id=image_id)
    prop = image.property
    if prop.landlord != request.user:
        return redirect("landlord_dashboard")

    image.delete()

    # ✅ If we deleted the primary, promote another image to primary (if any left)
    if not PropertyImage.objects.filter(property=prop, is_primary=True).exists():
        next_img = PropertyImage.objects.filter(property=prop).order_by("id").first()
        if next_img:
            next_img.is_primary = True
            next_img.save(update_fields=["is_primary"])

    messages.success(request, "Image deleted successfully.")
    return redirect("edit_property", property_id=prop.id)


# ---------------- Bookings ----------------

@login_required
def book_property(request, property_id):
    prop = get_object_or_404(Property, id=property_id)

    unit = Unit.objects.filter(property=prop, vacancy="vacant", is_active=True).order_by("id").first()
    if not unit:
        messages.error(request, "No vacant units available for this property.")
        return redirect("tenant_feed")

    if Booking.objects.filter(tenant=request.user, unit=unit, status__in=["pending", "confirmed"]).exists():
        messages.info(request, "You already have an active booking for this unit.")
        return redirect("tenant_feed")

    with transaction.atomic():
        Booking.objects.create(unit=unit, tenant=request.user, status="pending")
        Unit.objects.filter(id=unit.id).update(vacancy="booked")

    messages.success(
        request,
        f"Booking request sent! Landlord: {prop.landlord.get_full_name() or prop.landlord.username}",
    )
    return redirect("tenant_feed")


@login_required
def landlord_bookings(request):
    bookings = list(
        Booking.objects.filter(unit__property__landlord=request.user)
        .select_related("tenant", "unit", "unit__property")
        .order_by("-created_at")
    )

    context = {
        "bookings": bookings,
        "total_bookings": len(bookings),
        "confirmed_count": sum(1 for b in bookings if b.status == "confirmed"),
        "pending_count": sum(1 for b in bookings if b.status == "pending"),
        "cancelled_count": sum(1 for b in bookings if b.status == "cancelled"),
    }
    return render(request, "landlord_bookings.html", context)


@login_required
def tenant_bookings(request):
    bookings = (
        Booking.objects.filter(tenant=request.user)
        .select_related("unit", "unit__property", "unit__property__landlord")
        .order_by("-created_at")
    )

    context = {
        "bookings": bookings,
        "confirmed_count": bookings.filter(status="confirmed").count(),
        "pending_count": bookings.filter(status="pending").count(),
    }
    return render(request, "tenant_bookings.html", context)


@login_required
def unbook_property(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id, tenant=request.user)
    prop_name = booking.unit.property.name if booking.unit_id else "booking"

    if booking.unit_id and booking.status in ["pending", "confirmed"]:
        Unit.objects.filter(id=booking.unit_id).update(vacancy="vacant")

    booking.delete()
    messages.success(request, f"Successfully unbooked {prop_name}.")
    return redirect("tenant_bookings")


@login_required
def update_booking_status(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)

    if booking.unit.property.landlord != request.user:
        return redirect("landlord_bookings")

    if request.method == "POST":
        new_status = request.POST.get("status")
        if new_status in ["confirmed", "cancelled", "pending"]:
            with transaction.atomic():
                Booking.objects.filter(id=booking.id).update(status=new_status)
                vacancy = (
                    "occupied" if new_status == "confirmed"
                    else ("booked" if new_status == "pending" else "vacant")
                )
                Unit.objects.filter(id=booking.unit_id).update(vacancy=vacancy)

            messages.success(request, f"Booking status updated to {new_status}.")
    return redirect("landlord_bookings")


# ---------------- Collections ----------------

@login_required
def add_to_collection(request, property_id):
    prop = get_object_or_404(Property, id=property_id)
    _, created = TenantCollection.objects.get_or_create(tenant=request.user, property=prop)
    messages.success(
        request,
        "Property added to your collection!" if created else "Property is already in your collection.",
    )
    return redirect("tenant_feed")


@login_required
def remove_from_collection(request, property_id):
    prop = get_object_or_404(Property, id=property_id)
    TenantCollection.objects.filter(tenant=request.user, property=prop).delete()

    # Keep the UX consistent: if user removed from within collection page,
    # they should return there; otherwise tenant_feed is fine.
    next_url = request.GET.get("next")
    if next_url:
        return redirect(next_url)

    return redirect("tenant_feed")


@login_required
def view_collection(request):
    unit_filter = Q(property__units__is_active=True, property__units__vacancy="vacant")

    collection = (
        TenantCollection.objects.filter(tenant=request.user)
        .select_related("property", "property__landlord")
        .prefetch_related("property__images")
        .annotate(
            vacant_units_filtered=Count("property__units", filter=unit_filter, distinct=True),
            min_vacant_price=Min("property__units__price", filter=unit_filter),
        )
        .order_by("-added_at")
    )

    booked_property_ids = list(
        Booking.objects.filter(tenant=request.user, status__in=["pending", "confirmed"])
        .values_list("unit__property_id", flat=True)
        .distinct()
    )

    return render(
        request,
        "collection.html",
        {"collection": collection, "user_bookings": booked_property_ids},
    )


# ---------------- Password Reset ----------------

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


# ---------------- Profiles ----------------

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


# ---------------- Tenants ----------------

@login_required
def manage_tenants(request):
    if request.method == "POST":
        form = TenantForm(request.POST, user=request.user)
        if form.is_valid():
            tenant_obj = form.save(commit=False)

            if tenant_obj.unit_fk.property.landlord != request.user:
                messages.error(request, "You can only add tenants to your own units.")
                return redirect(reverse("manage_tenants"))

            tenant_obj.rent_balance = tenant_obj.monthly_rent - tenant_obj.rent_paid
            tenant_obj.cumulative_rent_balance += tenant_obj.rent_balance
            tenant_obj.save()

            Unit.objects.filter(id=tenant_obj.unit_fk_id).update(vacancy="occupied")

            messages.success(request, f"Tenant {tenant_obj.name} added successfully.")
            return redirect(reverse("tenant_list"))
    else:
        form = TenantForm(user=request.user)

    return render(request, "manage_tenants.html", {"form": form})


@login_required
def edit_tenant(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id)

    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to edit this tenant.")
        return redirect(reverse("tenant_list"))

    old_unit_id = tenant_obj.unit_fk_id

    if request.method == "POST":
        form = TenantForm(request.POST, instance=tenant_obj, user=request.user)
        if form.is_valid():
            updated = form.save(commit=False)

            if updated.unit_fk.property.landlord != request.user:
                messages.error(request, "You can only assign tenants to your own units.")
                return redirect(reverse("tenant_list"))

            updated.rent_balance = updated.monthly_rent - updated.rent_paid
            updated.cumulative_rent_balance += updated.rent_balance
            updated.save()

            if updated.unit_fk_id != old_unit_id:
                Unit.objects.filter(id=old_unit_id).update(vacancy="vacant")
                Unit.objects.filter(id=updated.unit_fk_id).update(vacancy="occupied")

            messages.success(request, f"Tenant {updated.name} updated successfully.")
            return redirect(reverse("tenant_list"))
    else:
        form = TenantForm(instance=tenant_obj, user=request.user)

    return render(request, "edit_tenant.html", {"form": form, "tenant": tenant_obj})


@login_required
def delete_tenant(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id)

    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to delete this tenant.")
        return redirect(reverse("tenant_list"))

    unit_id = tenant_obj.unit_fk_id
    tenant_name = tenant_obj.name
    tenant_obj.delete()

    Unit.objects.filter(id=unit_id).update(vacancy="vacant")

    messages.success(request, f"Tenant {tenant_name} deleted successfully.")
    return redirect(reverse("tenant_list"))


@login_required
def tenant_list(request):
    tenant_list_qs = (
        Tenant.objects.filter(unit_fk__property__landlord=request.user)
        .select_related("unit_fk", "unit_fk__property")
        .order_by("-date_paid")
    )
    return render(request, "tenant_list.html", {"tenant_list": tenant_list_qs})


# ---------------- Receipts ----------------

@login_required
def generate_receipt(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id)

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


# ---------------- Home ----------------

def home(request):
    return render(request, "home.html")
