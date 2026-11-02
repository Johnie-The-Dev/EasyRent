from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.paginator import Paginator
from django.db import connections, transaction
from django.db.models import Count, DecimalField, Q, Sum
from django.utils.dateparse import parse_date
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from listings.models import (
    Booking,
    DepositHistory,
    ManagerAuditEvent,
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

from .pdf import build_simple_table_pdf, parse_range_days

User = get_user_model()
logger = logging.getLogger(__name__)


def _superuser_required(view_func):
    @staff_member_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            return HttpResponseForbidden("Forbidden")
        return view_func(request, *args, **kwargs)
    return _wrapped


def _delete_user_and_related_data(user, actor):
    database = user._state.db or "default"
    landlord_properties = Property.objects.using(database).filter(landlord=user)
    user_bookings = Booking.objects.using(database).filter(
        Q(tenant=user) | Q(unit__property__landlord=user)
    )
    related_tenancies = Tenant.objects.using(database).filter(
        Q(user=user) | Q(unit_fk__property__landlord=user) | Q(booking__in=user_bookings)
    )
    active_tenancy_unit_ids = list(
        related_tenancies.filter(is_active=True).values_list("unit_fk_id", flat=True).distinct()
    )
    affected_unit_ids = set(active_tenancy_unit_ids)
    affected_unit_ids.update(user_bookings.values_list("unit_id", flat=True))

    files_to_delete = []
    for prop in landlord_properties.only("video"):
        if prop.video:
            files_to_delete.append((prop.video.storage, prop.video.name))
    for image in PropertyImage.objects.using(database).filter(
        property__landlord=user
    ).only("image"):
        if image.image:
            files_to_delete.append((image.image.storage, image.image.name))

    with transaction.atomic(using=database):
        tenancy_ids = related_tenancies.order_by().values("pk")
        _record_account_audit_event(
            actor,
            user,
            ManagerAuditEvent.ACTION_DELETED,
        )
        ManagerAuditEvent.objects.using(database).filter(
            target_username=user.username,
        ).update(target_username="Deleted account")
        Notification.objects.using(database).filter(
            Q(recipient=user) | Q(actor=user)
        ).delete()
        RentPayment.objects.using(database).filter(tenant_id__in=tenancy_ids).delete()
        ReceiptHistory.objects.using(database).filter(tenant_id__in=tenancy_ids).delete()
        DepositHistory.objects.using(database).filter(
            Q(tenant=user) | Q(tenancy_id__in=tenancy_ids) | Q(unit__property__landlord=user)
        ).delete()
        if user.email:
            PasswordResetCode.objects.using(database).filter(email__iexact=user.email).delete()
        related_tenancies.delete()

        user_bookings.delete()
        Unit.objects.using(database).filter(
            pk__in=affected_unit_ids,
            vacancy__in=[Unit.OCCUPIED, Unit.BOOKED],
        ).exclude(tenancies__is_active=True).exclude(
            bookings__status__in=[Booking.STATUS_PENDING, Booking.STATUS_CONFIRMED]
        ).update(vacancy=Unit.VACANT)
        landlord_properties.delete()
        user.delete(using=database)

    for storage, name in files_to_delete:
        storage.delete(name)

    logger.info(
        "Manager user %s deleted user %s and related EasyRent data",
        actor.pk,
        user.pk,
    )


def _record_account_audit_event(actor, managed_user, action):
    database = managed_user._state.db or "default"
    return ManagerAuditEvent.objects.using(database).create(
        actor_id=actor.pk,
        action=action,
        target_username=managed_user.username,
        target_role=managed_user.role,
        details=f"{managed_user.get_role_display()} account {action.removeprefix('account_')}.",
    )


@_superuser_required
def ops_dashboard(request):
    now = timezone.now()
    current_month = now.strftime("%Y-%m")
    since_7d = now - timedelta(days=7)
    since_30d = now - timedelta(days=30)

    # Users
    total_landlords = User.objects.filter(
        role="landlord",
        is_staff=False,
        is_superuser=False,
    ).count()
    total_tenants = User.objects.filter(
        role="tenant",
        is_staff=False,
        is_superuser=False,
    ).count()
    admin_accounts = User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).count()
    new_users_7d = User.objects.filter(date_joined__gte=since_7d).count()

    # Inventory
    total_properties = Property.objects.count()
    active_units_qs = Unit.objects.filter(is_active=True)
    total_units = active_units_qs.count()
    active_tenancy_records = Tenant.objects.filter(is_active=True).count()

    vacant_units = active_units_qs.filter(vacancy=Unit.VACANT).count()
    booked_units = active_units_qs.filter(vacancy=Unit.BOOKED).count()
    occupied_units = active_units_qs.filter(vacancy=Unit.OCCUPIED).count()
    maintenance_units = active_units_qs.filter(vacancy=Unit.MAINTENANCE).count()

    occupancy_rate = round((occupied_units / total_units) * 100, 1) if total_units else 0.0

    # Bookings
    pending_qs = Booking.objects.filter(status=Booking.STATUS_PENDING)
    pending_count = pending_qs.count()
    stale_pending_cutoff = now - timedelta(days=7)
    stale_pending_count = pending_qs.filter(created_at__lt=stale_pending_cutoff).count()
    confirmed_count = Booking.objects.filter(status=Booking.STATUS_CONFIRMED).count()
    cancelled_count = Booking.objects.filter(status=Booking.STATUS_CANCELLED).count()
    moved_in_count = Booking.objects.filter(status=Booking.STATUS_MOVED_IN).count()

    oldest_pending = (
        pending_qs.select_related("tenant", "unit", "unit__property")
        .order_by("created_at")
        .first()
    )

    pending_queue = (
        pending_qs.select_related("tenant", "unit", "unit__property")
        .order_by("-created_at")[:12]
    )
    recent_admin_activity = ManagerAuditEvent.objects.select_related("actor")[:8]

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
    negative_balances_count = Tenant.objects.filter(is_active=True).filter(negative_balances_filter).count()
    negative_balances = (
        Tenant.objects.filter(is_active=True).filter(negative_balances_filter)
        .select_related("unit_fk", "unit_fk__property", "unit_fk__property__landlord")
        .order_by("cumulative_rent_balance", "rent_balance")[:25]
    )

    # Money throughput (platform-wide)
    expected_monthly_rent = (
        active_units_qs.aggregate(total=Sum("price", output_field=DecimalField()))["total"]
        or Decimal("0")
    )
    rent_collected_month = (
        RentPayment.objects.filter(month_paid_for=current_month)
        .aggregate(total=Sum("amount", output_field=DecimalField()))["total"]
        or Decimal("0")
    )

    first_day = date(now.year, now.month, 1)
    deposits_month = (
        DepositHistory.objects.filter(date_received__gte=first_day, date_received__lte=now.date())
        .aggregate(total=Sum("amount", output_field=DecimalField()))["total"]
        or Decimal("0")
    )

    outstanding_balance_total = (
        Tenant.objects.filter(is_active=True)
        .aggregate(total=Sum("cumulative_rent_balance", output_field=DecimalField()))["total"]
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

    occupied_unit_ids_with_tenant = set(
        Tenant.objects.filter(is_active=True).values_list("unit_fk_id", flat=True)
    )
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
            "admin_accounts": admin_accounts,
            "active_users": User.objects.filter(is_active=True).count(),
            "inactive_users": User.objects.filter(is_active=False).count(),
            "new_users_7d": new_users_7d,
            "generated_at": now,
            "total_properties": total_properties,
            "total_units": total_units,
            "active_tenancy_records": active_tenancy_records,
            "vacant_units": vacant_units,
            "booked_units": booked_units,
            "occupied_units": occupied_units,
            "maintenance_units": maintenance_units,
            "occupancy_rate": occupancy_rate,
            "pending_count": pending_count,
            "stale_pending_count": stale_pending_count,
            "confirmed_count": confirmed_count,
            "cancelled_count": cancelled_count,
            "moved_in_count": moved_in_count,
            "expected_monthly_rent": expected_monthly_rent,
            "rent_collected_month": rent_collected_month,
            "deposits_month": deposits_month,
            "outstanding_balance_total": outstanding_balance_total,
        },
        "oldest_pending": oldest_pending,
        "pending_queue": pending_queue,
        "recent_admin_activity": recent_admin_activity,
        "stale_pending_cutoff": stale_pending_cutoff,

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
def manage_users(request):
    role = (request.GET.get("role") or "all").strip().lower()
    search = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "all").strip().lower()

    users = User.objects.all()
    if role in ("tenant", "landlord"):
        users = users.filter(role=role)
    if status == "active":
        users = users.filter(is_active=True)
    elif status == "inactive":
        users = users.filter(is_active=False)
    if search:
        users = users.filter(
            Q(username__icontains=search)
            | Q(email__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
        )

    users = users.order_by("-date_joined", "username")
    paginator = Paginator(users, 25)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "admin/ops_users.html",
        {
            "page": page,
            "role": role if role in ("tenant", "landlord") else "all",
            "status": status if status in ("active", "inactive") else "all",
            "search": search,
            "total_users": paginator.count,
        },
    )


@_superuser_required
def audit_log(request):
    action = (request.GET.get("action") or "all").strip()
    search = (request.GET.get("q") or "").strip()
    start_date = parse_date(request.GET.get("from", "").strip())
    end_date = parse_date(request.GET.get("to", "").strip())

    events = ManagerAuditEvent.objects.select_related("actor")
    valid_actions = {value for value, _label in ManagerAuditEvent.ACTION_CHOICES}
    if action in valid_actions:
        events = events.filter(action=action)
    else:
        action = "all"
    if search:
        events = events.filter(
            Q(target_username__icontains=search)
            | Q(actor__username__icontains=search)
            | Q(details__icontains=search)
        )
    if start_date:
        events = events.filter(created_at__date__gte=start_date)
    if end_date:
        events = events.filter(created_at__date__lte=end_date)

    paginator = Paginator(events, 50)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "admin/ops_audit_log.html",
        {
            "page": page,
            "action": action,
            "search": search,
            "start_date": start_date,
            "end_date": end_date,
            "total_events": paginator.count,
            "actions": ManagerAuditEvent.ACTION_CHOICES,
        },
    )


@_superuser_required
def user_detail(request, user_id):
    managed_user = get_object_or_404(User, pk=user_id)
    properties = Property.objects.filter(landlord=managed_user).prefetch_related(
        "units"
    ).order_by("-created_at")
    tenancies = Tenant.objects.filter(
        Q(user=managed_user)
        | Q(unit_fk__property__landlord=managed_user)
        | Q(booking__tenant=managed_user)
    ).select_related("unit_fk", "unit_fk__property").distinct()
    bookings = Booking.objects.filter(
        Q(tenant=managed_user) | Q(unit__property__landlord=managed_user)
    ).select_related("tenant", "unit", "unit__property").order_by("-created_at")
    collections = TenantCollection.objects.filter(tenant=managed_user).select_related(
        "property"
    ).order_by("-added_at")
    deposits = DepositHistory.objects.filter(
        Q(tenant=managed_user) | Q(unit__property__landlord=managed_user)
    ).select_related("unit", "unit__property", "tenant", "tenancy").order_by(
        "-date_received", "-created_at"
    )
    payments = RentPayment.objects.filter(
        tenant__in=tenancies.order_by().values("pk")
    ).select_related("tenant").order_by("-created_at")
    receipts = ReceiptHistory.objects.filter(
        tenant__in=tenancies.order_by().values("pk")
    ).select_related("tenant").order_by("-date_generated")
    properties_count = properties.count()
    tenancies_count = tenancies.count()
    bookings_count = bookings.count()
    collections_count = collections.count()
    deposits_count = deposits.count()
    payments_count = payments.count()
    receipts_count = receipts.count()

    context = {
        "managed_user": managed_user,
        "properties": properties[:100],
        "tenancies": tenancies[:100],
        "bookings": bookings[:100],
        "collections": collections[:100],
        "deposits": deposits[:100],
        "payments": payments[:100],
        "receipts": receipts[:100],
        "units_count": Unit.objects.filter(property__landlord=managed_user).count(),
        "payments_count": payments_count,
        "receipts_count": receipts_count,
        "deposits_count": deposits_count,
        "collections_count": collections_count,
        "bookings_count": bookings_count,
        "tenancies_count": tenancies_count,
        "properties_count": properties_count,
    }
    return render(request, "admin/ops_user_detail.html", context)


@_superuser_required
def toggle_user_active(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    managed_user = get_object_or_404(User, pk=user_id)
    if managed_user.is_superuser or managed_user.pk == request.user.pk:
        return HttpResponseForbidden("Manager and superuser accounts cannot be changed here.")

    database = managed_user._state.db or "default"
    with transaction.atomic(using=database):
        managed_user.is_active = not managed_user.is_active
        managed_user.save(update_fields=["is_active"])
        action = (
            ManagerAuditEvent.ACTION_REACTIVATED
            if managed_user.is_active
            else ManagerAuditEvent.ACTION_SUSPENDED
        )
        _record_account_audit_event(request.user, managed_user, action)
    status = "reactivated" if managed_user.is_active else "suspended"
    logger.info(
        "Manager user %s %s user %s",
        request.user.pk,
        status,
        managed_user.pk,
    )
    messages.success(request, f"{managed_user.username} has been {status}.")
    return redirect("ops:user_detail", user_id=managed_user.pk)


@_superuser_required
def delete_user(request, user_id):
    managed_user = get_object_or_404(User, pk=user_id)
    if managed_user.is_superuser or managed_user.pk == request.user.pk:
        return HttpResponseForbidden("Manager and superuser accounts cannot be deleted here.")

    if request.method == "POST":
        if request.POST.get("confirm_username", "").strip() != managed_user.username:
            return render(
                request,
                "admin/ops_user_delete.html",
                {
                    "managed_user": managed_user,
                    "error": "Enter the username exactly to confirm deletion.",
                },
                status=400,
            )
        logger.warning(
            "Manager user %s confirmed deletion of user %s",
            request.user.pk,
            managed_user.pk,
        )
        _delete_user_and_related_data(managed_user, request.user)
        return render(
            request,
            "admin/ops_user_deleted.html",
        )

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET", "POST"])

    return render(
        request,
        "admin/ops_user_delete.html",
        {"managed_user": managed_user},
    )


@_superuser_required
def reset_data(request):
    database_name = connections["default"].settings_dict["NAME"]
    if not database_name:
        return HttpResponse("The default database has no configured name.", status=500)

    if request.method == "POST":
        if request.POST.get("confirm_database", "").strip() != database_name:
            return render(
                request,
                "admin/ops_reset_data.html",
                {
                    "database_name": database_name,
                    "error": "Database name did not match; no data was deleted.",
                },
                status=400,
            )
        if request.POST.get("confirm_action", "").strip() != "DELETE ALL DATA":
            return render(
                request,
                "admin/ops_reset_data.html",
                {
                    "database_name": database_name,
                    "error": "Type DELETE ALL DATA exactly; no data was deleted.",
                },
                status=400,
            )

        logger.warning(
            "Manager user %s initiated a full data reset for database %s",
            request.user.pk,
            database_name,
        )
        call_command("flush", database="default", interactive=False, verbosity=0)
        return render(
            request,
            "admin/ops_reset_complete.html",
            {"database_name": database_name},
        )

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET", "POST"])

    return render(request, "admin/ops_reset_data.html", {"database_name": database_name})


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
        ["Bookings moved in (range)", str(bookings_in_range.filter(status=Booking.STATUS_MOVED_IN).count())],
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
        RentPayment.objects.filter(created_at__gte=since)
        .select_related("tenant", "tenant__unit_fk", "tenant__unit_fk__property", "tenant__unit_fk__property__landlord")
        .order_by("-created_at")
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
    scope = (request.GET.get("scope") or "recent").strip().lower()
    if scope not in {"all", "recent"}:
        scope = "recent"
    qs = User.objects.all() if scope == "all" else User.objects.filter(date_joined__gte=since)
    if role in ("tenant", "landlord"):
        qs = qs.filter(role=role)

    qs = qs.order_by("-date_joined")

    rows = [["Username", "Email", "Role", "Status", "Joined", "Last login"]]
    for u in qs[:4000]:
        rows.append([
            u.username,
            u.email or "—",
            getattr(u, "role", "—"),
            "Active" if u.is_active else "Suspended",
            u.date_joined.strftime("%Y-%m-%d %H:%M") if u.date_joined else "—",
            u.last_login.strftime("%Y-%m-%d %H:%M") if u.last_login else "—",
        ])

    report_scope = "all accounts" if scope == "all" else f"new accounts in last {days} days"
    return build_simple_table_pdf(
        title=f"EasyRent — User Registry ({report_scope}, role={role})",
        subtitle=(
            f"Rows shown: {len(rows)-1}"
            f"{' (capped at 4,000)' if qs.count() > 4000 else ''}. "
            f"Generated: {now.strftime('%Y-%m-%d %H:%M')}"
        ),
        rows=rows,
        filename=(
            f"user_registry_all_{role}.pdf"
            if scope == "all"
            else f"user_registry_last_{days}_days_{role}.pdf"
        ),
        landscape=True,
    )


@_superuser_required
def report_operations_snapshot_pdf(request):
    now = timezone.now()
    current_month = now.strftime("%Y-%m")
    units = Unit.objects.filter(is_active=True)
    active_tenancies = Tenant.objects.filter(is_active=True)
    pending = Booking.objects.filter(status=Booking.STATUS_PENDING)
    stale_cutoff = now - timedelta(days=7)
    expected_rent = (
        units.aggregate(total=Sum("price", output_field=DecimalField()))["total"]
        or Decimal("0")
    )
    rent_collected = (
        RentPayment.objects.filter(month_paid_for=current_month)
        .aggregate(total=Sum("amount", output_field=DecimalField()))["total"]
        or Decimal("0")
    )
    deposits_month = (
        DepositHistory.objects.filter(
            date_received__gte=date(now.year, now.month, 1),
            date_received__lte=now.date(),
        ).aggregate(total=Sum("amount", output_field=DecimalField()))["total"]
        or Decimal("0")
    )
    outstanding = (
        active_tenancies.aggregate(
            total=Sum("cumulative_rent_balance", output_field=DecimalField())
        )["total"]
        or Decimal("0")
    )
    unit_counts = {
        row["vacancy"]: row["total"]
        for row in units.values("vacancy").annotate(total=Count("id"))
    }

    rows = [
        ["Area", "Metric", "Value"],
        ["Accounts", "Landlords", str(User.objects.filter(role="landlord", is_staff=False, is_superuser=False).count())],
        ["Accounts", "Tenants", str(User.objects.filter(role="tenant", is_staff=False, is_superuser=False).count())],
        ["Accounts", "Active accounts", str(User.objects.filter(is_active=True).count())],
        ["Accounts", "Suspended accounts", str(User.objects.filter(is_active=False).count())],
        ["Accounts", "New accounts in last 7 days", str(User.objects.filter(date_joined__gte=now - timedelta(days=7)).count())],
        ["Inventory", "Properties", str(Property.objects.count())],
        ["Inventory", "Active units", str(units.count())],
        ["Inventory", "Vacant units", str(unit_counts.get(Unit.VACANT, 0))],
        ["Inventory", "Booked units", str(unit_counts.get(Unit.BOOKED, 0))],
        ["Inventory", "Occupied units", str(unit_counts.get(Unit.OCCUPIED, 0))],
        ["Inventory", "Maintenance units", str(unit_counts.get(Unit.MAINTENANCE, 0))],
        ["Inventory", "Recorded occupancy rate", f"{(unit_counts.get(Unit.OCCUPIED, 0) / units.count() * 100):.1f}%" if units.exists() else "0.0%"],
        ["Tenancies", "Active tenancy records", str(active_tenancies.count())],
        ["Bookings", "Pending", str(pending.count())],
        ["Bookings", "Pending over 7 days", str(pending.filter(created_at__lt=stale_cutoff).count())],
        ["Bookings", "Confirmed", str(Booking.objects.filter(status=Booking.STATUS_CONFIRMED).count())],
        ["Bookings", "Moved in", str(Booking.objects.filter(status=Booking.STATUS_MOVED_IN).count())],
        ["Bookings", "Cancelled", str(Booking.objects.filter(status=Booking.STATUS_CANCELLED).count())],
        ["Rent and deposits", "Potential monthly rent (KES)", f"{expected_rent:,.2f}"],
        ["Rent and deposits", f"Payments recorded for {current_month} (KES)", f"{rent_collected:,.2f}"],
        ["Rent and deposits", "Deposits received this month to date (KES)", f"{deposits_month:,.2f}"],
        ["Rent and deposits", "Outstanding balances (KES)", f"{outstanding:,.2f}"],
    ]

    return build_simple_table_pdf(
        title="EasyRent — Operations Snapshot",
        subtitle=f"Current platform data. Generated: {now.strftime('%Y-%m-%d %H:%M')}",
        rows=rows,
        filename=f"easyrent_operations_snapshot_{now:%Y%m%d_%H%M}.pdf",
        landscape=True,
    )


@_superuser_required
def report_inventory_pdf(request):
    now = timezone.now()
    units = Unit.objects.select_related("property", "property__landlord").order_by(
        "property__name", "unit_code"
    )
    total = units.count()
    rows = [["Property", "Town", "Landlord", "Unit", "Type", "Monthly rent (KES)", "Unit status", "Active"]]
    for unit in units[:4000]:
        rows.append([
            unit.property.name,
            unit.property.town or "—",
            unit.property.landlord.username,
            unit.unit_code,
            unit.unit_type,
            f"{unit.price:,.2f}",
            unit.get_vacancy_display(),
            "Yes" if unit.is_active else "No",
        ])

    return build_simple_table_pdf(
        title="EasyRent — Property and Unit Inventory",
        subtitle=(
            f"{len(rows) - 1} of {total} units shown"
            f"{' (capped at 4,000)' if total > 4000 else ''}. "
            f"Generated: {now.strftime('%Y-%m-%d %H:%M')}"
        ),
        rows=rows,
        filename=f"easyrent_inventory_{now:%Y%m%d}.pdf",
        landscape=True,
    )


@_superuser_required
def report_booking_queue_pdf(request):
    now = timezone.now()
    cutoff = now - timedelta(days=7)
    pending = Booking.objects.filter(status=Booking.STATUS_PENDING).select_related(
        "tenant", "unit", "unit__property", "unit__property__landlord"
    ).order_by("created_at")
    total = pending.count()
    rows = [["Requested", "Tenant", "Property", "Unit", "Landlord", "Days waiting"]]
    for booking in pending[:4000]:
        wait_days = max((now - booking.created_at).days, 0)
        rows.append([
            booking.created_at.strftime("%Y-%m-%d %H:%M"),
            booking.tenant.username,
            booking.unit.property.name,
            booking.unit.unit_code,
            booking.unit.property.landlord.username,
            f"{wait_days}{' (over 7 days)' if booking.created_at < cutoff else ''}",
        ])

    stale_count = pending.filter(created_at__lt=cutoff).count()
    return build_simple_table_pdf(
        title="EasyRent — Pending Booking Queue",
        subtitle=(
            f"{len(rows) - 1} of {total} pending bookings shown; "
            f"{stale_count} pending over 7 days. Generated: {now.strftime('%Y-%m-%d %H:%M')}"
        ),
        rows=rows,
        filename=f"easyrent_pending_bookings_{now:%Y%m%d}.pdf",
        landscape=True,
    )


@_superuser_required
def report_data_integrity_pdf(request):
    now = timezone.now()
    active_units = Unit.objects.filter(is_active=True)
    active_booking_unit_ids = Booking.objects.filter(
        status__in=[Booking.STATUS_PENDING, Booking.STATUS_CONFIRMED]
    ).values_list("unit_id", flat=True)
    active_tenant_unit_ids = Tenant.objects.filter(is_active=True).values_list(
        "unit_fk_id", flat=True
    )
    booked_without_booking = active_units.filter(vacancy=Unit.BOOKED).exclude(
        id__in=active_booking_unit_ids
    ).select_related("property", "property__landlord").order_by(
        "property__name", "unit_code"
    )
    occupied_without_tenant = active_units.filter(vacancy=Unit.OCCUPIED).exclude(
        id__in=active_tenant_unit_ids
    ).select_related("property", "property__landlord").order_by(
        "property__name", "unit_code"
    )
    negative_balances = Tenant.objects.filter(is_active=True).filter(
        Q(rent_balance__lt=0) | Q(cumulative_rent_balance__lt=0)
    ).select_related("unit_fk", "unit_fk__property", "unit_fk__property__landlord").order_by(
        "cumulative_rent_balance", "rent_balance"
    )
    rows = [
        [
            "Issue",
            "Property / unit",
            "Landlord / tenant",
            "Rent balance (KES)",
            "Cumulative balance (KES)",
        ]
    ]

    for unit in booked_without_booking[:2000]:
        rows.append([
            "Booked without active booking",
            f"{unit.property.name} / {unit.unit_code}",
            unit.property.landlord.username,
            "—",
            "—",
        ])
    for unit in occupied_without_tenant[:2000]:
        rows.append([
            "Occupied without active tenancy",
            f"{unit.property.name} / {unit.unit_code}",
            unit.property.landlord.username,
            "—",
            "—",
        ])
    for tenancy in negative_balances[:2000]:
        rows.append([
            "Negative active tenancy balance",
            f"{tenancy.unit_fk.property.name} / {tenancy.unit_fk.unit_code}",
            tenancy.name,
            f"{tenancy.rent_balance:,.2f}",
            f"{tenancy.cumulative_rent_balance:,.2f}",
        ])

    total = (
        booked_without_booking.count()
        + occupied_without_tenant.count()
        + negative_balances.count()
    )
    return build_simple_table_pdf(
        title="EasyRent — Data Integrity Review",
        subtitle=(
            f"{total} flagged record(s); at most 2,000 per check shown. "
            f"Generated: {now.strftime('%Y-%m-%d %H:%M')}"
        ),
        rows=rows,
        filename=f"easyrent_data_integrity_{now:%Y%m%d}.pdf",
        landscape=True,
    )


@_superuser_required
def report_activity_log_pdf(request):
    now = timezone.now()
    days = parse_range_days(request, default=30)
    scope = (request.GET.get("scope") or "recent").strip().lower()
    events = ManagerAuditEvent.objects.select_related("actor").order_by("-created_at")
    action = (request.GET.get("action") or "all").strip()
    valid_actions = {value for value, _label in ManagerAuditEvent.ACTION_CHOICES}
    if action in valid_actions:
        events = events.filter(action=action)
    else:
        action = "all"
    start_date = parse_date(request.GET.get("from", "").strip())
    end_date = parse_date(request.GET.get("to", "").strip())
    if start_date:
        events = events.filter(created_at__date__gte=start_date)
    if end_date:
        events = events.filter(created_at__date__lte=end_date)
    if not start_date and not end_date and scope != "all":
        events = events.filter(created_at__gte=now - timedelta(days=days))
    search = (request.GET.get("q") or "").strip()
    if search:
        events = events.filter(
            Q(target_username__icontains=search)
            | Q(actor__username__icontains=search)
            | Q(details__icontains=search)
        )
    total = events.count()
    rows = [["Date and time", "Action", "Account", "Role", "Admin", "Details"]]
    for event in events[:4000]:
        rows.append([
            event.created_at.strftime("%Y-%m-%d %H:%M"),
            event.get_action_display(),
            event.target_username,
            event.target_role or "—",
            event.actor.username if event.actor else "Former admin",
            event.details or "—",
        ])

    period = (
        f"{start_date or 'all dates'} to {end_date or 'today'}"
        if start_date or end_date
        else "all dates" if scope == "all" else f"last {days} days"
    )
    return build_simple_table_pdf(
        title=f"EasyRent — Manager Activity ({period})",
        subtitle=(
            f"{len(rows) - 1} of {total} events shown"
            f"{' (capped at 4,000)' if total > 4000 else ''}. "
            f"Generated: {now.strftime('%Y-%m-%d %H:%M')}"
        ),
        rows=rows,
        filename=f"easyrent_activity_{now:%Y%m%d}.pdf",
        landscape=True,
    )