from django.urls import path

from apps.payments import views

app_name = 'payments'

urlpatterns = [
    # Stripe Connect onboarding
    path(
        'connect/onboard/',
        views.StripeConnectOnboardView.as_view(),
        name='stripe_connect_onboard',
    ),
    path(
        'connect/return/',
        views.StripeConnectReturnView.as_view(),
        name='stripe_connect_return',
    ),
    path(
        'connect/refresh/',
        views.StripeConnectRefreshView.as_view(),
        name='stripe_connect_refresh',
    ),

    # Stripe Connect webhook (no auth, CSRF exempt)
    path(
        'webhooks/stripe-connect/',
        views.stripe_connect_webhook,
        name='stripe_connect_webhook',
    ),

    # Payment flow (invitee-facing)
    path(
        'return/',
        views.PaymentReturnView.as_view(),
        name='payment_return',
    ),
    path(
        'cancel/',
        views.PaymentCancelView.as_view(),
        name='payment_cancel',
    ),

    # Host dashboard
    path(
        'connect/dashboard/',
        views.ConnectDashboardView.as_view(),
        name='connect_dashboard',
    ),

    # Fee calculator (HTMX partial)
    path(
        'fee-calculator/',
        views.FeeCalculatorView.as_view(),
        name='fee_calculator',
    ),
]
