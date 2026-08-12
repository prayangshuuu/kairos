# IMPORTANT NOTICE REGARDING PAYSTATION FALLBACK ROUTE:
# Operating this PayStation fallback route at scale involves money-transmission and regulatory licensing 
# questions because Kairos collects funds into its own merchant account on behalf of hosts.
# Before operating this route in production, the legal position must be confirmed with a qualified professional.
# This codebase assumes, but does not establish, that this regulatory position is settled.

import logging
from typing import Optional
from django.conf import settings
from apps.payments.models import PaymentAccount, HostPaymentTerms
from apps.payments.providers import PaymentProvider, StripeConnectProvider, PayStationProvider

logger = logging.getLogger(__name__)

SUPPORTED_STRIPE_CURRENCIES = {
    'USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'CHF', 'SEK', 'NOK', 'DKK',
    'NZD', 'SGD', 'HKD', 'BRL', 'MXN', 'INR', 'BDT', 'MYR', 'PHP', 'THB',
    'PLN', 'CZK', 'HUF', 'RON', 'BGN', 'HRK', 'ZAR', 'KES', 'NGN',
}


def is_stripe_eligible(host, currency: str) -> bool:
    """Return True if host has an active Stripe Connect account with charges_enabled supporting the currency."""
    currency_upper = currency.upper()
    if currency_upper not in SUPPORTED_STRIPE_CURRENCIES:
        return False
    return PaymentAccount.objects.filter(
        user=host,
        provider='stripe_connect',
        charges_enabled=True,
        is_active=True
    ).exists()


def is_paystation_eligible(host, currency: str) -> bool:
    """Return True if PayStation route is enabled, currency is BDT, and host accepted terms."""
    if not getattr(settings, 'KAIROS_ENABLE_PAYSTATION_ROUTE', True):
        return False
    if currency.upper() != 'BDT':
        return False
    return HostPaymentTerms.objects.filter(user=host).exists()


def select_provider(event_type) -> Optional[PaymentProvider]:
    """
    Select the payment provider for a given event type.
    
    Resolution order:
    a. The host's explicit choice for that event type, if still valid
    b. Their connected Stripe account, if charges_enabled and it supports the currency
    c. PayStation fallback, if the host is eligible (BDT, flag on, accepted the terms)
    d. None available — return None (blocks saving a price and prompts host)
    
    Never silently route to a provider the host did not knowingly agree to.
    """
    if not event_type or not event_type.is_paid:
        return None

    host = event_type.owner
    currency = (event_type.currency or 'USD').upper()

    # a. The host's explicit choice for that event type, if still valid
    if event_type.payment_provider:
        if event_type.payment_provider == 'stripe_connect' and is_stripe_eligible(host, currency):
            return StripeConnectProvider()
        elif event_type.payment_provider == 'paystation' and is_paystation_eligible(host, currency):
            return PayStationProvider()

    # b. Connected Stripe account, if charges_enabled and supports the currency
    if is_stripe_eligible(host, currency):
        return StripeConnectProvider()

    # c. PayStation fallback, if eligible (BDT, flag on, terms accepted)
    if is_paystation_eligible(host, currency):
        return PayStationProvider()

    # d. None available
    return None
