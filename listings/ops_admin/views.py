from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.db.models import Count, DecimalField, Q, Sum
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from listings.models import (
    Booking,
    DepositHistory,
    Property,
    ReceiptHistory,
    Tenant,
    TenantCollection,
    Unit,
)

from .pdf import build_simple_table_pdf, parse_range_days

User = get_user_model()


def _superuser_required(view_func):
    @staff_member_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            return HttpResponse("Forbidden", status=403)
        return view_func(request, *args, **kwargs)
    return _wrapped


@_superuser_required
def ops_dashboard(request):
    now = timezone.now()
    current_month = now.strftime("%Y-%m")
    since_7d = now - timedelta(days=7)
    since_30d = now - timedelta(days=30)

    # Users
    total_landlords = User.objects.filter(role="landlord").count()
    total_tenants = User.objects.filter(role="tenant").count()
    new_users_7d = User.objects.filter(date_joined__gte=since_7d).count()

    # Inventory
    total_properties = Property.objects.count()
    active_units_qs = Unit.objects.filter(is_active=True)
    total_units = active_units_qs.count()

    vacant_units = active_units_qs.filter(vacancy=Unit.VACANT).count()
    booked_units = active_units_qs.filter(vacancy=Unit.BOOKED).count()
    occupied_units = active_units_qs.filter(vacancy=Unit.OCCUPIED).count()
    maintenance_units = active_units_qs.filter(vacancy=Unit.MAINTENANCE).count()

    occupancy_rate = round((occupied_units / total_units) * 100, 1) if total_units else 0.0

    # Bookings
    pending_qs = Booking.objects.filter(status=Booking.STATUS_PENDING)
    pending_count = pending_qs.count()
    confirmed_count = Booking.objects.filter(status=Booking.STATUS_CONFIRMED).count()
    cancelled_count = Booking.objects.filter(status=Booking.STATUS_CANCELLED).count()

    oldest_pending = (
        pending_qs.select_related("tenant", "unit", "unit__property")
        .order_by("created_at")
        .first()
    )

    pending_queue = (
        pending_qs.select_related("tenant", "unit", "unit__property")
        .order_by("-created_at")[:12]
    )

    # Demand: top saved properties
    top_saved = (
        TenantCollection.objects
        .values("property_id", "property__name", "property__town")
        .annotate(saves=Count("id"))
        .order_by("-saves")[:10]
    )

    # Demand: top towns by bookings (last 30 days)
    top_towns_by_bookings = (
        Booking.objects.filter(created_at__gte=since_30d)
        .values("unit__property__town")
        .annotate(bookings=Count("id"))
        .order_by("-bookings")[:10]
    )

    # Data integrity: tenants with negative balances
    negative_balances_filter = Q(rent_balance__lt=0) | Q(cumulative_rent_balance__lt=0)
    negative_balances_count = Tenant.objects.filter(negative_balances_filter).count()
    negative_balances = (
        Tenant.objects.filter(negative_balances_filter)
        .select_related("unit_fk", "unit_fk__property", "unit_fk__property__landlord")
        .order_by("cumulative_rent_balance", "rent_balance")[:25]
    )

    # Money throughput (platform-wide)
    expected_monthly_rent = (
        active_units_qs.aggregate(total=Sum("price", output_field=DecimalField()))["total"]
        or Decimal("0")
    )
    rent_collected_month = (
        ReceiptHistory.objects.filter(month_paid_for=current_month)
        .aggregate(total=Sum("rent_paid", output_field=DecimalField()))["total"]
        or Decimal("0")
    )

    first_day = date(now.year, now.month, 1)
    deposits_month = (
        DepositHistory.objects.filter(date_received__gte=first_day, date_received__lte=now.date())
        .aggregate(total=Sum("amount", output_field=DecimalField()))["total"]
        or Decimal("0")
    )

    outstanding_balance_total = (
        Tenant.objects.aggregate(total=Sum("cumulative_rent_balance", output_field=DecimalField()))["total"]
        or Decimal("0")
    )

    # Data integrity checks
    active_booking_statuses = [Booking.STATUS_PENDING, Booking.STATUS_CONFIRMED]

    booked_unit_ids_with_active_booking = set(
        Booking.objects.filter(status__in=active_booking_statuses).values_list("unit_id", flat=True)
    )
    booked_without_booking = (
        active_units_qs.filter(vacancy=Unit.BOOKED)
        .exclude(id__in=booked_unit_ids_with_active_booking)
        .select_related("property")
        .order_by("property_id", "unit_code")[:10]
    )

    occupied_unit_ids_with_tenant = set(Tenant.objects.values_list("unit_fk_id", flat=True))
    occupied_without_tenant = (
        active_units_qs.filter(vacancy=Unit.OCCUPIED)
        .exclude(id__in=occupied_unit_ids_with_tenant)
        .select_related("property")
        .order_by("property_id", "unit_code")[:10]
    )

    context = {
        "cards": {
            "total_landlords": total_landlords,
            "total_tenants": total_tenants,
            "new_users_7d": new_users_7d,
            "total_properties": total_properties,
            "total_units": total_units,
            "vacant_units": vacant_units,
            "booked_units": booked_units,
            "occupied_units": occupied_units,
            "maintenance_units": maintenance_units,
            "occupancy_rate": occupancy_rate,
            "pending_count": pending_count,
            "confirmed_count": confirmed_count,
            "cancelled_count": cancelled_count,
            "expected_monthly_rent": expected_monthly_rent,
            "rent_collected_month": rent_collected_month,
            "deposits_month": deposits_month,
            "outstanding_balance_total": outstanding_balance_total,
        },
        "oldest_pending": oldest_pending,
        "pending_queue": pending_queue,

        # Demand
        "top_saved": top_saved,
        "top_towns_by_bookings": top_towns_by_bookings,

        # Integrity
        "negative_balances_count": negative_balances_count,
        "negative_balances": negative_balances,

        # Existing integrity checks
        "booked_without_booking": booked_without_booking,
        "occupied_without_tenant": occupied_without_tenant,
    }
    return render(request, "admin/ops_dashboard.html", context)


@_superuser_required
def report_market_overview_pdf(request):
    now = timezone.now()
    days = parse_range_days(request, default=30)
    since = now - timedelta(days=days)

    users_total = User.objects.count()
    landlords_total = User.objects.filter(role="landlord").count()
    tenants_total = User.objects.filter(role="tenant").count()
    new_users = User.objects.filter(date_joined__gte=since).count()

    props_total = Property.objects.count()
    units_active = Unit.objects.filter(is_active=True)
    units_total = units_active.count()

    vacancy_counts = units_active.values("vacancy").annotate(c=Count("id"))
    vac = {r["vacancy"]: r["c"] for r in vacancy_counts}

    bookings_in_range = Booking.objects.filter(created_at__gte=since)
    rows = [
        ["Metric", "Value"],
        ["Users (total)", str(users_total)],
        ["Landlords (total)", str(landlords_total)],
        ["Tenants (total)", str(tenants_total)],
        [f"New users (last {days} days)", str(new_users)],
        ["Properties (total)", str(props_total)],
        ["Active units (total)", str(units_total)],
        ["Vacant units", str(vac.get(Unit.VACANT, 0))],
        ["Booked units", str(vac.get(Unit.BOOKED, 0))],
        ["Occupied units", str(vac.get(Unit.OCCUPIED, 0))],
        ["Maintenance units", str(vac.get(Unit.MAINTENANCE, 0))],
        ["Bookings pending (range)", str(bookings_in_range.filter(status=Booking.STATUS_PENDING).count())],
        ["Bookings confirmed (range)", str(bookings_in_range.filter(status=Booking.STATUS_CONFIRMED).count())],
        ["Bookings cancelled (range)", str(bookings_in_range.filter(status=Booking.STATUS_CANCELLED).count())],
        ["", ""],
        [f"Top towns by properties (last {days} days)", ""],
        ["Town", "Properties"],
    ]

    top_towns_props = (
        Property.objects.filter(created_at__gte=since)
        .values("town").annotate(properties=Count("id")).order_by("-properties")[:10]
    )
    for r in top_towns_props:
        rows.append([r["town"] or "—", str(r["properties"])])

    rows.append(["", ""])
    rows.append([f"Top towns by bookings (last {days} days)", ""])
    rows.append(["Town", "Bookings"])
    top_towns_bookings = (
        Booking.objects.filter(created_at__gte=since)
        .values("unit__property__town")
        .annotate(bookings=Count("id"))
        .order_by("-bookings")[:10]
    )
    for r in top_towns_bookings:
        rows.append([r["unit__property__town"] or "—", str(r["bookings"])])

    rows.append(["", ""])
    rows.append([f"Top saved properties (last {days} days)", ""])
    rows.append(["Property", "Town | Saves"])
    top_saved = (
        TenantCollection.objects.filter(added_at__gte=since)
        .values("property__name", "property__town")
        .annotate(saves=Count("id"))
        .order_by("-saves")[:10]
    )
    for r in top_saved:
        rows.append([r["property__name"] or "—", f"{r['property__town'] or '—'} | {r['saves']}"])

    return build_simple_table_pdf(
        title=f"EasyRent — Market Overview (last {days} days)",
        subtitle=f"Generated: {now.strftime('%Y-%m-%d %H:%M')} (Africa/Nairobi)",
        rows=rows,
        filename=f"market_overview_last_{days}_days.pdf",
    )


@_superuser_required
def report_receipts_ledger_pdf(request):
    now = timezone.now()
    days = parse_range_days(request, default=30)
    since = now - timedelta(days=days)

    qs = (
        ReceiptHistory.objects.filter(date_generated__gte=since)
        .select_related("tenant", "tenant__unit_fk", "tenant__unit_fk__property", "tenant__unit_fk__property__landlord")
        .order_by("-date_generated")
    )

    rows = [["Date", "Tenant", "Property", "Unit", "Landlord", "Month", "Paid", "Balance", "CumBal"]]
    for r in qs[:2000]:
        unit = getattr(r.tenant, "unit_fk", None)
        prop = getattr(unit, "property", None) if unit else None
        landlord = getattr(prop, "landlord", None) if prop else None
        rows.append([
            r.date_generated.strftime("%Y-%m-%d %H:%M"),
            getattr(r.tenant, "name", "—"),
            getattr(prop, "name", "—"),
            getattr(unit, "unit_code", "—"),
            getattr(landlord, "username", "—"),
            r.month_paid_for,
            f"{r.rent_paid:,.0f}",
            f"{r.rent_balance:,.0f}",
            f"{r.cumulative_rent_balance:,.0f}",
        ])

    return build_simple_table_pdf(
        title=f"EasyRent — Receipts Ledger (last {days} days)",
        subtitle=f"Rows shown: {len(rows)-1} (capped). Generated: {now.strftime('%Y-%m-%d %H:%M')}",
        rows=rows,
        filename=f"receipts_ledger_last_{days}_days.pdf",
        landscape=True,
    )


@_superuser_required
def report_deposits_ledger_pdf(request):
    now = timezone.now()
    days = parse_range_days(request, default=30)
    since = now.date() - timedelta(days=days)

    qs = (
        DepositHistory.objects.filter(date_received__gte=since)
        .select_related("unit", "unit__property", "unit__property__landlord", "tenant")
        .order_by("-date_received", "-created_at")
    )

    rows = [["Date", "Property", "Unit", "Landlord", "TenantUser", "Amount", "Reference", "Notes"]]
    for d in qs[:2000]:
        prop = getattr(d.unit, "property", None)
        landlord = getattr(prop, "landlord", None) if prop else None
        tenant_user = getattr(d, "tenant", None)
        rows.append([
            d.date_received.strftime("%Y-%m-%d"),
            getattr(prop, "name", "—"),
            getattr(d.unit, "unit_code", "—"),
            getattr(landlord, "username", "—"),
            getattr(tenant_user, "username", "—") if tenant_user else "—",
            f"{d.amount:,.0f}",
            d.reference or "—",
            (d.notes or "—")[:60],
        ])

    return build_simple_table_pdf(
        title=f"EasyRent — Deposits Ledger (last {days} days)",
        subtitle=f"Rows shown: {len(rows)-1} (capped). Generated: {now.strftime('%Y-%m-%d %H:%M')}",
        rows=rows,
        filename=f"deposits_ledger_last_{days}_days.pdf",
        landscape=True,
    )


@_superuser_required
def report_user_registry_pdf(request):
    now = timezone.now()
    days = parse_range_days(request, default=30)
    since = now - timedelta(days=days)

    role = (request.GET.get("role") or "all").strip().lower()
    qs = User.objects.filter(date_joined__gte=since)
    if role in ("tenant", "landlord"):
        qs = qs.filter(role=role)

    qs = qs.order_by("-date_joined")

    rows = [["Username", "Email", "Role", "Active", "Joined", "Last login"]]
    for u in qs[:4000]:
        rows.append([
            u.username,
            u.email or "—",
            getattr(u, "role", "—"),
            "yes" if u.is_active else "no",
            u.date_joined.strftime("%Y-%m-%d %H:%M") if u.date_joined else "—",
            u.last_login.strftime("%Y-%m-%d %H:%M") if u.last_login else "—",
        ])

    return build_simple_table_pdf(
        title=f"EasyRent — User Registry (new in last {days} days, role={role})",
        subtitle=f"Rows shown: {len(rows)-1} (capped). Generated: {now.strftime('%Y-%m-%d %H:%M')}",
        rows=rows,
        filename=f"user_registry_last_{days}_days_{role}.pdf",
        landscape=True,
    )