import logging
from django.conf import settings
from apps.payments.models import PaymentAccount
from apps.payments.providers import StripeConnectProvider, PayStationProvider

logger = logging.getLogger(__name__)

def get_available_providers(host, currency):
    """
    Return a dictionary of available payment providers for this host and currency.
    Keys are provider names, values are provider instances.
    """
    providers = {}
    
    # Check Stripe Connect
    stripe_acct = PaymentAccount.objects.filter(
        user=host, 
        provider='stripe_connect', 
        charges_enabled=True,
        is_active=True
    ).first()
    
    if stripe_acct:
        providers['stripe_connect'] = StripeConnectProvider()
        
    # Check PayStation (feature flagged, BDT only)
    if getattr(settings, 'KAIROS_ENABLE_PAYSTATION_ROUTE', False):
        if currency.upper() == 'BDT':
            # Check if host has an active paystation account/route setup
            # If we auto-provision paystation for all BD hosts, we could just enable it.
            # But let's check if they have a PaymentAccount for it.
            # Actually, for PayStation we don't necessarily need an external account ID since we collect it.
            # Let's say if they have the flag and currency is BDT, they can use it. But they need a PaymentAccount 
            # to signify they accepted terms or we configured it.
            ps_acct = PaymentAccount.objects.filter(
                user=host, 
                provider='paystation',
                is_active=True
            ).first()
            if ps_acct:
                providers['paystation'] = PayStationProvider()
            
    return providers

def select_provider(event_type):
    """
    Select the payment provider for a given event type.
    Resolution order:
    1. The host's explicitly chosen provider for that event type
    2. First provider supporting the event type's currency where host is onboarded
    3. None (error)
    """
    if not event_type.is_paid:
        return None
        
    providers = get_available_providers(event_type.owner, event_type.currency)
    
    if not providers:
        return None
        
    # 1. Explicit choice
    if event_type.payment_provider and event_type.payment_provider in providers:
        return providers[event_type.payment_provider]
        
    # 2. First available (Stripe preferred if both, dict maintains insertion order)
    return list(providers.values())[0]
