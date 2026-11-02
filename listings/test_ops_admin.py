from datetime import date, timedelta
from decimal import Decimal
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib import admin
from django.conf import settings
from django.db import connections
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from listings.forms import TenantForm
from listings.models import (
    Booking,
    DepositHistory,
    ManagerAuditEvent,
    Notification,
    Property,
    ReceiptHistory,
    RentPayment,
    Tenant,
    Unit,
)


User = get_user_model()


class OpsManagerTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_superuser(
            username="manager",
            email="manager@example.com",
            password=None,
        )
        self.landlord = User.objects.create_user(
            username="landlord",
            email="landlord@example.com",
            password=None,
            role="landlord",
        )
        self.tenant_user = User.objects.create_user(
            username="tenant",
            email="tenant@example.com",
            password=None,
            role="tenant",
        )
        self.property = Property.objects.create(
            name="Manager Test Property",
            town="Nairobi",
            property_type="Single Room",
            price=Decimal("10000.00"),
            landlord=self.landlord,
            landlord_phone="0712345678",
        )
        self.unit = Unit.objects.create(
            property=self.property,
            unit_code="A1",
            unit_type="Single Room",
            price=Decimal("10000.00"),
        )

    def force_admin_login(self, user=None):
        self.client.force_login(user or self.manager)
        self.client.cookies[settings.ADMIN_SESSION_COOKIE_NAME] = self.client.cookies[
            settings.SESSION_COOKIE_NAME
        ].value
        self.client.cookies[settings.ADMIN_SESSION_COOKIE_NAME]["path"] = (
            settings.ADMIN_SESSION_COOKIE_PATH
        )

    def create_tenancy_history(self, booking=None):
        tenancy = Tenant.objects.create(
            name="Test Resident",
            phone="0712345678",
            user=self.tenant_user,
            unit_fk=self.unit,
            booking=booking,
            is_active=True,
            date_occupied=date(2026, 1, 1),
            rent_paid=Decimal("10000.00"),
            date_paid=date(2026, 1, 1),
            month_paid_for="2026-01",
            monthly_rent=Decimal("10000.00"),
        )
        RentPayment.objects.create(
            tenant=tenancy,
            amount=Decimal("10000.00"),
            date_paid=date(2026, 1, 1),
            month_paid_for="2026-01",
            monthly_rent=Decimal("10000.00"),
            rent_balance=Decimal("0.00"),
            cumulative_rent_balance=Decimal("0.00"),
        )
        ReceiptHistory.objects.create(
            tenant=tenancy,
            rent_paid=Decimal("10000.00"),
            date_paid=date(2026, 1, 1),
            month_paid_for="2026-01",
            monthly_rent=Decimal("10000.00"),
            rent_balance=Decimal("0.00"),
            cumulative_rent_balance=Decimal("0.00"),
        )
        DepositHistory.objects.create(
            tenant=self.tenant_user,
            unit=self.unit,
            tenancy=tenancy,
            amount=Decimal("5000.00"),
            date_received=date(2026, 1, 1),
        )
        return tenancy

    def test_manager_can_search_and_review_accounts(self):
        self.force_admin_login()

        response = self.client.get(reverse("ops:users"), {"role": "landlord", "q": "landlord"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "landlord@example.com")
        self.assertNotContains(response, "tenant@example.com")

        detail = self.client.get(reverse("ops:user_detail", args=[self.landlord.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Manager Test Property")

    def test_tenant_form_offers_only_accounts_who_booked_with_this_landlord(self):
        Booking.objects.create(
            unit=self.unit,
            tenant=self.tenant_user,
            status=Booking.STATUS_PENDING,
        )

        form = TenantForm(user=self.landlord)

        self.assertIn("user", form.fields)
        self.assertFalse(form.fields["user"].required)
        self.assertEqual(list(form.fields["user"].queryset), [self.tenant_user])

    def test_account_review_shows_manually_linked_tenancy_activity(self):
        self.create_tenancy_history()
        self.force_admin_login()

        response = self.client.get(reverse("ops:user_detail", args=[self.tenant_user.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Resident")
        self.assertContains(response, "Rent payments")
        self.assertContains(response, "10000.00")

    def test_manager_activity_is_audited_and_filterable(self):
        self.force_admin_login()
        self.client.post(reverse("ops:toggle_user_active", args=[self.tenant_user.pk]))
        self.client.post(reverse("ops:toggle_user_active", args=[self.tenant_user.pk]))

        self.assertEqual(ManagerAuditEvent.objects.count(), 2)
        response = self.client.get(
            reverse("ops:audit_log"),
            {"action": ManagerAuditEvent.ACTION_REACTIVATED, "q": self.tenant_user.username},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manager activity")
        self.assertContains(response, self.tenant_user.username)
        self.assertContains(response, "Account reactivated")
        self.assertEqual(response.context["total_events"], 1)

    def test_native_admin_account_status_changes_are_audited(self):
        user_admin = admin.site._registry[User]
        request = RequestFactory().post("/admin/listings/user/")
        request.user = self.manager

        self.tenant_user.is_active = False
        user_admin.save_model(request, self.tenant_user, form=None, change=True)
        self.tenant_user.is_active = True
        user_admin.save_model(request, self.tenant_user, form=None, change=True)

        self.assertEqual(
            list(
                ManagerAuditEvent.objects.order_by("action").values_list("action", flat=True)
            ),
            [
                ManagerAuditEvent.ACTION_REACTIVATED,
                ManagerAuditEvent.ACTION_SUSPENDED,
            ],
        )

    def test_manager_audit_admin_is_read_only(self):
        self.force_admin_login()
        self.client.post(reverse("ops:toggle_user_active", args=[self.tenant_user.pk]))

        response = self.client.get("/admin/listings/managerauditevent/")

        self.assertEqual(response.status_code, 200)
        audit_admin = admin.site._registry[ManagerAuditEvent]
        request = RequestFactory().get("/admin/listings/managerauditevent/")
        request.user = self.manager
        self.assertFalse(audit_admin.has_add_permission(request))
        self.assertFalse(audit_admin.has_change_permission(request))
        self.assertFalse(audit_admin.has_delete_permission(request))

    def test_dashboard_flags_pending_bookings_older_than_seven_days(self):
        booking = Booking.objects.create(unit=self.unit, tenant=self.tenant_user)
        Booking.objects.filter(pk=booking.pk).update(
            created_at=timezone.now() - timedelta(days=8)
        )
        self.force_admin_login()

        response = self.client.get(reverse("ops:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["cards"]["stale_pending_count"], 1)
        self.assertContains(response, "Bookings pending for more than 7 days")
        self.assertContains(response, "Over 7 days")

    def test_account_deletion_leaves_audit_record(self):
        self.force_admin_login()
        delete_url = reverse("ops:delete_user", args=[self.tenant_user.pk])

        response = self.client.post(
            delete_url,
            {"confirm_username": self.tenant_user.username},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertFalse(User.objects.filter(pk=self.tenant_user.pk).exists())
        event = ManagerAuditEvent.objects.get(action=ManagerAuditEvent.ACTION_DELETED)
        self.assertEqual(event.target_username, "Deleted account")
        self.assertEqual(event.actor, self.manager)
        self.assertContains(response, "Personal account details have been removed")
        self.assertNotContains(response, "tenant")

        response = self.client.get(delete_url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(response, "This account is no longer available.")
        self.assertNotContains(response, self.tenant_user.username)

    def test_admin_home_and_user_admin_are_available_to_manager(self):
        self.force_admin_login()

        self.assertEqual(self.client.get("/admin/").status_code, 200)
        response = self.client.get("/admin/listings/user/add/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "role")

    def test_admin_and_site_logins_keep_independent_sessions(self):
        self.manager.set_password("ManagerPassword-12345")
        self.manager.save(update_fields=["password"])
        self.tenant_user.set_password("TenantPassword-12345")
        self.tenant_user.save(update_fields=["password"])

        admin_login = self.client.post(
            "/admin/login/",
            {"username": self.manager.username, "password": "ManagerPassword-12345"},
        )

        self.assertEqual(admin_login.status_code, 302)
        self.assertIn("admin_sessionid", admin_login.cookies)
        self.assertEqual(admin_login.cookies["admin_sessionid"]["path"], "/admin/")
        self.assertNotIn("sessionid", admin_login.cookies)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

        site_login = self.client.post(
            reverse("login"),
            {"username": self.tenant_user.username, "password": "TenantPassword-12345"},
        )

        self.assertEqual(site_login.status_code, 302)
        self.assertIn("sessionid", site_login.cookies)
        self.assertNotIn("admin_sessionid", site_login.cookies)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        tenant_page = self.client.get(reverse("tenant_feed"))
        self.assertEqual(tenant_page.status_code, 200)
        self.assertEqual(tenant_page.context["user"], self.tenant_user)

        self.client.post("/admin/logout/")
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        tenant_page = self.client.get(reverse("tenant_feed"))
        self.assertEqual(tenant_page.context["user"], self.tenant_user)

        admin_login = self.client.post(
            "/admin/login/",
            {"username": self.manager.username, "password": "ManagerPassword-12345"},
        )

        self.assertEqual(admin_login.status_code, 302)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        tenant_page = self.client.get(reverse("tenant_feed"))
        self.assertEqual(tenant_page.context["user"], self.tenant_user)

    def test_site_login_does_not_invalidate_csrf_for_admin_reactivation(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        client.cookies[settings.ADMIN_SESSION_COOKIE_NAME] = client.cookies[
            settings.SESSION_COOKIE_NAME
        ].value
        client.cookies[settings.ADMIN_SESSION_COOKIE_NAME]["path"] = (
            settings.ADMIN_SESSION_COOKIE_PATH
        )
        self.tenant_user.is_active = False
        self.tenant_user.save(update_fields=["is_active"])

        admin_page = client.get(reverse("ops:user_detail", args=[self.tenant_user.pk]))
        admin_token_match = re.search(
            rb'name="csrfmiddlewaretoken" value="([^"]+)"',
            admin_page.content,
        )
        self.assertIsNotNone(admin_token_match)
        self.assertIn(settings.ADMIN_CSRF_COOKIE_NAME, admin_page.cookies)

        client.cookies.pop(settings.SESSION_COOKIE_NAME, None)
        self.landlord.set_password("LandlordPassword-12345")
        self.landlord.save(update_fields=["password"])
        login_page = client.get(reverse("login"))
        site_token_match = re.search(
            rb'name="csrfmiddlewaretoken" value="([^"]+)"',
            login_page.content,
        )
        self.assertIsNotNone(site_token_match)
        login_response = client.post(
            reverse("login"),
            {
                "username": self.landlord.username,
                "password": "LandlordPassword-12345",
                "csrfmiddlewaretoken": site_token_match.group(1).decode(),
            },
        )
        self.assertEqual(login_response.status_code, 302)

        response = client.post(
            reverse("ops:toggle_user_active", args=[self.tenant_user.pk]),
            {"csrfmiddlewaretoken": admin_token_match.group(1).decode()},
        )

        self.assertRedirects(
            response,
            reverse("ops:user_detail", args=[self.tenant_user.pk]),
        )
        self.tenant_user.refresh_from_db()
        self.assertTrue(self.tenant_user.is_active)

    def test_dashboard_excludes_admins_from_role_totals_and_clarifies_metrics(self):
        self.create_tenancy_history()
        self.force_admin_login()

        response = self.client.get(reverse("ops:dashboard"))

        self.assertEqual(response.status_code, 200)
        cards = response.context["cards"]
        self.assertEqual(cards["total_landlords"], 1)
        self.assertEqual(cards["total_tenants"], 1)
        self.assertEqual(cards["admin_accounts"], 1)
        self.assertEqual(cards["active_tenancy_records"], 1)
        self.assertEqual(cards["occupied_units"], 0)
        self.assertContains(response, "Recorded occupancy rate")
        self.assertContains(response, "Occupancy uses each active unit’s status, not tenancy records.")
        self.assertContains(response, "Payments recorded for current rent month")
        self.assertContains(response, "Deposits received this month to date")

    def test_dashboard_links_relevant_pdf_exports(self):
        self.force_admin_login()

        response = self.client.get(reverse("ops:dashboard"))

        self.assertEqual(response.status_code, 200)
        for report_name in (
            "operations_snapshot_pdf",
            "inventory_pdf",
            "booking_queue_pdf",
            "data_integrity_pdf",
            "activity_log_pdf",
            "market_overview_pdf",
            "receipts_ledger_pdf",
            "deposits_ledger_pdf",
            "user_registry_pdf",
        ):
            self.assertContains(response, reverse(f"ops:{report_name}"))

    def test_manager_report_endpoints_return_pdf_attachments(self):
        self.force_admin_login()
        report_names = (
            "operations_snapshot_pdf",
            "inventory_pdf",
            "booking_queue_pdf",
            "data_integrity_pdf",
            "activity_log_pdf",
        )

        for report_name in report_names:
            with self.subTest(report=report_name):
                response = self.client.get(reverse(f"ops:{report_name}"))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/pdf")
                self.assertIn("attachment; filename=", response["Content-Disposition"])
                self.assertTrue(response.content.startswith(b"%PDF"))

    def test_manager_report_endpoints_reject_staff_without_superuser_status(self):
        staff_user = User.objects.create_user(username="report-staff", is_staff=True)
        self.force_admin_login(staff_user)

        for report_name in (
            "operations_snapshot_pdf",
            "inventory_pdf",
            "booking_queue_pdf",
            "data_integrity_pdf",
            "activity_log_pdf",
        ):
            with self.subTest(report=report_name):
                response = self.client.get(reverse(f"ops:{report_name}"))
                self.assertEqual(response.status_code, 403)

    def test_user_registry_can_export_all_roles_and_suspended_users(self):
        self.tenant_user.is_active = False
        self.tenant_user.save(update_fields=["is_active"])
        self.force_admin_login()

        with patch(
            "listings.ops_admin.views.build_simple_table_pdf",
            return_value=HttpResponse(b"%PDF", content_type="application/pdf"),
        ) as build_pdf:
            response = self.client.get(
                reverse("ops:user_registry_pdf"),
                {"scope": "all", "role": "all"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(build_pdf.call_args.kwargs["filename"], "user_registry_all_all.pdf")
        self.assertIn(
            [
                self.tenant_user.username,
                self.tenant_user.email,
                "tenant",
                "Suspended",
                self.tenant_user.date_joined.strftime("%Y-%m-%d %H:%M"),
                "—",
            ],
            build_pdf.call_args.kwargs["rows"],
        )

    def test_inventory_and_booking_exports_include_record_details(self):
        booking = Booking.objects.create(unit=self.unit, tenant=self.tenant_user)
        self.force_admin_login()

        with patch(
            "listings.ops_admin.views.build_simple_table_pdf",
            return_value=HttpResponse(b"%PDF", content_type="application/pdf"),
        ) as build_pdf:
            inventory_response = self.client.get(reverse("ops:inventory_pdf"))
        self.assertEqual(inventory_response.status_code, 200)
        self.assertIn(
            [
                self.property.name,
                self.property.town,
                self.landlord.username,
                self.unit.unit_code,
                self.unit.unit_type,
                "10,000.00",
                "Vacant",
                "Yes",
            ],
            build_pdf.call_args.kwargs["rows"],
        )

        with patch(
            "listings.ops_admin.views.build_simple_table_pdf",
            return_value=HttpResponse(b"%PDF", content_type="application/pdf"),
        ) as build_pdf:
            booking_response = self.client.get(reverse("ops:booking_queue_pdf"))
        self.assertEqual(booking_response.status_code, 200)
        self.assertIn(
            [
                booking.created_at.strftime("%Y-%m-%d %H:%M"),
                self.tenant_user.username,
                self.property.name,
                self.unit.unit_code,
                self.landlord.username,
                "0",
            ],
            build_pdf.call_args.kwargs["rows"],
        )

    def test_integrity_export_includes_dashboard_flag_categories(self):
        self.unit.vacancy = Unit.BOOKED
        self.unit.save(update_fields=["vacancy"])
        occupied_unit = Unit.objects.create(
            property=self.property,
            unit_code="A2",
            unit_type="Single Room",
            price=Decimal("12000.00"),
            vacancy=Unit.OCCUPIED,
        )
        tenancy = self.create_tenancy_history()
        tenancy.cumulative_rent_balance = Decimal("-100.00")
        tenancy.save(update_fields=["cumulative_rent_balance"])
        self.force_admin_login()

        with patch(
            "listings.ops_admin.views.build_simple_table_pdf",
            return_value=HttpResponse(b"%PDF", content_type="application/pdf"),
        ) as build_pdf:
            response = self.client.get(reverse("ops:data_integrity_pdf"))

        rows = build_pdf.call_args.kwargs["rows"]
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            ["Booked without active booking", "Manager Test Property / A1", "landlord", "—", "—"],
            rows,
        )
        self.assertIn(
            ["Occupied without active tenancy", "Manager Test Property / A2", "landlord", "—", "—"],
            rows,
        )
        self.assertIn(
            [
                "Negative active tenancy balance",
                "Manager Test Property / A1",
                "Test Resident",
                "0.00",
                "-100.00",
            ],
            rows,
        )
        self.assertTrue(Unit.objects.filter(pk=occupied_unit.pk).exists())

    def test_snapshot_and_activity_exports_reflect_selected_data(self):
        old_event = ManagerAuditEvent.objects.create(
            actor=self.manager,
            action=ManagerAuditEvent.ACTION_SUSPENDED,
            target_username="older-account",
            target_role="tenant",
            details="Suspended by manager",
        )
        old_timestamp = timezone.now() - timedelta(days=60)
        ManagerAuditEvent.objects.filter(pk=old_event.pk).update(created_at=old_timestamp)
        self.force_admin_login()

        with patch(
            "listings.ops_admin.views.build_simple_table_pdf",
            return_value=HttpResponse(b"%PDF", content_type="application/pdf"),
        ) as build_pdf:
            snapshot_response = self.client.get(reverse("ops:operations_snapshot_pdf"))
        self.assertEqual(snapshot_response.status_code, 200)
        self.assertIn(
            ["Inventory", "Active units", "1"],
            build_pdf.call_args.kwargs["rows"],
        )
        self.assertIn(
            ["Rent and deposits", "Potential monthly rent (KES)", "10,000.00"],
            build_pdf.call_args.kwargs["rows"],
        )

        with patch(
            "listings.ops_admin.views.build_simple_table_pdf",
            return_value=HttpResponse(b"%PDF", content_type="application/pdf"),
        ) as build_pdf:
            activity_response = self.client.get(
                reverse("ops:activity_log_pdf"),
                {
                    "action": ManagerAuditEvent.ACTION_SUSPENDED,
                    "q": "older-account",
                    "from": old_timestamp.date().isoformat(),
                    "to": old_timestamp.date().isoformat(),
                },
            )
        self.assertEqual(activity_response.status_code, 200)
        self.assertIn(
            [
                old_timestamp.strftime("%Y-%m-%d %H:%M"),
                "Account suspended",
                "older-account",
                "tenant",
                "manager",
                "Suspended by manager",
            ],
            build_pdf.call_args.kwargs["rows"],
        )

    def test_manager_can_create_an_account_with_selected_role(self):
        self.force_admin_login()

        response = self.client.post(
            "/admin/listings/user/add/",
            {
                "username": "new-landlord",
                "email": "new-landlord@example.com",
                "first_name": "New",
                "last_name": "Landlord",
                "role": "landlord",
                "password1": "StrongPassword-12345",
                "password2": "StrongPassword-12345",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(username="new-landlord").role, "landlord")

    def test_staff_without_superuser_status_cannot_manage_users(self):
        staff_user = User.objects.create_user(
            username="staff",
            password=None,
            is_staff=True,
        )
        self.force_admin_login(staff_user)

        response = self.client.get(reverse("ops:users"))

        self.assertEqual(response.status_code, 403)

    def test_manager_can_suspend_and_reactivate_accounts(self):
        self.force_admin_login()
        url = reverse("ops:toggle_user_active", args=[self.tenant_user.pk])

        response = self.client.post(url)

        self.assertRedirects(response, reverse("ops:user_detail", args=[self.tenant_user.pk]))
        self.tenant_user.refresh_from_db()
        self.assertFalse(self.tenant_user.is_active)

        self.client.post(url)
        self.tenant_user.refresh_from_db()
        self.assertTrue(self.tenant_user.is_active)

    def test_user_deletion_requires_exact_confirmation(self):
        self.force_admin_login()
        response = self.client.post(
            reverse("ops:delete_user", args=[self.tenant_user.pk]),
            {"confirm_username": "not-the-username"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=self.tenant_user.pk).exists())

    def test_deleting_tenant_removes_linked_records_and_releases_unit(self):
        booking = Booking.objects.create(
            unit=self.unit,
            tenant=self.tenant_user,
            status=Booking.STATUS_MOVED_IN,
        )
        tenancy = self.create_tenancy_history(booking)
        self.unit.vacancy = Unit.OCCUPIED
        self.unit.save(update_fields=["vacancy"])
        self.force_admin_login()

        response = self.client.post(
            reverse("ops:delete_user", args=[self.tenant_user.pk]),
            {"confirm_username": self.tenant_user.username},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(pk=self.tenant_user.pk).exists())
        self.assertFalse(Booking.objects.filter(pk=booking.pk).exists())
        self.assertFalse(Tenant.objects.filter(pk=tenancy.pk).exists())
        self.assertFalse(RentPayment.objects.exists())
        self.assertFalse(ReceiptHistory.objects.exists())
        self.assertFalse(DepositHistory.objects.exists())
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.vacancy, Unit.VACANT)

    def test_deleting_tenant_removes_manually_linked_tenancy_without_booking(self):
        tenancy = self.create_tenancy_history()
        self.force_admin_login()

        response = self.client.post(
            reverse("ops:delete_user", args=[self.tenant_user.pk]),
            {"confirm_username": self.tenant_user.username},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Tenant.objects.filter(pk=tenancy.pk).exists())
        self.assertFalse(RentPayment.objects.exists())

    def test_deleting_account_anonymizes_audit_history_and_removes_its_notifications(self):
        ManagerAuditEvent.objects.create(
            actor=self.manager,
            action=ManagerAuditEvent.ACTION_SUSPENDED,
            target_username=self.tenant_user.username,
            target_role=self.tenant_user.role,
            details="Tenant account suspended.",
        )
        sent_notification = Notification.objects.create(
            recipient=self.landlord,
            actor=self.tenant_user,
            message="Booking cancelled by tenant: Test Resident.",
        )
        received_notification = Notification.objects.create(
            recipient=self.tenant_user,
            actor=self.manager,
            message="Your account was reviewed.",
        )
        unrelated_notification = Notification.objects.create(
            recipient=self.landlord,
            actor=self.manager,
            message="Unrelated account update.",
        )
        self.force_admin_login()

        response = self.client.post(
            reverse("ops:delete_user", args=[self.tenant_user.pk]),
            {"confirm_username": self.tenant_user.username},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(pk=self.tenant_user.pk).exists())
        self.assertFalse(Notification.objects.filter(pk=sent_notification.pk).exists())
        self.assertFalse(Notification.objects.filter(pk=received_notification.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=unrelated_notification.pk).exists())
        self.assertFalse(
            ManagerAuditEvent.objects.filter(target_username=self.tenant_user.username).exists()
        )
        self.assertEqual(
            ManagerAuditEvent.objects.filter(target_username="Deleted account").count(),
            2,
        )
    def test_deleting_landlord_removes_owned_inventory_and_financial_history(self):
        booking = Booking.objects.create(
            unit=self.unit,
            tenant=self.tenant_user,
            status=Booking.STATUS_MOVED_IN,
        )
        tenancy = self.create_tenancy_history(booking)
        self.force_admin_login()

        response = self.client.post(
            reverse("ops:delete_user", args=[self.landlord.pk]),
            {"confirm_username": self.landlord.username},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(pk=self.landlord.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.tenant_user.pk).exists())
        self.assertFalse(Property.objects.filter(pk=self.property.pk).exists())
        self.assertFalse(Unit.objects.filter(pk=self.unit.pk).exists())
        self.assertFalse(Tenant.objects.filter(pk=tenancy.pk).exists())
        self.assertFalse(RentPayment.objects.exists())
        self.assertFalse(ReceiptHistory.objects.exists())
        self.assertFalse(DepositHistory.objects.exists())

    def test_manager_and_superuser_accounts_are_protected_from_deletion(self):
        self.force_admin_login()

        response = self.client.post(
            reverse("ops:delete_user", args=[self.manager.pk]),
            {"confirm_username": self.manager.username},
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.manager.pk).exists())

    @patch("listings.ops_admin.views.call_command")
    def test_full_reset_requires_both_confirmation_fields(self, call_command):
        self.force_admin_login()

        response = self.client.post(
            reverse("ops:reset_data"),
            {"confirm_database": "wrong", "confirm_action": "DELETE ALL DATA"},
        )

        self.assertEqual(response.status_code, 400)
        call_command.assert_not_called()
        self.assertTrue(User.objects.filter(pk=self.tenant_user.pk).exists())

    @patch("listings.ops_admin.views.call_command")
    def test_full_reset_runs_only_after_both_exact_confirmations(self, call_command):
        self.force_admin_login()
        database_name = connections["default"].settings_dict["NAME"]

        response = self.client.post(
            reverse("ops:reset_data"),
            {
                "confirm_database": database_name,
                "confirm_action": "DELETE ALL DATA",
            },
        )

        self.assertEqual(response.status_code, 200)
        call_command.assert_called_once_with(
            "flush",
            database="default",
            interactive=False,
            verbosity=0,
        )
