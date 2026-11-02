from django.urls import path
from . import views

app_name = "ops"

urlpatterns = [
    path("", views.ops_dashboard, name="dashboard"),
    path("users/", views.manage_users, name="users"),
    path("activity/", views.audit_log, name="audit_log"),
    path("users/<int:user_id>/", views.user_detail, name="user_detail"),
    path("users/<int:user_id>/status/", views.toggle_user_active, name="toggle_user_active"),
    path("users/<int:user_id>/delete/", views.delete_user, name="delete_user"),
    path("data/reset/", views.reset_data, name="reset_data"),

    path("reports/market-overview.pdf", views.report_market_overview_pdf, name="market_overview_pdf"),
    path("reports/receipts-ledger.pdf", views.report_receipts_ledger_pdf, name="receipts_ledger_pdf"),
    path("reports/deposits-ledger.pdf", views.report_deposits_ledger_pdf, name="deposits_ledger_pdf"),
    path("reports/user-registry.pdf", views.report_user_registry_pdf, name="user_registry_pdf"),
    path("reports/operations-snapshot.pdf", views.report_operations_snapshot_pdf, name="operations_snapshot_pdf"),
    path("reports/inventory.pdf", views.report_inventory_pdf, name="inventory_pdf"),
    path("reports/booking-queue.pdf", views.report_booking_queue_pdf, name="booking_queue_pdf"),
    path("reports/data-integrity.pdf", views.report_data_integrity_pdf, name="data_integrity_pdf"),
    path("reports/activity-log.pdf", views.report_activity_log_pdf, name="activity_log_pdf"),
]
