# listings/views.py (UNIT-FIRST / NO FALLBACK) — UPDATED ✅
from decimal import Decimal, InvalidOperation

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
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.views import View
from django.views.decorators.http import require_POST

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

from .models import (
    Booking,
    DepositHistory,
    Notification,
    Property,
    PropertyImage,
    ReceiptHistory,
    Tenant,
    TenantCollection,
    Unit,
)

User = get_user_model()

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

ACTIVE_BOOKING_STATUSES = (Booking.STATUS_PENDING, Booking.STATUS_CONFIRMED)


def _notify(recipient, message: str, *, level: str = Notification.LEVEL_INFO, url: str = "", actor=None) -> None:
    """Create a navbar notification for the recipient."""
    if not recipient:
        return

    allowed_levels = {k for k, _ in Notification.LEVEL_CHOICES}
    safe_level = level if level in allowed_levels else Notification.LEVEL_INFO

    Notification.objects.create(
        recipient=recipient,
        actor=actor if actor and getattr(actor, "is_authenticated", False) else None,
        message=(message or "").strip()[:255],
        level=safe_level,
        url=(url or "").strip()[:500],
    )


def _formset_has_any_unit(formset) -> bool:
    """At least one non-deleted unit_code exists (after formset.is_valid())."""
    for f in formset.forms:
        cd = getattr(f, "cleaned_data", None) or {}
        code = (cd.get("unit_code") or "").strip()
        deleting = bool(cd.get("DELETE", False))
        if code and not deleting:
            return True
    return False


def _cancel_active_bookings_for_unit(unit: Unit) -> int:
    """Cancel pending/confirmed bookings for a unit. Returns number cancelled."""
    return Booking.objects.filter(unit=unit, status__in=ACTIVE_BOOKING_STATUSES).update(status=Booking.STATUS_CANCELLED)


def _unit_has_active_bookings(unit: Unit) -> bool:
    return Booking.objects.filter(unit=unit, status__in=ACTIVE_BOOKING_STATUSES).exists()


def _unit_has_tenant(unit: Unit) -> bool:
    return Tenant.objects.filter(unit_fk=unit).exists()


def _set_unit_vacancy(unit: Unit, vacancy: str) -> None:
    Unit.objects.filter(id=unit.id).update(vacancy=vacancy)


def _normalize_decimal(val, default="0") -> Decimal:
    try:
        return Decimal(str(val))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _money(val) -> str:
    v = _normalize_decimal(val, default="0")
    return f"{v:,.0f}"


def _safe_str(s, fallback="—") -> str:
    s = (str(s).strip() if s is not None else "")
    return s if s else fallback


def _safe_payment_amount(request) -> Decimal:
    """Reads optional payment increment from POST as 'payment_amount'."""
    amt = _normalize_decimal(request.POST.get("payment_amount"), default="0")
    return amt if amt > 0 else Decimal("0")


def _safe_deposit_amount(request) -> Decimal:
    """Reads optional deposit amount from POST as 'deposit_amount' (not rent)."""
    amt = _normalize_decimal(request.POST.get("deposit_amount"), default="0")
    return amt if amt > 0 else Decimal("0")


def _parse_iso_date(date_str):
    """Parse yyyy-mm-dd safely. Returns date or None."""
    if not date_str:
        return None
    try:
        return timezone.datetime.fromisoformat(date_str).date()
    except ValueError:
        return None


def _get_tenant_user_fk(tenant_obj):
    """
    If your Tenant model has a FK to auth user, return it.
    Otherwise None. (DepositHistory.tenant is optional)
    """
    for attr in ("user", "user_fk", "tenant_user"):
        if hasattr(tenant_obj, f"{attr}_id"):
            return getattr(tenant_obj, attr, None)
    return None


def _next_or_referer(request, fallback_name="home"):
    return request.POST.get("next") or request.GET.get("next") or request.META.get("HTTP_REFERER") or fallback_name


# ---------------------------------------------------------------------
# Notifications (URLs in your updated urls.py)
# ---------------------------------------------------------------------

@login_required
def notifications_center(request):
    qs = (
        Notification.objects.filter(recipient=request.user)
        .select_related("actor")
        .order_by("-created_at")
    )
    unread_count = qs.filter(is_read=False).count()
    return render(
        request,
        "notifications.html",
        {"notifications": qs[:200], "unread_count": unread_count},
    )


@login_required
@require_POST
def notification_mark_read(request, notification_id):
    Notification.objects.filter(id=notification_id, recipient=request.user).update(is_read=True)
    return redirect(_next_or_referer(request))


@login_required
@require_POST
def notification_dismiss(request, notification_id):
    Notification.objects.filter(id=notification_id, recipient=request.user).delete()
    return redirect(_next_or_referer(request))


@login_required
@require_POST
def notifications_clear_all(request):
    Notification.objects.filter(recipient=request.user).delete()
    messages.success(request, "Notifications cleared.")
    return redirect(_next_or_referer(request))


# ---------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------

def home(request):
    return render(request, "home.html")


# ---------------------------------------------------------------------
# Landlord Dashboard
# ---------------------------------------------------------------------

@login_required
def landlord_dashboard(request):
    properties_qs = Property.objects.filter(landlord=request.user)
    units_qs = Unit.objects.filter(property__landlord=request.user, is_active=True)

    total_units = units_qs.count()
    occupied_units = units_qs.filter(vacancy=Unit.OCCUPIED).count()
    vacant_units = units_qs.filter(vacancy=Unit.VACANT).count()
    booked_units = units_qs.filter(vacancy=Unit.BOOKED).count()
    maintenance_units = units_qs.filter(vacancy=Unit.MAINTENANCE).count()

    occupancy_rate = round((occupied_units / total_units * 100), 1) if total_units else 0

    expected_monthly_rent = (
        units_qs.aggregate(total=Sum("price", output_field=DecimalField()))["total"] or Decimal("0")
    )

    current_month = timezone.now().strftime("%Y-%m")

    rent_paid_month = (
        ReceiptHistory.objects.filter(
            tenant__unit_fk__property__landlord=request.user,
            month_paid_for=current_month,
        ).aggregate(total=Sum("rent_paid", output_field=DecimalField()))["total"] or Decimal("0")
    )

    rent_remaining_month = expected_monthly_rent - rent_paid_month

    outstanding_balance_total = (
        Tenant.objects.filter(unit_fk__property__landlord=request.user)
        .aggregate(total=Sum("rent_balance", output_field=DecimalField()))["total"]
        or Decimal("0")
    )

    pending_bookings = (
        Booking.objects.filter(unit__property__landlord=request.user, status=Booking.STATUS_PENDING)
        .select_related("tenant", "unit", "unit__property")
        .order_by("-created_at")[:8]
    )

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
    properties = (
        properties_qs.annotate(
            total_units_count=Count("units", filter=active_units_filter, distinct=True),
            vacant_units_count=Count(
                "units",
                filter=Q(units__is_active=True, units__vacancy=Unit.VACANT),
                distinct=True,
            ),
            occupied_units_count=Count(
                "units",
                filter=Q(units__is_active=True, units__vacancy=Unit.OCCUPIED),
                distinct=True,
            ),
            booked_units_count=Count(
                "units",
                filter=Q(units__is_active=True, units__vacancy=Unit.BOOKED),
                distinct=True,
            ),
            expected_monthly_rent=Sum("units__price", filter=active_units_filter, output_field=DecimalField()),
        )
        .prefetch_related("images")
        .order_by("-created_at")
    )

    context = {
        "total_units": total_units,
        "occupied_units": occupied_units,
        "vacant_units": vacant_units,
        "booked_units": booked_units,
        "maintenance_units": maintenance_units,
        "occupancy_rate": occupancy_rate,

        "expected_monthly_rent": expected_monthly_rent,
        "rent_paid_month": rent_paid_month,
        "rent_remaining_month": rent_remaining_month,
        "outstanding_balance_total": outstanding_balance_total,

        # compatibility keys
        "total_monthly_rent": expected_monthly_rent,
        "rent_collected_month": rent_paid_month,
        "outstanding_balance": outstanding_balance_total,

        "pending_bookings": pending_bookings,
        "pending_count": pending_bookings.count(),
        "recent_tenants": recent_tenants,
        "recent_payments": recent_payments,

        "properties": properties,
        "total_properties": properties.count(),
    }
    return render(request, "landlord_dashboard.html", context)


# ---------------------------------------------------------------------
# Tenant Feed
# ---------------------------------------------------------------------

def tenant_feed(request):
    properties_list = Property.objects.all().select_related("landlord").prefetch_related("images")

    county = request.GET.get("county", "")
    town = request.GET.get("town", "")
    unit_type = request.GET.get("property_type", "")
    min_price = request.GET.get("min_price", "")
    max_price = request.GET.get("max_price", "")

    if county:
        properties_list = properties_list.filter(county__icontains=county)
    if town:
        properties_list = properties_list.filter(town__icontains=town)

    unit_filter = Q(units__is_active=True, units__vacancy=Unit.VACANT)

    if unit_type:
        unit_filter &= Q(units__unit_type=unit_type)

    if min_price:
        unit_filter &= Q(units__price__gte=_normalize_decimal(min_price, default="0"))

    if max_price:
        unit_filter &= Q(units__price__lte=_normalize_decimal(max_price, default="999999999"))

    properties_list = (
        properties_list.filter(unit_filter)
        .distinct()
        .annotate(
            vacant_units_filtered=Count("units", filter=unit_filter, distinct=True),
            min_vacant_price=Min("units__price", filter=unit_filter),
            total_units_active=Count("units", filter=Q(units__is_active=True), distinct=True),
        )
        .order_by("-created_at")
    )

    paginator = Paginator(properties_list, 10)
    properties = paginator.get_page(request.GET.get("page"))

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

    for prop in properties:
        prop.amenities = extract_amenities(getattr(prop, "description", ""))
        prop.vacant_units_display = getattr(prop, "vacant_units_filtered", 0) or 0

    context = {
        "properties": properties,
        "selected_county": county,
        "selected_town": town,
        "selected_type": unit_type,
        "selected_min_price": min_price,
        "selected_max_price": max_price,
        "user_bookings": [],
        "saved_properties": [],
    }

    if request.user.is_authenticated:
        context["user_bookings"] = list(
            Booking.objects.filter(tenant=request.user, status__in=ACTIVE_BOOKING_STATUSES)
            .values_list("unit__property_id", flat=True)
            .distinct()
        )
        context["saved_properties"] = list(
            TenantCollection.objects.filter(tenant=request.user).values_list("property_id", flat=True)
        )

    return render(request, "tenant_feed.html", context)


# ---------------------------------------------------------------------
# Properties CRUD
# ---------------------------------------------------------------------

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
                prop.save()

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
                        u.vacancy = Unit.VACANT
                    if u.is_active is None:
                        u.is_active = True
                    u.save()

                for obj in formset.deleted_objects:
                    obj.delete()

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
                prop = form.save()

                units = unit_formset.save(commit=False)
                for u in units:
                    if not (u.unit_code or "").strip():
                        continue
                    u.save()

                for obj in unit_formset.deleted_objects:
                    if obj.vacancy == Unit.OCCUPIED:
                        messages.error(request, f"Cannot delete occupied unit {obj.unit_code}. Mark inactive instead.")
                        continue
                    obj.delete()

                image_file = form.cleaned_data.get("property_image")
                if image_file:
                    has_primary = PropertyImage.objects.filter(property=prop, is_primary=True).exists()
                    PropertyImage.objects.create(property=prop, image=image_file, is_primary=(not has_primary))

            messages.success(request, "Property updated successfully.")
            return redirect("edit_property", property_id=prop.id)

        messages.error(request, "Please fix the errors below.")
    else:
        form = PropertyForm(instance=prop)
        unit_formset = UnitFormSet(instance=prop)

    return render(request, "edit_property.html", {"form": form, "unit_formset": unit_formset, "property": prop})


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

    if not PropertyImage.objects.filter(property=prop, is_primary=True).exists():
        next_img = PropertyImage.objects.filter(property=prop).order_by("id").first()
        if next_img:
            next_img.is_primary = True
            next_img.save(update_fields=["is_primary"])

    messages.success(request, "Image deleted successfully.")
    return redirect("edit_property", property_id=prop.id)


# ---------------------------------------------------------------------
# Bookings (confirmed DOES NOT mean occupied)
# ---------------------------------------------------------------------

@login_required
def book_property(request, property_id):
    prop = get_object_or_404(Property, id=property_id)

    with transaction.atomic():
        unit = (
            Unit.objects.select_for_update()
            .filter(property=prop, vacancy=Unit.VACANT, is_active=True)
            .order_by("id")
            .first()
        )
        if not unit:
            messages.error(request, "No vacant units available for this property.")
            return redirect("tenant_feed")

        if Booking.objects.filter(tenant=request.user, unit=unit, status__in=ACTIVE_BOOKING_STATUSES).exists():
            messages.info(request, "You already have an active booking for this unit.")
            return redirect("tenant_feed")

        Booking.objects.create(unit=unit, tenant=request.user, status=Booking.STATUS_PENDING)
        _set_unit_vacancy(unit, Unit.BOOKED)

    _notify(
        prop.landlord,
        f"New booking request: {prop.name} — Unit {unit.unit_code} (from {request.user.username}).",
        level=Notification.LEVEL_INFO,
        actor=request.user,
        url="",
    )

    messages.success(request, "Booking request sent. Track it under My Bookings.")
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
        "confirmed_count": sum(1 for b in bookings if b.status == Booking.STATUS_CONFIRMED),
        "pending_count": sum(1 for b in bookings if b.status == Booking.STATUS_PENDING),
        "cancelled_count": sum(1 for b in bookings if b.status == Booking.STATUS_CANCELLED),
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
        "bookings_count": bookings.count(),
        "confirmed_count": bookings.filter(status=Booking.STATUS_CONFIRMED).count(),
        "pending_count": bookings.filter(status=Booking.STATUS_PENDING).count(),
        "cancelled_count": bookings.filter(status=Booking.STATUS_CANCELLED).count(),
    }
    return render(request, "tenant_bookings.html", context)


@login_required
def unbook_property(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id, tenant=request.user)
    prop_name = booking.unit.property.name if booking.unit_id else "booking"
    landlord = booking.unit.property.landlord if booking.unit_id else None
    unit_code = booking.unit.unit_code if booking.unit_id else ""

    with transaction.atomic():
        unit = None
        if booking.unit_id and booking.status in ACTIVE_BOOKING_STATUSES:
            unit = Unit.objects.select_for_update().get(id=booking.unit_id)

        booking.delete()

        if (
            unit
            and unit.vacancy == Unit.BOOKED
            and (not _unit_has_active_bookings(unit))
            and (not _unit_has_tenant(unit))
        ):
            _set_unit_vacancy(unit, Unit.VACANT)

    if landlord:
        _notify(
            landlord,
            f"Booking cancelled by tenant: {prop_name} — Unit {unit_code} ({request.user.username}).",
            level=Notification.LEVEL_WARNING,
            actor=request.user,
            url="",
        )

    messages.success(request, f"Successfully unbooked {prop_name}.")
    return redirect("tenant_bookings")


@login_required
def update_booking_status(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)

    if booking.unit.property.landlord != request.user:
        return redirect("landlord_bookings")

    if request.method == "POST":
        new_status = request.POST.get("status")
        if new_status in [Booking.STATUS_CONFIRMED, Booking.STATUS_CANCELLED, Booking.STATUS_PENDING]:
            with transaction.atomic():
                unit = Unit.objects.select_for_update().get(id=booking.unit_id)

                Booking.objects.filter(id=booking.id).update(status=new_status)

                if new_status in (Booking.STATUS_PENDING, Booking.STATUS_CONFIRMED):
                    if unit.vacancy != Unit.OCCUPIED:
                        _set_unit_vacancy(unit, Unit.BOOKED)
                else:
                    if unit.vacancy == Unit.BOOKED and (not _unit_has_active_bookings(unit)) and (not _unit_has_tenant(unit)):
                        _set_unit_vacancy(unit, Unit.VACANT)

            level = Notification.LEVEL_INFO
            if new_status == Booking.STATUS_CONFIRMED:
                level = Notification.LEVEL_SUCCESS
            elif new_status == Booking.STATUS_CANCELLED:
                level = Notification.LEVEL_WARNING

            _notify(
                booking.tenant,
                f"Your booking for {unit.property.name} — Unit {unit.unit_code} is now {new_status.upper()}.",
                level=level,
                actor=request.user,
                url="",
            )

            messages.success(request, f"Booking status updated to {new_status}.")
    return redirect("landlord_bookings")


# ---------------------------------------------------------------------
# Clear cancelled booking (tenant deletes cancelled booking entry)
# ---------------------------------------------------------------------

@login_required
def clear_booking(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id, tenant=request.user)

    if booking.status != Booking.STATUS_CANCELLED:
        messages.error(request, "Only cancelled bookings can be cleared.")
        return redirect("tenant_bookings")

    booking.delete()
    messages.success(request, "Cancelled booking cleared.")
    return redirect("tenant_bookings")


# ---------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------

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

    next_url = request.GET.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("tenant_feed")


@login_required
def view_collection(request):
    unit_filter = Q(property__units__is_active=True, property__units__vacancy=Unit.VACANT)

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
        Booking.objects.filter(tenant=request.user, status__in=ACTIVE_BOOKING_STATUSES)
        .values_list("unit__property_id", flat=True)
        .distinct()
    )

    return render(request, "collection.html", {"collection": collection, "user_bookings": booked_property_ids})


# ---------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# Tenants (booked -> occupied + correct cumulative math + payment increments + initial deposit)
# ---------------------------------------------------------------------

@login_required
def manage_tenants(request):
    if request.method == "POST":
        form = TenantForm(request.POST, user=request.user)
        if form.is_valid():
            deposit_amount = _safe_deposit_amount(request)

            with transaction.atomic():
                tenant_obj = form.save(commit=False)

                unit = (
                    Unit.objects.select_for_update()
                    .select_related("property", "property__landlord")
                    .get(id=tenant_obj.unit_fk_id)
                )

                if unit.property.landlord != request.user:
                    messages.error(request, "You can only add tenants to your own units.")
                    return redirect("manage_tenants")

                if Tenant.objects.filter(unit_fk=unit).exists():
                    messages.error(request, "That unit already has a tenant. Choose another unit.")
                    return redirect("manage_tenants")

                affected_bookings = list(
                    Booking.objects.filter(unit=unit, status__in=ACTIVE_BOOKING_STATUSES).select_related("tenant")
                )

                cancelled = Booking.objects.filter(unit=unit, status__in=ACTIVE_BOOKING_STATUSES).update(
                    status=Booking.STATUS_CANCELLED
                )

                _set_unit_vacancy(unit, Unit.OCCUPIED)

                payment_amount = _safe_payment_amount(request)
                if payment_amount > 0:
                    tenant_obj.rent_paid = _normalize_decimal(tenant_obj.rent_paid, "0") + payment_amount

                tenant_obj.rent_balance = (
                    _normalize_decimal(tenant_obj.monthly_rent, "0") - _normalize_decimal(tenant_obj.rent_paid, "0")
                )
                tenant_obj.cumulative_rent_balance = tenant_obj.rent_balance
                tenant_obj.unit_fk = unit
                tenant_obj.save()

                if cancelled and affected_bookings:
                    for b in affected_bookings:
                        _notify(
                            b.tenant,
                            f"Your booking for {unit.property.name} — Unit {unit.unit_code} was cancelled (unit is now occupied).",
                            level=Notification.LEVEL_WARNING,
                            actor=request.user,
                        )

                if deposit_amount > 0:
                    DepositHistory.objects.create(
                        tenant=_get_tenant_user_fk(tenant_obj),
                        unit=unit,
                        amount=deposit_amount,
                        date_received=_parse_iso_date(request.POST.get("deposit_date")) or timezone.now().date(),
                        reference=(request.POST.get("deposit_reference") or "").strip(),
                        notes=(request.POST.get("deposit_notes") or "").strip(),
                    )

            if cancelled:
                messages.info(request, f"Note: {cancelled} active booking(s) for this unit were cancelled.")
            if deposit_amount > 0:
                messages.success(
                    request,
                    f"Tenant {tenant_obj.name} added successfully. Deposit recorded (KES {deposit_amount:,.0f}).",
                )
            else:
                messages.success(request, f"Tenant {tenant_obj.name} added successfully.")
            return redirect("tenant_list")
    else:
        form = TenantForm(user=request.user)

    return render(request, "manage_tenants.html", {"form": form})


@login_required
def edit_tenant(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id)

    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to edit this tenant.")
        return redirect("tenant_list")

    old_unit_id = tenant_obj.unit_fk_id
    old_rent_balance = _normalize_decimal(tenant_obj.rent_balance, "0")
    old_cum = _normalize_decimal(tenant_obj.cumulative_rent_balance, "0")

    if request.method == "POST":
        form = TenantForm(request.POST, instance=tenant_obj, user=request.user)
        if form.is_valid():
            payment_amount = _safe_payment_amount(request)

            with transaction.atomic():
                updated = form.save(commit=False)

                new_unit = Unit.objects.select_for_update().select_related("property").get(id=updated.unit_fk_id)
                old_unit = Unit.objects.select_for_update().get(id=old_unit_id)

                if new_unit.property.landlord != request.user:
                    messages.error(request, "You can only assign tenants to your own units.")
                    return redirect("tenant_list")

                cancelled = 0

                if new_unit.id != old_unit.id:
                    if Tenant.objects.filter(unit_fk=new_unit).exclude(id=tenant_obj.id).exists():
                        messages.error(request, "That unit already has a tenant. Choose another unit.")
                        return redirect("tenant_list")

                    if _unit_has_active_bookings(old_unit) and (not _unit_has_tenant(old_unit)):
                        _set_unit_vacancy(old_unit, Unit.BOOKED)
                    else:
                        _set_unit_vacancy(old_unit, Unit.VACANT)

                    affected = list(
                        Booking.objects.filter(unit=new_unit, status__in=ACTIVE_BOOKING_STATUSES).select_related("tenant")
                    )
                    cancelled = Booking.objects.filter(unit=new_unit, status__in=ACTIVE_BOOKING_STATUSES).update(
                        status=Booking.STATUS_CANCELLED
                    )
                    _set_unit_vacancy(new_unit, Unit.OCCUPIED)

                    if cancelled and affected:
                        for b in affected:
                            _notify(
                                b.tenant,
                                f"Your booking for {new_unit.property.name} — Unit {new_unit.unit_code} was cancelled (unit is now occupied).",
                                level=Notification.LEVEL_WARNING,
                                actor=request.user,
                            )
                else:
                    _set_unit_vacancy(new_unit, Unit.OCCUPIED)

                if payment_amount > 0:
                    updated.rent_paid = _normalize_decimal(updated.rent_paid, "0") + payment_amount

                monthly_rent = _normalize_decimal(updated.monthly_rent, "0")
                rent_paid = _normalize_decimal(updated.rent_paid, "0")

                new_balance = monthly_rent - rent_paid
                updated.rent_balance = new_balance
                updated.cumulative_rent_balance = (old_cum - old_rent_balance) + new_balance

                updated.unit_fk = new_unit
                updated.save()

            if cancelled:
                messages.info(request, f"Note: {cancelled} active booking(s) for this unit were cancelled.")
            if payment_amount > 0:
                messages.success(request, f"Payment recorded (KES {payment_amount:,.0f}). Tenant updated successfully.")
            else:
                messages.success(request, f"Tenant {updated.name} updated successfully.")
            return redirect("tenant_list")
    else:
        form = TenantForm(instance=tenant_obj, user=request.user)

    return render(request, "edit_tenant.html", {"form": form, "tenant": tenant_obj})


@login_required
def delete_tenant(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id)

    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to delete this tenant.")
        return redirect("tenant_list")

    with transaction.atomic():
        unit = Unit.objects.select_for_update().get(id=tenant_obj.unit_fk_id)
        tenant_name = tenant_obj.name
        tenant_obj.delete()

        if _unit_has_active_bookings(unit):
            _set_unit_vacancy(unit, Unit.BOOKED)
        else:
            _set_unit_vacancy(unit, Unit.VACANT)

    messages.success(request, f"Tenant {tenant_name} deleted successfully.")
    return redirect("tenant_list")


@login_required
def tenant_list(request):
    tenants = (
        Tenant.objects.filter(unit_fk__property__landlord=request.user)
        .select_related("unit_fk", "unit_fk__property")
        .order_by("-date_paid")
    )

    unit_ids = [t.unit_fk_id for t in tenants if t.unit_fk_id]
    deposit_totals = {}
    if unit_ids:
        rows = (
            DepositHistory.objects.filter(unit_id__in=unit_ids)
            .values("unit_id")
            .annotate(total=Sum("amount", output_field=DecimalField()))
        )
        deposit_totals = {r["unit_id"]: (r["total"] or Decimal("0")) for r in rows}

    for t in tenants:
        t.deposit_total = deposit_totals.get(t.unit_fk_id, Decimal("0"))

    return render(request, "tenant_list.html", {"tenant_list": tenants})


# ---------------------------------------------------------------------
# Password Reset
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# Receipts (rent receipt + shows deposit total for unit)
# ---------------------------------------------------------------------

@login_required
def generate_receipt(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id)

    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to generate a receipt for this tenant.")
        return redirect("tenant_list")

    issued_at = timezone.now()
    receipt_no = f"ER-{issued_at.strftime('%Y%m%d')}-{get_random_string(6, allowed_chars='ABCDEFGHJKLMNPQRSTUVWXYZ23456789')}"

    ReceiptHistory.objects.create(
        tenant=tenant_obj,
        rent_paid=tenant_obj.rent_paid,
        date_paid=tenant_obj.date_paid,
        month_paid_for=tenant_obj.month_paid_for,
        monthly_rent=tenant_obj.monthly_rent,
        rent_balance=tenant_obj.rent_balance,
        cumulative_rent_balance=tenant_obj.cumulative_rent_balance,
    )

    unit = tenant_obj.unit_fk

    deposit_total = (
        DepositHistory.objects.filter(unit=unit)
        .aggregate(total=Sum("amount", output_field=DecimalField()))["total"]
        or Decimal("0")
    )

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="receipt_{tenant_obj.name}.pdf"'

    p = canvas.Canvas(response, pagesize=A6)
    width, height = A6

    brand = HexColor("#2F5BEA")
    brand_dark = HexColor("#1F3FB8")
    bg = HexColor("#FFFFFF")
    light_bg = HexColor("#F5F7FB")
    text = HexColor("#111827")
    muted = HexColor("#6B7280")
    border = HexColor("#E5E7EB")
    success = HexColor("#16A34A")

    p.setFillColor(bg)
    p.rect(0, 0, width, height, fill=1, stroke=0)

    header_h = 18 * mm
    p.setFillColor(brand)
    p.rect(0, height - header_h, width, header_h, fill=1, stroke=0)

    p.setFillColor(HexColor("#FFFFFF"))
    p.setFont("Helvetica-Bold", 12)
    p.drawString(8 * mm, height - 11 * mm, "EasyRent")
    p.setFont("Helvetica", 9)
    p.drawRightString(width - 8 * mm, height - 11 * mm, "RECEIPT")

    y = height - header_h - 5 * mm
    p.setFillColor(text)
    p.setFont("Helvetica-Bold", 8)
    p.drawString(8 * mm, y, f"Receipt No: {receipt_no}")
    p.setFont("Helvetica", 8)
    p.setFillColor(muted)
    p.drawRightString(width - 8 * mm, y, f"Issued: {issued_at.strftime('%d %b %Y, %H:%M')}")
    y -= 6 * mm

    prop = unit.property
    landlord = request.user

    tenant_name = _safe_str(tenant_obj.name)
    tenant_phone = _safe_str(getattr(tenant_obj, "phone", None))
    property_name = _safe_str(getattr(prop, "name", None))
    property_loc = ", ".join([x for x in [getattr(prop, "town", ""), getattr(prop, "county", "")] if x]) or "—"
    unit_code = _safe_str(getattr(unit, "unit_code", None))

    landlord_name = _safe_str((landlord.get_full_name() or landlord.username))
    landlord_email = _safe_str(getattr(landlord, "email", None))
    landlord_phone = _safe_str(getattr(prop, "landlord_phone", None))

    month_paid_for = _safe_str(getattr(tenant_obj, "month_paid_for", None))
    date_paid = _safe_str(getattr(tenant_obj, "date_paid", None))
    date_occupied = _safe_str(getattr(tenant_obj, "date_occupied", None))

    rent_paid = _money(getattr(tenant_obj, "rent_paid", 0))
    monthly_rent = _money(getattr(tenant_obj, "monthly_rent", 0))
    rent_balance = _money(getattr(tenant_obj, "rent_balance", 0))
    cumulative_balance = _money(getattr(tenant_obj, "cumulative_rent_balance", 0))
    deposit_total_fmt = _money(deposit_total)

    def section_title(label: str, yy: float):
        p.setFillColor(brand_dark)
        p.setFont("Helvetica-Bold", 8)
        p.drawString(8 * mm, yy, label)

    def keyval(k: str, v: str, yy: float):
        p.setFont("Helvetica", 8)
        p.setFillColor(muted)
        p.drawString(8 * mm, yy, k)
        p.setFillColor(text)
        p.drawRightString(width - 8 * mm, yy, v)

    def box(x, yy_top, w, h):
        p.setFillColor(light_bg)
        p.setStrokeColor(border)
        p.setLineWidth(0.7)
        p.rect(x, yy_top - h, w, h, fill=1, stroke=1)

    parties_h = 32 * mm
    box(6 * mm, y, width - 12 * mm, parties_h)
    yy = y - 5 * mm

    section_title("Tenant", yy); yy -= 5 * mm
    keyval("Name", tenant_name, yy); yy -= 4.2 * mm
    keyval("Phone", tenant_phone, yy); yy -= 4.2 * mm
    keyval("Date Occupied", date_occupied, yy); yy -= 5.2 * mm

    section_title("Landlord / Issuer", yy); yy -= 5 * mm
    keyval("Name", landlord_name, yy); yy -= 4.2 * mm
    keyval("Phone", landlord_phone, yy)

    y -= (parties_h + 6 * mm)

    prop_h = 18 * mm
    box(6 * mm, y, width - 12 * mm, prop_h)
    yy = y - 5 * mm
    section_title("Property Details", yy); yy -= 5 * mm
    keyval("Property", property_name, yy); yy -= 4.2 * mm
    keyval("Location", property_loc, yy); yy -= 4.2 * mm
    keyval("Unit", unit_code, yy)

    y -= (prop_h + 6 * mm)

    pay_h = 38 * mm
    box(6 * mm, y, width - 12 * mm, pay_h)
    yy = y - 5 * mm
    section_title("Payment", yy); yy -= 5 * mm

    keyval("Month Paid For", month_paid_for, yy); yy -= 4.2 * mm
    keyval("Date Paid", date_paid, yy); yy -= 5.0 * mm

    def amount_row(label, amount, yy_row, bold=False, color=text):
        p.setFillColor(muted)
        p.setFont("Helvetica-Bold" if bold else "Helvetica", 8)
        p.drawString(8 * mm, yy_row, label)
        p.setFillColor(color)
        p.drawRightString(width - 8 * mm, yy_row, f"KES {amount}")

    amount_row("Monthly Rent", monthly_rent, yy); yy -= 4.2 * mm
    amount_row("Amount Received", rent_paid, yy, bold=True, color=success); yy -= 4.2 * mm
    amount_row("Balance (This Month)", rent_balance, yy); yy -= 4.2 * mm
    amount_row("Cumulative Balance", cumulative_balance, yy); yy -= 4.2 * mm
    amount_row("Deposit (Total)", deposit_total_fmt, yy); yy -= 4.2 * mm

    p.setStrokeColor(border)
    p.setLineWidth(0.7)
    p.line(8 * mm, 22 * mm, width - 8 * mm, 22 * mm)

    p.setFillColor(muted)
    p.setFont("Helvetica", 7)
    p.drawString(8 * mm, 18 * mm, "Issued by EasyRent. This receipt is generated electronically.")
    p.drawString(8 * mm, 14.5 * mm, f"Support / Email: {landlord_email}")

    p.setFillColor(text)
    p.setFont("Helvetica", 7)
    p.drawRightString(width - 8 * mm, 14.5 * mm, "Signature: ____________________")

    p.setStrokeColor(border)
    p.setLineWidth(1)
    p.rect(4 * mm, 4 * mm, width - 8 * mm, height - 8 * mm, fill=0, stroke=1)

    p.showPage()
    p.save()
    return response


@login_required
def receipt_history(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id)

    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to view this receipt history.")
        return redirect("tenant_list")

    history = ReceiptHistory.objects.filter(tenant=tenant_obj).order_by("-date_generated")
    return render(request, "receipt_history.html", {"tenant": tenant_obj, "history": history})
