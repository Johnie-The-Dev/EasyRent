# listings/views.py (UNIT-FIRST / NO FALLBACK) — UPDATED ✅ (Apartment removed everywhere)
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DecimalField, Min, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
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
    PasswordResetCode,
    Property,
    PropertyImage,
    RentPayment,
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


def landlord_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_landlord():
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return login_required(wrapped)


def tenant_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_tenant():
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return login_required(wrapped)


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
    return Tenant.objects.filter(unit_fk=unit, is_active=True).exists()


def _unit_has_history(unit: Unit) -> bool:
    return unit.tenancies.exists() or unit.bookings.exists() or unit.deposit_records.exists()


def _property_has_history(prop: Property) -> bool:
    return (
        Tenant.objects.filter(unit_fk__property=prop).exists()
        or Booking.objects.filter(unit__property=prop).exists()
        or DepositHistory.objects.filter(unit__property=prop).exists()
    )


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


def _safe_payment_amount(form) -> Decimal:
    """Return the validated payment amount entered for this request."""
    amt = _normalize_decimal(form.cleaned_data.get("payment_amount"), default="0")
    return amt if amt > 0 else Decimal("0")


def _safe_deposit_amount(form) -> Decimal:
    """Return the validated deposit amount entered for this request."""
    amt = _normalize_decimal(form.cleaned_data.get("deposit_amount"), default="0")
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


def _valid_unit_types_set():
    """
    Server-side safety for filters:
    since 'Apartment' is removed, ignore it if someone still passes it.
    """
    return {k for (k, _label) in getattr(Unit, "UNIT_TYPE_CHOICES", [])}


def _available_unit_filter(prefix="units"):
    return Q(
        **{
            f"{prefix}__is_active": True,
            f"{prefix}__vacancy": Unit.VACANT,
        }
    ) & ~Q(**{f"{prefix}__tenancies__is_active": True})


def _safe_from_email() -> str:
    """
    Ensure we always have a usable from email.
    """
    return (
        getattr(settings, "DEFAULT_FROM_EMAIL", None)
        or getattr(settings, "EMAIL_HOST_USER", None)
        or "no-reply@easyrent.local"
    )


def _normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


# ---------------------------------------------------------------------
# Notifications
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

@landlord_required
def landlord_dashboard(request):
    properties_qs = Property.objects.filter(landlord=request.user)
    units_qs = Unit.objects.filter(property__landlord=request.user, is_active=True)

    total_units = units_qs.count()
    occupied_units = units_qs.filter(vacancy=Unit.OCCUPIED).count()
    vacant_units = units_qs.filter(vacancy=Unit.VACANT).exclude(tenancies__is_active=True).distinct().count()
    booked_units = units_qs.filter(vacancy=Unit.BOOKED).count()
    maintenance_units = units_qs.filter(vacancy=Unit.MAINTENANCE).count()

    occupancy_rate = round((occupied_units / total_units * 100), 1) if total_units else 0

    expected_monthly_rent = (
        units_qs.aggregate(total=Sum("price", output_field=DecimalField()))["total"] or Decimal("0")
    )

    current_month = timezone.now().strftime("%Y-%m")

    rent_paid_month = (
        RentPayment.objects.filter(
            tenant__unit_fk__property__landlord=request.user,
            month_paid_for=current_month,
        ).aggregate(total=Sum("amount", output_field=DecimalField()))["total"] or Decimal("0")
    )

    rent_remaining_month = expected_monthly_rent - rent_paid_month

    outstanding_balance_total = (
        Tenant.objects.filter(unit_fk__property__landlord=request.user, is_active=True)
        .aggregate(total=Sum("rent_balance", output_field=DecimalField()))["total"]
        or Decimal("0")
    )

    pending_bookings = (
        Booking.objects.filter(unit__property__landlord=request.user, status=Booking.STATUS_PENDING)
        .select_related("tenant", "unit", "unit__property")
        .order_by("-created_at")[:8]
    )

    recent_tenants = (
        Tenant.objects.filter(unit_fk__property__landlord=request.user, is_active=True)
        .select_related("unit_fk", "unit_fk__property")
        .prefetch_related("payments")
        .order_by("-date_paid")[:8]
    )

    recent_payments = (
        RentPayment.objects.filter(tenant__unit_fk__property__landlord=request.user)
        .select_related("tenant", "tenant__unit_fk", "tenant__unit_fk__property")
        .order_by("-created_at")[:8]
    )

    active_units_filter = Q(units__is_active=True)
    available_units_filter = _available_unit_filter()
    properties = (
        properties_qs.annotate(
            total_units_count=Count("units", filter=active_units_filter, distinct=True),
            vacant_units_count=Count(
                "units",
                filter=available_units_filter,
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
        "pending_count": Booking.objects.filter(
            unit__property__landlord=request.user,
            status=Booking.STATUS_PENDING,
        ).count(),
        "recent_tenants": recent_tenants,
        "recent_payments": recent_payments,
        "properties": properties,
        "total_properties": properties.count(),
    }
    return render(request, "landlord_dashboard.html", context)


# ---------------------------------------------------------------------
# Tenant Feed (filter is UNIT TYPE only; Apartment removed & ignored if provided)
# ---------------------------------------------------------------------

def tenant_feed(request):
    properties_list = (
        Property.objects.all()
        .select_related("landlord")
        .prefetch_related("images")
    )

    county = request.GET.get("county", "").strip()
    town = request.GET.get("town", "").strip()
    unit_type = request.GET.get("property_type", "").strip()  # still named property_type in query for compatibility
    min_price = request.GET.get("min_price", "").strip()
    max_price = request.GET.get("max_price", "").strip()

    if county:
        properties_list = properties_list.filter(county__icontains=county)
    if town:
        properties_list = properties_list.filter(town__icontains=town)

    unit_filter = _available_unit_filter()

    valid_unit_types = _valid_unit_types_set()
    if unit_type and unit_type in valid_unit_types:
        unit_filter &= Q(units__unit_type=unit_type)
    else:
        unit_type = ""

    if min_price:
        unit_filter &= Q(units__price__gte=_normalize_decimal(min_price, default="0"))

    if max_price:
        unit_filter &= Q(units__price__lte=_normalize_decimal(max_price, default="999999999"))

    unit_filter &= ~Q(units__tenancies__is_active=True)

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

    if request.user.is_authenticated and request.user.is_tenant():
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

@landlord_required
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
                    if u.pk is None and not u.is_active:
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


@landlord_required
def edit_property(request, property_id):
    prop = get_object_or_404(Property, id=property_id, landlord=request.user)

    if request.method == "POST":
        form = PropertyForm(request.POST, request.FILES, instance=prop)
        unit_formset = UnitFormSet(request.POST, request.FILES, instance=prop)

        if form.is_valid() and unit_formset.is_valid():
            units = unit_formset.save(commit=False)
            protected_units = [
                unit
                for unit in unit_formset.deleted_objects
                if unit.vacancy in (Unit.OCCUPIED, Unit.BOOKED) or _unit_has_history(unit)
            ]
            if protected_units:
                unit_codes = ", ".join(unit.unit_code for unit in protected_units)
                messages.error(
                    request,
                    f"Cannot delete unit(s) {unit_codes} because they have occupancy or history. Mark them inactive instead.",
                )
                return render(
                    request,
                    "edit_property.html",
                    {"form": form, "unit_formset": unit_formset, "property": prop},
                )

            with transaction.atomic():
                prop = form.save()

                for u in units:
                    if not (u.unit_code or "").strip():
                        continue
                    u.save()

                for obj in unit_formset.deleted_objects:
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


@landlord_required
def delete_property(request, property_id):
    prop = get_object_or_404(Property, id=property_id, landlord=request.user)
    if request.method == "POST":
        if _property_has_history(prop):
            messages.error(
                request,
                "This property has tenant, booking, or deposit history and cannot be deleted. Mark its units inactive instead.",
            )
            return redirect("landlord_dashboard")
        prop.delete()
        messages.success(request, "Property deleted.")
        return redirect("landlord_dashboard")
    return render(request, "delete_property.html", {"property": prop})


@landlord_required
@require_POST
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

@tenant_required
@require_POST
def book_property(request, property_id):
    with transaction.atomic():
        prop = get_object_or_404(Property.objects.select_for_update(), id=property_id)
        if Booking.objects.filter(
            tenant=request.user,
            unit__property=prop,
            status__in=ACTIVE_BOOKING_STATUSES,
        ).exists():
            messages.info(request, "You already have an active booking in this property.")
            return redirect("tenant_feed")

        unit = (
            Unit.objects.select_for_update()
            .filter(property=prop, vacancy=Unit.VACANT, is_active=True)
            .exclude(tenancies__is_active=True)
            .order_by("id")
            .first()
        )
        if not unit:
            messages.error(request, "No vacant units available for this property.")
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


@landlord_required
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
        "moved_in_count": sum(1 for b in bookings if b.status == Booking.STATUS_MOVED_IN),
    }
    return render(request, "landlord_bookings.html", context)


@tenant_required
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
        "moved_in_count": bookings.filter(status=Booking.STATUS_MOVED_IN).count(),
    }
    return render(request, "tenant_bookings.html", context)


@tenant_required
@require_POST
def unbook_property(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id, tenant=request.user)
    if booking.status not in ACTIVE_BOOKING_STATUSES:
        messages.error(request, "Only pending or confirmed bookings can be cancelled.")
        return redirect("tenant_bookings")

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


@landlord_required
@require_POST
def update_booking_status(request, booking_id):
    booking = get_object_or_404(Booking, id=booking_id)

    if booking.unit.property.landlord != request.user:
        return redirect("landlord_bookings")

    new_status = request.POST.get("status")
    allowed_transitions = {
        Booking.STATUS_PENDING: {Booking.STATUS_CONFIRMED, Booking.STATUS_CANCELLED},
        Booking.STATUS_CONFIRMED: {Booking.STATUS_CANCELLED},
        Booking.STATUS_CANCELLED: set(),
    }

    with transaction.atomic():
        booking = Booking.objects.select_for_update().select_related("tenant", "unit").get(id=booking.id)
        unit = Unit.objects.select_for_update().get(id=booking.unit_id)

        if new_status not in allowed_transitions.get(booking.status, set()):
            messages.error(request, "That booking status transition is not allowed.")
            return redirect("landlord_bookings")

        if new_status == Booking.STATUS_CONFIRMED:
            other_active_booking = Booking.objects.filter(
                unit=unit,
                status__in=ACTIVE_BOOKING_STATUSES,
            ).exclude(id=booking.id).exists()
            if (
                not unit.is_active
                or unit.vacancy in (Unit.OCCUPIED, Unit.MAINTENANCE)
                or _unit_has_tenant(unit)
                or other_active_booking
            ):
                messages.error(request, "This unit is no longer available to confirm.")
                return redirect("landlord_bookings")

        booking.status = new_status
        booking.save(update_fields=["status", "updated_at"])

        if new_status == Booking.STATUS_CONFIRMED:
            _set_unit_vacancy(unit, Unit.BOOKED)
        elif (
            unit.vacancy == Unit.BOOKED
            and not _unit_has_active_bookings(unit)
            and not _unit_has_tenant(unit)
        ):
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


@tenant_required
@require_POST
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

@tenant_required
@require_POST
def add_to_collection(request, property_id):
    prop = get_object_or_404(Property, id=property_id)
    _, created = TenantCollection.objects.get_or_create(tenant=request.user, property=prop)
    messages.success(
        request,
        "Property added to your collection!" if created else "Property is already in your collection.",
    )
    return redirect("tenant_feed")


@tenant_required
@require_POST
def remove_from_collection(request, property_id):
    prop = get_object_or_404(Property, id=property_id)
    TenantCollection.objects.filter(tenant=request.user, property=prop).delete()

    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect("tenant_feed")


@tenant_required
def view_collection(request):
    unit_filter = _available_unit_filter("property__units")

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

@landlord_required
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


@tenant_required
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

@landlord_required
def manage_tenants(request):
    booking_id = request.POST.get("booking_id") if request.method == "POST" else request.GET.get("booking")
    booking = None
    initial = {}

    if booking_id:
        booking = get_object_or_404(
            Booking.objects.select_related("tenant", "unit", "unit__property"),
            id=booking_id,
            unit__property__landlord=request.user,
        )
        if booking.status != Booking.STATUS_CONFIRMED or Tenant.objects.filter(booking=booking).exists():
            messages.error(request, "Only an unconverted confirmed booking can be moved in.")
            return redirect("landlord_bookings")

        today = timezone.localdate()
        initial = {
            "name": booking.tenant.get_full_name() or booking.tenant.username,
            "unit_fk": booking.unit_id,
            "date_occupied": booking.move_in_date or today,
            "date_paid": today,
            "month_paid_for": today.strftime("%Y-%m"),
            "monthly_rent": booking.unit.price,
        }

    if request.method == "POST":
        form = TenantForm(request.POST, user=request.user, initial=initial)
        if booking:
            form.fields["unit_fk"].disabled = True
        if form.is_valid():
            deposit_amount = _safe_deposit_amount(form)
            payment_amount = _safe_payment_amount(form)

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

                if Tenant.objects.filter(unit_fk=unit, is_active=True).exists():
                    messages.error(request, "That unit already has a tenant. Choose another unit.")
                    return redirect("manage_tenants")

                if not booking:
                    confirmed_booking = Booking.objects.filter(
                        unit=unit,
                        status=Booking.STATUS_CONFIRMED,
                    ).first()
                    if confirmed_booking:
                        form.add_error(
                            "unit_fk",
                            "This unit has a confirmed booking. Use Create tenancy from the Bookings page, or cancel the booking first.",
                        )
                        return render(request, "manage_tenants.html", {"form": form, "booking": None})

                source_booking = None
                if booking:
                    source_booking = Booking.objects.select_for_update().select_related(
                        "tenant", "unit", "unit__property"
                    ).get(id=booking.id)
                    if (
                        source_booking.status != Booking.STATUS_CONFIRMED
                        or source_booking.unit.property.landlord != request.user
                        or source_booking.unit_id != unit.id
                        or Tenant.objects.filter(booking=source_booking).exists()
                    ):
                        messages.error(request, "This booking is no longer available to convert.")
                        return redirect("landlord_bookings")

                affected_bookings = list(
                    Booking.objects.filter(unit=unit, status__in=ACTIVE_BOOKING_STATUSES)
                    .exclude(id=source_booking.id if source_booking else None)
                    .select_related("tenant")
                )

                competing_bookings = Booking.objects.filter(unit=unit, status__in=ACTIVE_BOOKING_STATUSES)
                if source_booking:
                    competing_bookings = competing_bookings.exclude(id=source_booking.id)
                    source_booking.status = Booking.STATUS_MOVED_IN
                    source_booking.move_in_date = source_booking.move_in_date or tenant_obj.date_occupied
                    source_booking.save(update_fields=["status", "move_in_date", "updated_at"])
                cancelled = competing_bookings.update(status=Booking.STATUS_CANCELLED)

                _set_unit_vacancy(unit, Unit.OCCUPIED)

                tenant_obj.rent_paid = payment_amount

                tenant_obj.rent_balance = (
                    _normalize_decimal(tenant_obj.monthly_rent, "0") - _normalize_decimal(tenant_obj.rent_paid, "0")
                )
                tenant_obj.cumulative_rent_balance = tenant_obj.rent_balance
                tenant_obj.unit_fk = unit
                tenant_obj.booking = source_booking
                tenant_obj.save()

                if tenant_obj.rent_paid > 0:
                    RentPayment.objects.create(
                        tenant=tenant_obj,
                        amount=tenant_obj.rent_paid,
                        date_paid=tenant_obj.date_paid,
                        month_paid_for=tenant_obj.month_paid_for,
                        monthly_rent=tenant_obj.monthly_rent,
                        rent_balance=tenant_obj.rent_balance,
                        cumulative_rent_balance=tenant_obj.cumulative_rent_balance,
                    )

                if cancelled and affected_bookings:
                    for b in affected_bookings:
                        _notify(
                            b.tenant,
                            f"Your booking for {unit.property.name} — Unit {unit.unit_code} was cancelled because the unit is now occupied.",
                            level=Notification.LEVEL_WARNING,
                            actor=request.user,
                        )

                if deposit_amount > 0:
                    DepositHistory.objects.create(
                        tenant=(source_booking.tenant if source_booking else _get_tenant_user_fk(tenant_obj)),
                        unit=unit,
                        tenancy=tenant_obj,
                        amount=deposit_amount,
                        date_received=form.cleaned_data.get("deposit_date") or timezone.now().date(),
                        reference=(request.POST.get("deposit_reference") or "").strip(),
                        notes=(request.POST.get("deposit_notes") or "").strip(),
                    )

            if source_booking:
                _notify(
                    source_booking.tenant,
                    f"Your booking for {unit.property.name} — Unit {unit.unit_code} is now a tenancy.",
                    level=Notification.LEVEL_SUCCESS,
                    actor=request.user,
                )

            if cancelled:
                messages.info(request, f"Note: {cancelled} competing booking(s) for this unit were cancelled.")
            if deposit_amount > 0:
                success_message = f"Tenant {tenant_obj.name} added successfully. Deposit recorded (KES {deposit_amount:,.0f})."
                if source_booking:
                    success_message = f"Booking moved in and tenancy created for {tenant_obj.name}. Deposit recorded (KES {deposit_amount:,.0f})."
                messages.success(
                    request,
                    success_message,
                )
            elif source_booking:
                messages.success(request, f"Booking moved in and tenancy created for {tenant_obj.name}.")
            else:
                messages.success(request, f"Tenant {tenant_obj.name} added successfully.")
            return redirect("tenant_list")
    else:
        form = TenantForm(user=request.user, initial=initial)
        if booking:
            form.fields["unit_fk"].disabled = True

    return render(request, "manage_tenants.html", {"form": form, "booking": booking})


@landlord_required
def edit_tenant(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id, is_active=True)

    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to edit this tenant.")
        return redirect("tenant_list")

    old_unit_id = tenant_obj.unit_fk_id
    old_rent_balance = _normalize_decimal(tenant_obj.rent_balance, "0")
    old_cum = _normalize_decimal(tenant_obj.cumulative_rent_balance, "0")

    if request.method == "POST":
        form = TenantForm(request.POST, instance=tenant_obj, user=request.user)
        if form.is_valid():
            payment_amount = _safe_payment_amount(form)

            with transaction.atomic():
                updated = form.save(commit=False)

                new_unit = Unit.objects.select_for_update().select_related("property").get(id=updated.unit_fk_id)
                old_unit = Unit.objects.select_for_update().get(id=old_unit_id)

                if new_unit.property.landlord != request.user:
                    messages.error(request, "You can only assign tenants to your own units.")
                    return redirect("tenant_list")

                cancelled = 0

                if new_unit.id != old_unit.id:
                    if Tenant.objects.filter(unit_fk=new_unit, is_active=True).exclude(id=tenant_obj.id).exists():
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

                month_paid_total = (
                    RentPayment.objects.filter(tenant=tenant_obj, month_paid_for=updated.month_paid_for)
                    .aggregate(total=Sum("amount", output_field=DecimalField()))["total"]
                    or Decimal("0")
                )
                updated.rent_paid = month_paid_total + payment_amount

                monthly_rent = _normalize_decimal(updated.monthly_rent, "0")
                rent_paid = _normalize_decimal(updated.rent_paid, "0")

                new_balance = monthly_rent - rent_paid
                updated.rent_balance = new_balance
                updated.cumulative_rent_balance = (old_cum - old_rent_balance) + new_balance

                updated.unit_fk = new_unit
                updated.save()

                if payment_amount > 0:
                    RentPayment.objects.create(
                        tenant=updated,
                        amount=payment_amount,
                        date_paid=updated.date_paid,
                        month_paid_for=updated.month_paid_for,
                        monthly_rent=updated.monthly_rent,
                        rent_balance=updated.rent_balance,
                        cumulative_rent_balance=updated.cumulative_rent_balance,
                    )

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


@landlord_required
@require_POST
def delete_tenant(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id, is_active=True)

    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to delete this tenant.")
        return redirect("tenant_list")

    with transaction.atomic():
        unit = Unit.objects.select_for_update().get(id=tenant_obj.unit_fk_id)
        tenant_name = tenant_obj.name
        tenant_obj.is_active = False
        tenant_obj.save(update_fields=["is_active"])

        if _unit_has_active_bookings(unit):
            _set_unit_vacancy(unit, Unit.BOOKED)
        else:
            _set_unit_vacancy(unit, Unit.VACANT)

    messages.success(request, f"Tenancy for {tenant_name} ended. Payment and deposit history was retained.")
    return redirect("tenant_list")


@landlord_required
def tenant_list(request):
    tenants = (
        Tenant.objects.filter(unit_fk__property__landlord=request.user, is_active=True)
        .select_related("unit_fk", "unit_fk__property")
        .order_by("-date_paid")
    )

    tenant_ids = [t.id for t in tenants]
    deposit_totals = {}
    if tenant_ids:
        rows = (
            DepositHistory.objects.filter(tenancy_id__in=tenant_ids)
            .values("tenancy_id")
            .annotate(total=Sum("amount", output_field=DecimalField()))
        )
        deposit_totals = {r["tenancy_id"]: (r["total"] or Decimal("0")) for r in rows}

    for t in tenants:
        t.deposit_total = deposit_totals.get(t.id, Decimal("0"))

    return render(request, "tenant_list.html", {"tenant_list": tenants})


# ---------------------------------------------------------------------
# Password Reset (DB-backed: PasswordResetCode has code_hash/expires_at/used_at)
# ---------------------------------------------------------------------

RESET_CODE_LENGTH = 6
RESET_CODE_TTL_MINUTES = 10


def _hash_reset_code(email: str, code: str) -> str:
    """
    HMAC-SHA256(secret, "email|code") => hex digest
    """
    secret = (settings.SECRET_KEY or "").encode("utf-8")
    msg = f"{_normalize_email(email)}|{(code or '').strip()}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def _mask_email(email: str) -> str:
    email = (email or "").strip()
    if "@" not in email:
        return email
    name, dom = email.split("@", 1)
    shown = (name[:2] if len(name) >= 2 else (name[:1] if name else ""))
    return f"{shown}***@{dom}"


class RequestPasswordResetCodeView(View):
    def get(self, request):
        return render(request, "password_reset_request.html")

    def post(self, request):
        email = (request.POST.get("email") or "").strip()
        if not email:
            messages.error(request, "Please enter your email.")
            return redirect("password_reset")

        # Always respond generically to avoid account enumeration
        generic_ok = "If an account exists for that email, a reset code has been sent."

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            messages.success(request, generic_ok)
            return redirect("password_reset_verify")

        now = timezone.now()
        expires_at = now + timedelta(minutes=RESET_CODE_TTL_MINUTES)

        code = get_random_string(RESET_CODE_LENGTH, allowed_chars="0123456789")
        code_hash = _hash_reset_code(email, code)

        with transaction.atomic():
            # Invalidate any active codes for this email
            PasswordResetCode.objects.filter(
                email__iexact=email,
                used_at__isnull=True,
                expires_at__gt=now,
            ).update(used_at=now)

            PasswordResetCode.objects.create(
                email=email,
                code_hash=code_hash,
                created_at=now,
                expires_at=expires_at,
                used_at=None,
            )

        subject = "Your Password Reset Code"
        body = (
            f"Your password reset code is: {code}\n\n"
            f"This code expires in {RESET_CODE_TTL_MINUTES} minutes.\n"
            f"If you didn’t request this, ignore this email."
        )

        try:
            send_mail(
                subject,
                body,
                _safe_from_email(),
                [email],
                fail_silently=False,
            )
        except Exception as e:
            if getattr(settings, "DEBUG", False):
                # keep dev flow working even without SMTP
                print(f"[DEV] Password reset code for {email}: {code} ({e.__class__.__name__})")
                messages.warning(
                    request,
                    "SMTP not configured in DEBUG. Check console logs for the code or configure SMTP.",
                )
                return redirect("password_reset_verify")

            messages.error(request, "We couldn't send an email right now. Please try again later.")
            return redirect("password_reset")

        messages.success(request, f"A reset code has been sent to {_mask_email(email)}.")
        return redirect("password_reset_verify")


class VerifyResetCodeView(View):
    def get(self, request):
        return render(request, "password_reset_verify.html")

    def post(self, request):
        email = (request.POST.get("email") or "").strip()
        code = (request.POST.get("code") or "").strip()

        if not email or not code:
            messages.error(request, "Enter your email and the code.")
            return redirect("password_reset_verify")

        now = timezone.now()
        code_hash = _hash_reset_code(email, code)

        prc = (
            PasswordResetCode.objects.filter(
                email__iexact=email,
                code_hash=code_hash,
                used_at__isnull=True,
                expires_at__gt=now,
            )
            .order_by("-created_at")
            .first()
        )

        if not prc:
            messages.error(request, "Invalid or expired code. Please request a new one.")
            return redirect("password_reset_verify")

        prc.used_at = now
        prc.save(update_fields=["used_at"])

        request.session["reset_email"] = email
        return redirect("password_reset_new_password")


class SetNewPasswordView(View):
    def get(self, request):
        return render(request, "password_reset_new_password.html")

    def post(self, request):
        email = (request.session.get("reset_email") or "").strip()
        password = (request.POST.get("password") or "").strip()

        if not email:
            messages.error(request, "Your reset session expired. Please request a new code.")
            return redirect("password_reset")

        if not password or len(password) < 8:
            messages.error(request, "Password must be at least 8 characters.")
            return redirect("password_reset_new_password")

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            messages.error(request, "No account found for that email.")
            return redirect("password_reset")

        user.set_password(password)
        user.save(update_fields=["password"])

        request.session.pop("reset_email", None)

        messages.success(request, "Password reset successfully. You can now log in.")
        return redirect("login")


# ---------------------------------------------------------------------
# Receipts (rent receipt + shows deposit total for unit)
# ---------------------------------------------------------------------

@landlord_required
@require_POST
def generate_receipt(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id)

    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to generate a receipt for this tenant.")
        return redirect("tenant_list")

    issued_at = timezone.now()
    payment = tenant_obj.payments.order_by("-created_at", "-id").first()
    if not payment:
        messages.error(request, "No recorded payment is available for a receipt.")
        return redirect("tenant_list")
    receipt_no = f"ER-{payment.created_at:%Y%m%d}-{payment.id:06d}"

    unit = tenant_obj.unit_fk

    deposit_total = (
        DepositHistory.objects.filter(tenancy=tenant_obj)
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

    month_paid_for = _safe_str(payment.month_paid_for)
    date_paid = _safe_str(payment.date_paid)
    date_occupied = _safe_str(getattr(tenant_obj, "date_occupied", None))

    rent_paid = _money(payment.amount)
    monthly_rent = _money(payment.monthly_rent)
    rent_balance = _money(payment.rent_balance)
    cumulative_balance = _money(payment.cumulative_rent_balance)
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


@landlord_required
def receipt_history(request, tenant_id):
    tenant_obj = get_object_or_404(Tenant, id=tenant_id)

    if tenant_obj.unit_fk.property.landlord != request.user:
        messages.error(request, "You don't have permission to view this receipt history.")
        return redirect("tenant_list")

    history = sorted(
        [
            *ReceiptHistory.objects.filter(tenant=tenant_obj),
            *RentPayment.objects.filter(tenant=tenant_obj),
        ],
        key=lambda item: item.date_generated,
        reverse=True,
    )
    return render(request, "receipt_history.html", {"tenant": tenant_obj, "history": history})