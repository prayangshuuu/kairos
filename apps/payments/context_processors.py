"""
Context processor for wallet navigation items.
Provides show_wallet_nav and wallet_nav_badge to all templates
that use the app layout.
"""

from datetime import timedelta

from django.utils import timezone


def wallet_nav(request):
    """
    Adds wallet navigation context variables:
    - show_wallet_nav: True if user has a payment route configured
    - wallet_nav_badge: True if there's a pending or recently completed payout request
    """
    if not request.user.is_authenticated:
        return {"show_wallet_nav": False, "wallet_nav_badge": False}

    from apps.payments.wallet import has_payment_route
    from apps.payments.models import PayoutRequest

    show = has_payment_route(request.user)
    if not show:
        return {"show_wallet_nav": False, "wallet_nav_badge": False}

    # Check for active or recently completed payout requests
    now = timezone.now()
    has_active = PayoutRequest.objects.filter(
        host=request.user,
        status__in=[
            PayoutRequest.STATUS_REQUESTED,
            PayoutRequest.STATUS_APPROVED,
            PayoutRequest.STATUS_PROCESSING,
        ],
    ).exists()

    badge = has_active
    if not badge:
        recent_cutoff = now - timedelta(days=3)
        badge = PayoutRequest.objects.filter(
            host=request.user,
            status=PayoutRequest.STATUS_COMPLETED,
            processed_at__gte=recent_cutoff,
        ).exists()

    return {
        "show_wallet_nav": True,
        "wallet_nav_badge": badge,
    }
