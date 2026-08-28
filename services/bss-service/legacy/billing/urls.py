from django.urls import path

from . import views

urlpatterns = [
    path("invoices", views.InvoiceListCreateView.as_view()),
    path("invoices/generate-due", views.GenerateDueInvoicesView.as_view()),
    path("invoices/refresh-overdue", views.RefreshOverdueInvoicesView.as_view()),
    path("invoices/<int:invoice_id>", views.InvoiceDetailView.as_view()),
    path("ledger", views.LedgerListView.as_view()),
    path("ledger/<int:entry_id>", views.LedgerEntryDetailView.as_view()),
    path("adjustments", views.AdjustmentCreateView.as_view()),
    path("settings", views.BillingSettingsView.as_view()),
    path("accounts", views.BillingAccountListView.as_view()),
    path("accounts/<int:account_id>", views.BillingAccountDetailView.as_view()),
    path("accounts/<int:account_id>/plan", views.BillingAccountPlanView.as_view()),
]
