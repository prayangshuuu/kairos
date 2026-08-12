from django.shortcuts import render
from django.views import View


class PricingView(View):
    """Honest pricing page explaining Kairos's 100% free business model."""

    def get(self, request):
        return render(request, "subscriptions/pricing.html")
