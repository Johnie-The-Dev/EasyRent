from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse

from .forms import TenantForm
from .models import (
	Booking,
	DepositHistory,
	Property,
	PropertyImage,
	RentPayment,
	ReceiptHistory,
	Tenant,
	TenantCollection,
	Unit,
)


class LandlordWorkflowSecurityTests(TestCase):
	def setUp(self):
		user_model = get_user_model()
		self.landlord = user_model.objects.create_user(
			username="landlord",
			password="test-password",
			role="landlord",
		)
		self.tenant_user = user_model.objects.create_user(
			username="tenant",
			password="test-password",
			role="tenant",
		)
		self.property = Property.objects.create(
			name="Test Property",
			town="Nairobi",
			property_type="Single Room",
			price=Decimal("10000.00"),
			landlord=self.landlord,
			landlord_phone="+254712345678",
		)
		self.unit = Unit.objects.create(
			property=self.property,
			unit_code="A1",
			unit_type="Single Room",
			price=Decimal("10000.00"),
		)
		self.tenant = Tenant.objects.create(
			name="Test Resident",
			phone="0712345678",
			unit_fk=self.unit,
			date_occupied=date(2026, 1, 1),
			rent_paid=Decimal("0.00"),
			date_paid=date(2026, 1, 1),
			month_paid_for="2026-01",
			monthly_rent=Decimal("10000.00"),
		)
		self.unit.vacancy = Unit.OCCUPIED
		self.unit.save(update_fields=["vacancy"])

	def create_booked_unit(self, status=Booking.STATUS_CONFIRMED, tenant=None):
		unit = Unit.objects.create(
			property=self.property,
			unit_code="B1",
			unit_type="Single Room",
			price=Decimal("12000.00"),
			vacancy=Unit.BOOKED,
		)
		booking = Booking.objects.create(
			unit=unit,
			tenant=tenant or self.tenant_user,
			status=status,
		)
		return unit, booking

	def test_tenant_cannot_open_landlord_dashboard(self):
		self.client.force_login(self.tenant_user)

		response = self.client.get(reverse("landlord_dashboard"))

		self.assertEqual(response.status_code, 403)

	def test_tenant_feed_hides_tenant_actions_from_landlord(self):
		market_property = Property.objects.create(
			name="Available Listing",
			town="Nairobi",
			property_type="Single Room",
			price=Decimal("9000.00"),
			landlord=self.landlord,
			landlord_phone="+254712345678",
		)
		Unit.objects.create(
			property=market_property,
			unit_code="B1",
			unit_type="Single Room",
			price=Decimal("9000.00"),
		)
		self.client.force_login(self.landlord)

		response = self.client.get(reverse("tenant_feed"))

		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, "My Collection")
		self.assertNotContains(response, "Request Unit")
		self.assertNotContains(response, "Save / Hifadhi")

	def test_landlord_dashboard_renders_owned_property(self):
		self.client.force_login(self.landlord)

		response = self.client.get(reverse("landlord_dashboard"))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, self.property.name)
		self.assertContains(response, "recent payment(s)")

	def test_tenant_forms_render_without_duplicate_or_misleading_controls(self):
		self.client.force_login(self.landlord)

		create_response = self.client.get(reverse("manage_tenants"))
		edit_response = self.client.get(reverse("edit_tenant", args=[self.tenant.id]))

		self.assertEqual(create_response.status_code, 200)
		self.assertNotContains(create_response, "Rent Paid (KES)")
		self.assertContains(create_response, "Payment Received Now (KES)")
		self.assertEqual(edit_response.status_code, 200)
		self.assertContains(edit_response, "Payment Received Now (KES)")
		self.assertNotContains(edit_response, "Balance (preview)")

	def test_tenant_feed_shows_tenant_actions_to_tenant(self):
		market_property = Property.objects.create(
			name="Available Listing",
			town="Nairobi",
			property_type="Single Room",
			price=Decimal("9000.00"),
			landlord=self.landlord,
			landlord_phone="+254712345678",
		)
		Unit.objects.create(
			property=market_property,
			unit_code="B1",
			unit_type="Single Room",
			price=Decimal("9000.00"),
		)
		self.client.force_login(self.tenant_user)

		response = self.client.get(reverse("tenant_feed"))

		self.assertContains(response, "My Collection")
		self.assertContains(response, "Request Unit")
		self.assertContains(response, "Save / Hifadhi")

	def test_new_tenant_form_has_one_payment_entry_and_edit_total_is_read_only(self):
		new_tenant_form = TenantForm(user=self.landlord)
		edit_tenant_form = TenantForm(instance=self.tenant, user=self.landlord)

		self.assertNotIn("rent_paid", new_tenant_form.fields)
		self.assertIn("payment_amount", new_tenant_form.fields)
		self.assertTrue(edit_tenant_form.fields["rent_paid"].disabled)

	def test_get_request_does_not_create_booking(self):
		self.client.force_login(self.tenant_user)

		response = self.client.get(reverse("book_property", args=[self.property.id]))

		self.assertEqual(response.status_code, 405)
		self.assertFalse(Booking.objects.exists())
		self.unit.refresh_from_db()
		self.assertEqual(self.unit.vacancy, Unit.OCCUPIED)

	def test_tenant_cannot_book_two_units_in_one_property(self):
		first_vacant = Unit.objects.create(
			property=self.property,
			unit_code="A2",
			unit_type="Single Room",
			price=Decimal("10000.00"),
		)
		second_vacant = Unit.objects.create(
			property=self.property,
			unit_code="A3",
			unit_type="Single Room",
			price=Decimal("10000.00"),
		)
		self.client.force_login(self.tenant_user)

		first_response = self.client.post(reverse("book_property", args=[self.property.id]))
		second_response = self.client.post(reverse("book_property", args=[self.property.id]))

		self.assertRedirects(first_response, reverse("tenant_feed"))
		self.assertRedirects(second_response, reverse("tenant_feed"))
		self.assertEqual(Booking.objects.filter(tenant=self.tenant_user).count(), 1)
		first_vacant.refresh_from_db()
		second_vacant.refresh_from_db()
		self.assertEqual(first_vacant.vacancy, Unit.BOOKED)
		self.assertEqual(second_vacant.vacancy, Unit.VACANT)

	def test_cancelled_booking_cannot_be_reactivated(self):
		booking = Booking.objects.create(
			unit=self.unit,
			tenant=self.tenant_user,
			status=Booking.STATUS_CANCELLED,
		)
		self.client.force_login(self.landlord)

		response = self.client.post(
			reverse("update_booking_status", args=[booking.id]),
			{"status": Booking.STATUS_CONFIRMED},
		)

		self.assertRedirects(response, reverse("landlord_bookings"))
		booking.refresh_from_db()
		self.assertEqual(booking.status, Booking.STATUS_CANCELLED)

	def test_booking_confirmation_rejected_for_occupied_unit(self):
		booking = Booking.objects.create(
			unit=self.unit,
			tenant=self.tenant_user,
			status=Booking.STATUS_PENDING,
		)
		self.client.force_login(self.landlord)

		self.client.post(
			reverse("update_booking_status", args=[booking.id]),
			{"status": Booking.STATUS_CONFIRMED},
		)

		booking.refresh_from_db()
		self.unit.refresh_from_db()
		self.assertEqual(booking.status, Booking.STATUS_PENDING)
		self.assertEqual(self.unit.vacancy, Unit.OCCUPIED)

	def test_feed_and_collection_exclude_active_tenancy_from_availability(self):
		TenantCollection.objects.create(tenant=self.tenant_user, property=self.property)
		self.unit.vacancy = Unit.VACANT
		self.unit.save(update_fields=["vacancy"])
		self.client.force_login(self.tenant_user)

		feed_response = self.client.get(reverse("tenant_feed"))
		collection_response = self.client.get(reverse("view_collection"))

		self.assertNotIn(self.property, feed_response.context["properties"])
		self.assertEqual(collection_response.context["collection"][0].vacant_units_filtered, 0)

	def test_remove_collection_does_not_redirect_off_site(self):
		TenantCollection.objects.create(tenant=self.tenant_user, property=self.property)
		self.client.force_login(self.tenant_user)

		response = self.client.post(
			reverse("remove_from_collection", args=[self.property.id]) + "?next=https://evil.example",
		)

		self.assertRedirects(response, reverse("tenant_feed"))
		self.assertFalse(TenantCollection.objects.filter(tenant=self.tenant_user).exists())

	def test_remove_collection_returns_to_safe_collection_path(self):
		TenantCollection.objects.create(tenant=self.tenant_user, property=self.property)
		self.client.force_login(self.tenant_user)

		response = self.client.post(
			reverse("remove_from_collection", args=[self.property.id]),
			{"next": reverse("view_collection")},
		)

		self.assertRedirects(response, reverse("view_collection"))

	def test_get_request_does_not_delete_tenant(self):
		self.client.force_login(self.landlord)

		response = self.client.get(reverse("delete_tenant", args=[self.tenant.id]))

		self.assertEqual(response.status_code, 405)
		self.assertTrue(Tenant.objects.filter(id=self.tenant.id).exists())

	def test_end_tenancy_archives_tenant_and_releases_unit(self):
		self.client.force_login(self.landlord)

		response = self.client.post(reverse("delete_tenant", args=[self.tenant.id]))

		self.assertRedirects(response, reverse("tenant_list"))
		self.tenant.refresh_from_db()
		self.unit.refresh_from_db()
		self.assertFalse(self.tenant.is_active)
		self.assertEqual(self.unit.vacancy, Unit.VACANT)

	def test_confirmed_booking_converts_to_linked_tenancy(self):
		unit, booking = self.create_booked_unit()
		competing_user = get_user_model().objects.create_user(
			username="competing-tenant",
			password="test-password",
			role="tenant",
		)
		competing_booking = Booking.objects.create(
			unit=unit,
			tenant=competing_user,
			status=Booking.STATUS_PENDING,
		)
		self.client.force_login(self.landlord)
		conversion_url = f"{reverse('manage_tenants')}?booking={booking.id}"

		get_response = self.client.get(conversion_url)
		self.assertEqual(get_response.status_code, 200)
		self.assertContains(get_response, "Create Tenancy")
		self.assertContains(get_response, self.tenant_user.username)
		self.assertContains(get_response, "Confirm Move-in")

		response = self.client.post(
			conversion_url,
			{
				"booking_id": booking.id,
				"name": self.tenant_user.username,
				"phone": "0712345678",
				"date_occupied": "2026-10-02",
				"date_paid": "2026-10-02",
				"month_paid_for": "2026-10",
				"monthly_rent": "12000.00",
				"payment_amount": "0",
				"deposit_amount": "5000.00",
				"deposit_date": "2026-10-02",
			},
		)

		self.assertRedirects(response, reverse("tenant_list"))
		booking.refresh_from_db()
		competing_booking.refresh_from_db()
		unit.refresh_from_db()
		tenancy = Tenant.objects.get(booking=booking)
		deposit = DepositHistory.objects.get(tenancy=tenancy)
		self.assertEqual(booking.status, Booking.STATUS_MOVED_IN)
		self.assertEqual(competing_booking.status, Booking.STATUS_CANCELLED)
		self.assertEqual(tenancy.unit_fk_id, unit.id)
		self.assertEqual(unit.vacancy, Unit.OCCUPIED)
		self.assertEqual(deposit.tenant_id, self.tenant_user.id)

		self.client.force_login(self.tenant_user)
		tenant_history_response = self.client.get(reverse("tenant_bookings"))
		self.assertContains(tenant_history_response, "Moved in")
		self.assertNotContains(tenant_history_response, "Cancel Booking")
		self.assertEqual(tenant_history_response.context["moved_in_count"], 1)
		cancel_response = self.client.post(reverse("unbook_property", args=[booking.id]))
		self.assertRedirects(cancel_response, reverse("tenant_bookings"))
		booking.refresh_from_db()
		self.assertEqual(booking.status, Booking.STATUS_MOVED_IN)

		self.client.force_login(self.landlord)
		landlord_history_response = self.client.get(reverse("landlord_bookings"))
		self.assertContains(landlord_history_response, "Moved in")
		self.assertContains(landlord_history_response, "View tenant")
		self.assertEqual(landlord_history_response.context["moved_in_count"], 1)
		blocked_transition = self.client.post(
			reverse("update_booking_status", args=[booking.id]),
			{"status": Booking.STATUS_CANCELLED},
		)
		self.assertRedirects(blocked_transition, reverse("landlord_bookings"))
		booking.refresh_from_db()
		self.assertEqual(booking.status, Booking.STATUS_MOVED_IN)

	def test_manual_tenant_entry_cannot_override_confirmed_booking(self):
		unit, booking = self.create_booked_unit()
		self.client.force_login(self.landlord)

		response = self.client.post(
			reverse("manage_tenants"),
			{
				"name": "Walk-in Resident",
				"phone": "0712345678",
				"unit_fk": unit.id,
				"date_occupied": "2026-10-02",
				"date_paid": "2026-10-02",
				"month_paid_for": "2026-10",
				"monthly_rent": "12000.00",
				"payment_amount": "0",
			},
		)

		self.assertEqual(response.status_code, 200)
		self.assertFormError(
			response.context["form"],
			"unit_fk",
			"This unit has a confirmed booking. Use Create tenancy from the Bookings page, or cancel the booking first.",
		)
		booking.refresh_from_db()
		unit.refresh_from_db()
		self.assertEqual(booking.status, Booking.STATUS_CONFIRMED)
		self.assertEqual(unit.vacancy, Unit.BOOKED)
		self.assertFalse(Tenant.objects.filter(unit_fk=unit, is_active=True).exists())

	def test_pending_booking_cannot_be_converted(self):
		_unit, booking = self.create_booked_unit(status=Booking.STATUS_PENDING)
		self.client.force_login(self.landlord)

		response = self.client.get(f"{reverse('manage_tenants')}?booking={booking.id}")

		self.assertRedirects(response, reverse("landlord_bookings"))
		self.assertFalse(Tenant.objects.filter(booking=booking).exists())

	def test_other_landlord_cannot_open_booking_conversion(self):
		_unit, booking = self.create_booked_unit()
		other_landlord = get_user_model().objects.create_user(
			username="other-landlord",
			password="test-password",
			role="landlord",
		)
		self.client.force_login(other_landlord)

		response = self.client.get(f"{reverse('manage_tenants')}?booking={booking.id}")

		self.assertEqual(response.status_code, 404)

	def test_property_with_tenancy_history_cannot_be_deleted(self):
		self.client.force_login(self.landlord)

		response = self.client.post(reverse("delete_property", args=[self.property.id]))

		self.assertRedirects(response, reverse("landlord_dashboard"))
		self.assertTrue(Property.objects.filter(id=self.property.id).exists())
		self.assertTrue(Tenant.objects.filter(id=self.tenant.id).exists())

	def test_edit_property_rejects_deleting_unit_with_active_tenant(self):
		self.client.force_login(self.landlord)
		page = self.client.get(reverse("edit_property", args=[self.property.id]))
		unit_formset = page.context["unit_formset"]
		prefix = unit_formset.prefix
		data = {
			"name": self.property.name,
			"county": "",
			"town": self.property.town,
			"property_type": self.property.property_type,
			"price": str(self.property.price),
			"landlord_phone": self.property.landlord_phone,
			"description": "",
			f"{prefix}-TOTAL_FORMS": str(unit_formset.total_form_count()),
			f"{prefix}-INITIAL_FORMS": str(unit_formset.initial_form_count()),
			f"{prefix}-MIN_NUM_FORMS": "0",
			f"{prefix}-MAX_NUM_FORMS": "1000",
			f"{prefix}-0-id": str(self.unit.id),
			f"{prefix}-0-unit_code": self.unit.unit_code,
			f"{prefix}-0-unit_type": self.unit.unit_type,
			f"{prefix}-0-price": str(self.unit.price),
			f"{prefix}-0-deposit": str(self.unit.deposit),
			f"{prefix}-0-vacancy": Unit.OCCUPIED,
			f"{prefix}-0-is_active": "on",
			f"{prefix}-0-DELETE": "on",
		}

		response = self.client.post(reverse("edit_property", args=[self.property.id]), data)

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Cannot delete unit(s) A1")
		self.assertTrue(Unit.objects.filter(id=self.unit.id).exists())
		self.assertTrue(Tenant.objects.filter(id=self.tenant.id, is_active=True).exists())

	def test_add_property_creates_property_and_first_unit(self):
		self.client.force_login(self.landlord)
		page = self.client.get(reverse("add_property"))
		formset = page.context["formset"]
		prefix = formset.prefix
		data = {
			"name": "New Rental",
			"county": "Nairobi",
			"town": "Westlands",
			"property_type": "Single Room",
			"price": "9000.00",
			"landlord_phone": "+254712345678",
			"description": "",
			f"{prefix}-TOTAL_FORMS": str(formset.total_form_count()),
			f"{prefix}-INITIAL_FORMS": "0",
			f"{prefix}-MIN_NUM_FORMS": "0",
			f"{prefix}-MAX_NUM_FORMS": "1000",
			f"{prefix}-0-unit_code": "N1",
			f"{prefix}-0-unit_type": "Single Room",
			f"{prefix}-0-price": "9000.00",
			f"{prefix}-0-deposit": "0.00",
			f"{prefix}-0-vacancy": Unit.VACANT,
		}

		response = self.client.post(reverse("add_property"), data)

		self.assertRedirects(response, reverse("landlord_dashboard"))
		new_property = Property.objects.get(name="New Rental")
		self.assertEqual(new_property.units.count(), 1)
		self.assertEqual(new_property.units.get().unit_code, "N1")

	def test_add_property_inherits_property_defaults_for_unit(self):
		self.client.force_login(self.landlord)
		page = self.client.get(reverse("add_property"))
		formset = page.context["formset"]
		prefix = formset.prefix
		data = {
			"name": "Defaulted Rental",
			"county": "Nairobi",
			"town": "Kilimani",
			"property_type": "Bed Sitter",
			"price": "15000.00",
			"landlord_phone": "+254712345678",
			"description": "",
			f"{prefix}-TOTAL_FORMS": str(formset.total_form_count()),
			f"{prefix}-INITIAL_FORMS": "0",
			f"{prefix}-MIN_NUM_FORMS": "0",
			f"{prefix}-MAX_NUM_FORMS": "1000",
			f"{prefix}-0-unit_code": "D1",
			f"{prefix}-0-unit_type": "",
			f"{prefix}-0-price": "",
			f"{prefix}-0-deposit": "0.00",
			f"{prefix}-0-vacancy": "",
		}

		response = self.client.post(reverse("add_property"), data)

		self.assertRedirects(response, reverse("landlord_dashboard"))
		unit = Property.objects.get(name="Defaulted Rental").units.get()
		self.assertEqual(unit.unit_type, "Bed Sitter")
		self.assertEqual(unit.price, Decimal("15000.00"))
		self.assertEqual(unit.vacancy, Unit.VACANT)
		self.assertTrue(unit.is_active)

	def test_edit_property_updates_unit_and_property(self):
		self.client.force_login(self.landlord)
		PropertyImage.objects.create(
			property=self.property,
			image="property_images/test.jpg",
			is_primary=True,
		)
		page = self.client.get(reverse("edit_property", args=[self.property.id]))
		formset = page.context["unit_formset"]
		prefix = formset.prefix
		data = {
			"name": "Renamed Property",
			"county": "Nairobi",
			"town": "Westlands",
			"property_type": "Single Room",
			"price": "11000.00",
			"landlord_phone": "+254712345678",
			"description": "Updated description",
			f"{prefix}-TOTAL_FORMS": str(formset.total_form_count()),
			f"{prefix}-INITIAL_FORMS": str(formset.initial_form_count()),
			f"{prefix}-MIN_NUM_FORMS": "0",
			f"{prefix}-MAX_NUM_FORMS": "1000",
			f"{prefix}-0-id": str(self.unit.id),
			f"{prefix}-0-unit_code": "A1-UPDATED",
			f"{prefix}-0-unit_type": "Single Room",
			f"{prefix}-0-price": "11500.00",
			f"{prefix}-0-deposit": "1000.00",
			f"{prefix}-0-vacancy": Unit.OCCUPIED,
			f"{prefix}-0-is_active": "on",
		}

		response = self.client.post(reverse("edit_property", args=[self.property.id]), data)

		self.assertEqual(response.status_code, 302)
		self.property.refresh_from_db()
		self.unit.refresh_from_db()
		self.assertEqual(self.property.name, "Renamed Property")
		self.assertEqual(self.unit.unit_code, "A1-UPDATED")
		self.assertEqual(self.unit.price, Decimal("11500.00"))

	def test_edit_property_image_delete_form_is_not_nested(self):
		image = PropertyImage.objects.create(
			property=self.property,
			image="property_images/test.jpg",
			is_primary=True,
		)
		self.client.force_login(self.landlord)

		response = self.client.get(reverse("edit_property", args=[self.property.id]))

		self.assertEqual(response.status_code, 200)
		content = response.content.decode()
		edit_form_start = content.index('<form method="post" enctype="multipart/form-data"')
		edit_form_end = content.index("</form>", edit_form_start)
		delete_button = content.index(f'form="delete-property-image-{image.id}"')
		delete_form = content.index(f'<form id="delete-property-image-{image.id}"')
		delete_property_form = content.index(
			f'action="{reverse("delete_property", args=[self.property.id])}"'
		)
		self.assertLess(delete_button, edit_form_end)
		self.assertGreater(delete_form, edit_form_end)
		self.assertGreater(delete_property_form, edit_form_end)
		self.assertIn(
			f'action="{reverse("delete_property_image", args=[image.id])}"',
			content,
		)

	def test_edit_property_deletes_unoccupied_unit(self):
		self.client.force_login(self.landlord)
		vacant_unit = Unit.objects.create(
			property=self.property,
			unit_code="A2",
			unit_type="Single Room",
			price=Decimal("9000.00"),
		)
		page = self.client.get(reverse("edit_property", args=[self.property.id]))
		formset = page.context["unit_formset"]
		prefix = formset.prefix
		data = {
			"name": self.property.name,
			"county": "",
			"town": self.property.town,
			"property_type": self.property.property_type,
			"price": str(self.property.price),
			"landlord_phone": self.property.landlord_phone,
			"description": "",
			f"{prefix}-TOTAL_FORMS": str(formset.total_form_count()),
			f"{prefix}-INITIAL_FORMS": str(formset.initial_form_count()),
			f"{prefix}-MIN_NUM_FORMS": "0",
			f"{prefix}-MAX_NUM_FORMS": "1000",
			f"{prefix}-0-id": str(self.unit.id),
			f"{prefix}-0-unit_code": self.unit.unit_code,
			f"{prefix}-0-unit_type": self.unit.unit_type,
			f"{prefix}-0-price": str(self.unit.price),
			f"{prefix}-0-deposit": "0.00",
			f"{prefix}-0-vacancy": Unit.OCCUPIED,
			f"{prefix}-0-is_active": "on",
			f"{prefix}-1-id": str(vacant_unit.id),
			f"{prefix}-1-unit_code": vacant_unit.unit_code,
			f"{prefix}-1-unit_type": vacant_unit.unit_type,
			f"{prefix}-1-price": str(vacant_unit.price),
			f"{prefix}-1-deposit": "0.00",
			f"{prefix}-1-vacancy": Unit.VACANT,
			f"{prefix}-1-DELETE": "on",
		}

		response = self.client.post(reverse("edit_property", args=[self.property.id]), data)

		self.assertEqual(
			response.status_code,
			302,
			f"formset={response.context['unit_formset'].errors if response.context else None}; "
			f"nonform={response.context['unit_formset'].non_form_errors() if response.context else None}",
		)
		self.assertFalse(Unit.objects.filter(id=vacant_unit.id).exists())

	def test_unit_with_tenancy_cannot_be_deleted_directly(self):
		with self.assertRaises(ProtectedError):
			self.unit.delete()

		self.assertTrue(Unit.objects.filter(id=self.unit.id).exists())
		self.assertTrue(Tenant.objects.filter(id=self.tenant.id).exists())

	def test_editing_tenant_records_incremental_payment(self):
		self.client.force_login(self.landlord)
		response = self.client.post(
			reverse("edit_tenant", args=[self.tenant.id]),
			{
				"name": self.tenant.name,
				"phone": self.tenant.phone,
				"unit_fk": self.unit.id,
				"date_occupied": "2026-01-01",
				"rent_paid": "0",
				"date_paid": "2026-01-15",
				"month_paid_for": "2026-01",
				"monthly_rent": "10000.00",
				"payment_amount": "2500.00",
			},
		)

		self.assertRedirects(response, reverse("tenant_list"))
		self.tenant.refresh_from_db()
		payment = RentPayment.objects.get(tenant=self.tenant)
		self.assertEqual(payment.amount, Decimal("2500.00"))
		self.assertEqual(payment.month_paid_for, "2026-01")
		self.assertEqual(self.tenant.rent_paid, Decimal("2500.00"))

	def test_repeated_receipt_download_does_not_create_financial_rows(self):
		payment = RentPayment.objects.create(
			tenant=self.tenant,
			amount=Decimal("2500.00"),
			date_paid=date(2026, 1, 15),
			month_paid_for="2026-01",
			monthly_rent=Decimal("10000.00"),
			rent_balance=Decimal("7500.00"),
			cumulative_rent_balance=Decimal("7500.00"),
		)
		self.client.force_login(self.landlord)

		first = self.client.post(reverse("generate_receipt", args=[self.tenant.id]))
		second = self.client.post(reverse("generate_receipt", args=[self.tenant.id]))

		self.assertEqual(first["Content-Type"], "application/pdf")
		self.assertEqual(second["Content-Type"], "application/pdf")
		self.assertEqual(RentPayment.objects.filter(tenant=self.tenant).count(), 1)
		self.assertEqual(ReceiptHistory.objects.filter(tenant=self.tenant).count(), 0)
		self.assertIsNotNone(payment.pk)

	def test_deposit_totals_stay_with_the_tenancy(self):
		DepositHistory.objects.create(
			unit=self.unit,
			tenancy=self.tenant,
			amount=Decimal("20000.00"),
			date_received=date(2026, 1, 1),
		)
		self.client.force_login(self.landlord)
		self.client.post(reverse("delete_tenant", args=[self.tenant.id]))
		next_tenant = Tenant.objects.create(
			name="Next Resident",
			phone="0712345679",
			unit_fk=self.unit,
			date_occupied=date(2026, 2, 1),
			rent_paid=Decimal("0.00"),
			date_paid=date(2026, 2, 1),
			month_paid_for="2026-02",
			monthly_rent=Decimal("10000.00"),
		)

		response = self.client.get(reverse("tenant_list"))

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.context["tenant_list"][0].id, next_tenant.id)
		self.assertEqual(response.context["tenant_list"][0].deposit_total, Decimal("0"))

	def test_get_request_does_not_delete_property_image(self):
		image = PropertyImage.objects.create(
			property=self.property,
			image="property_images/test.jpg",
			is_primary=True,
		)
		self.client.force_login(self.landlord)

		response = self.client.get(reverse("delete_property_image", args=[image.id]))

		self.assertEqual(response.status_code, 405)
		self.assertTrue(PropertyImage.objects.filter(id=image.id).exists())

	def test_post_request_deletes_property_image(self):
		image = PropertyImage.objects.create(
			property=self.property,
			image="property_images/test.jpg",
			is_primary=True,
		)
		self.client.force_login(self.landlord)

		response = self.client.post(reverse("delete_property_image", args=[image.id]))

		self.assertRedirects(response, reverse("edit_property", args=[self.property.id]))
		self.assertFalse(PropertyImage.objects.filter(id=image.id).exists())
