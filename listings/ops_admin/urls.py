from django.urls import path
from . import views

app_name = "ops"

urlpatterns = [
    path("", views.ops_dashboard, name="dashboard"),

    path("reports/market-overview.pdf", views.report_market_overview_pdf, name="market_overview_pdf"),
    path("reports/receipts-ledger.pdf", views.report_receipts_ledger_pdf, name="receipts_ledger_pdf"),
    path("reports/deposits-ledger.pdf", views.report_deposits_ledger_pdf, name="deposits_ledger_pdf"),
    path("reports/user-registry.pdf", views.report_user_registry_pdf, name="user_registry_pdf"),
]
