from django.urls import path

from apps.payments import views

app_name = "payments"

urlpatterns = [
    # Stripe Connect onboarding
    path(
        "connect/onboard/",
        views.StripeConnectOnboardView.as_view(),
        name="stripe_connect_onboard",
    ),
    path(
        "connect/return/",
        views.StripeConnectReturnView.as_view(),
        name="stripe_connect_return",
    ),
    path(
        "connect/refresh/",
        views.StripeConnectRefreshView.as_view(),
        name="stripe_connect_refresh",
    ),
    # Stripe Connect webhook (no auth, CSRF exempt)
    path(
        "webhooks/stripe-connect/",
        views.stripe_connect_webhook,
        name="stripe_connect_webhook",
    ),
    # Payment flow (invitee-facing)
    path(
        "return/",
        views.PaymentReturnView.as_view(),
        name="payment_return",
    ),
    path(
        "cancel/",
        views.PaymentCancelView.as_view(),
        name="payment_cancel",
    ),
    # Host dashboard
    path(
        "connect/dashboard/",
        views.ConnectDashboardView.as_view(),
        name="connect_dashboard",
    ),
    # Staff-only: this instance's own Stripe platform credentials (self-hosting)
    path(
        "settings/stripe/",
        views.PlatformStripeSettingsView.as_view(),
        name="platform_stripe_settings",
    ),
    # Fee calculator (HTMX partial)
    path(
        "fee-calculator/",
        views.FeeCalculatorView.as_view(),
        name="fee_calculator",
    ),
    path("enable-paystation/", views.EnablePaystationView.as_view(), name="enable_paystation"),

    # Host Wallet & Payouts (Task 39)
    path("wallet/", views.HostWalletView.as_view(), name="wallet"),
    path("wallet/methods/add/", views.AddPayoutMethodView.as_view(), name="add_payout_method"),
    path("wallet/methods/<int:method_id>/delete/", views.DeletePayoutMethodView.as_view(), name="delete_payout_method"),
    path("wallet/request/", views.RequestPayoutView.as_view(), name="request_payout"),
    path("wallet/statement/csv/", views.DownloadStatementCSVView.as_view(), name="wallet_statement_csv"),
    path("wallet/statement/pdf/", views.DownloadStatementPDFView.as_view(), name="wallet_statement_pdf"),

    # Admin Payout Queue (Task 39)
    path("admin/payouts/", views.AdminPayoutQueueView.as_view(), name="admin_payout_queue"),
    path("admin/payouts/<int:request_id>/process/", views.AdminPayoutProcessView.as_view(), name="admin_payout_process"),
    path("admin/payouts/export/csv/", views.AdminPayoutExportCSVView.as_view(), name="admin_payout_export_csv"),
]
