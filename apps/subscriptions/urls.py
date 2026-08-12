from django.urls import path

from apps.subscriptions import views

app_name = "subscriptions"

urlpatterns = [
    path("pricing/", views.PricingView.as_view(), name="pricing"),
]
