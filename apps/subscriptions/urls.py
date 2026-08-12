from django.urls import path

from apps.subscriptions import views

app_name = "subscriptions"

urlpatterns = [
    path("pricing/", views.PricingView.as_view(), name="pricing"),
    path("settings/billing/", views.BillingSettingsView.as_view(), name="billing_settings"),
    path(
        "subscriptions/stripe/checkout/",
        views.StripeBillingCheckoutView.as_view(),
        name="stripe_checkout",
    ),
    path(
        "subscriptions/stripe/portal/",
        views.StripeCustomerPortalView.as_view(),
        name="stripe_portal",
    ),
    path(
        "subscriptions/paystation/start/",
        views.StartPaystationSubscriptionView.as_view(),
        name="paystation_start",
    ),
    path("subscriptions/cancel/", views.CancelSubscriptionView.as_view(), name="cancel"),
    path("webhooks/stripe-billing/", views.stripe_billing_webhook, name="stripe_billing_webhook"),
]
