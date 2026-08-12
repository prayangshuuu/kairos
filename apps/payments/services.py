import logging
import secrets
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.bookings.models import Booking
from apps.payments.models import Payment, PaymentAccount, SlotHold

logger = logging.getLogger(__name__)

# Currencies widely supported by Stripe for presentment
SUPPORTED_CURRENCIES = {
    'USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'CHF', 'SEK', 'NOK', 'DKK',
    'NZD', 'SGD', 'HKD', 'BRL', 'MXN', 'INR', 'BDT', 'MYR', 'PHP', 'THB',
    'PLN', 'CZK', 'HUF', 'RON', 'BGN', 'HRK', 'ZAR', 'KES', 'NGN',
}


def compute_platform_fee(amount_cents: int) -> dict:
    """Compute the platform fee for a given amount.

    Uses settings:
      KAIROS_PLATFORM_FEE_PERCENT  (default 5.0)
      KAIROS_PLATFORM_FEE_FIXED_CENTS (default 0)
      KAIROS_PLATFORM_FEE_MIN_CENTS  (default 50)

    Fee = max(min_fee, (amount × percent / 100) + fixed)

    The returned dict captures the exact parameters at the time of computation
    so the fee stored on the Payment row never needs recomputation.
    """
    fee_percent = float(getattr(settings, 'KAIROS_PLATFORM_FEE_PERCENT', 5.0))
    fee_fixed = int(getattr(settings, 'KAIROS_PLATFORM_FEE_FIXED_CENTS', 0))
    min_fee = int(getattr(settings, 'KAIROS_PLATFORM_FEE_MIN_CENTS', 50))

    calculated_fee = int((amount_cents * fee_percent / 100.0) + fee_fixed)
    fee_amount_cents = max(min_fee, calculated_fee)

    return {
        'fee_percent': fee_percent,
        'fee_fixed': fee_fixed,
        'fee_amount_cents': fee_amount_cents,
    }


def compute_fee_breakdown(amount_cents: int, fee_amount_cents: int, currency: str = 'USD') -> dict:
    """Returns breakdown for host-facing display.

    Shows three lines:
      1. Total amount the invitee pays
      2. Kairos platform fee
      3. Estimated Stripe processing fee (2.9% + 30¢ standard rate)
      4. What the host actually receives

    Note: Stripe's own processing fee is deducted from the host's side on
    direct charges. The host's net = amount − Stripe fee − platform fee.
    """
    stripe_fee_estimate = int(amount_cents * 0.029) + 30  # Standard Stripe rate
    host_receives = amount_cents - fee_amount_cents - stripe_fee_estimate
    return {
        'amount_cents': amount_cents,
        'platform_fee_cents': fee_amount_cents,
        'stripe_fee_estimate_cents': stripe_fee_estimate,
        'host_receives_cents': max(0, host_receives),
        'currency': currency,
    }


@transaction.atomic
def create_payment_for_booking(*, booking: Booking) -> Payment:
    """Create a Payment and SlotHold for a paid booking.

    The fee is computed and frozen at creation time. Changing the global fee
    settings afterwards will NOT affect this payment's recorded fee.
    """
    event_type = booking.event_type
    if not event_type or event_type.price_cents <= 0:
        raise ValueError("Booking's event type is not paid.")

    from apps.payments.routing import select_provider
    provider = select_provider(event_type)
    
    if not provider:
        raise ValueError("No valid payment provider configured for this event type/currency.")
        
    payment_account = PaymentAccount.objects.filter(
        user=event_type.owner, 
        provider=provider.name,
        is_active=True
    ).first()
    
    if not payment_account and provider.name != 'paystation':
        # Paystation might not strictly need a payment_account record, 
        # but for consistency we require it. If missing, this shouldn't have been returned by get_available_providers.
        raise ValueError(f"Host lacks a valid payment account for {provider.name}")

    amount_cents = event_type.price_cents
    currency = event_type.currency
    fee_data = compute_platform_fee(amount_cents)

    date_str = timezone.now().strftime('%Y%m%d')
    random_hex = secrets.token_hex(2).upper()
    invoice_number = f"KRS-{date_str}-{random_hex}"

    # For PayStation, the gateway fee is typically our cost. Let's estimate it at 2.5% for BDT
    gateway_fee_cents = 0
    if provider.name == 'paystation':
        gateway_fee_cents = int(amount_cents * 0.025)
        
    net_owed_cents = amount_cents - fee_data['fee_amount_cents'] - gateway_fee_cents

    payment = Payment.objects.create(
        booking=booking,
        payment_account=payment_account,
        provider=provider.name,
        invoice_number=invoice_number,
        amount_cents=amount_cents,
        currency=currency,
        status=Payment.STATUS_PENDING,
        fee_percent_applied=Decimal(str(fee_data['fee_percent'])),
        fee_fixed_applied=fee_data['fee_fixed'],
        fee_amount_cents=fee_data['fee_amount_cents'],
        gateway_fee_cents=gateway_fee_cents,
        net_owed_cents=net_owed_cents,
    )

    ttl_minutes = int(getattr(settings, 'KAIROS_SLOT_HOLD_TTL_MINUTES', 30))
    expires_at = timezone.now() + timedelta(minutes=ttl_minutes)

    SlotHold.objects.create(
        booking=booking,
        payment=payment,
        expires_at=expires_at,
    )

    # Transition booking to pending_payment so the slot is held via the
    # exclusion constraint (BLOCKING_STATUSES includes "pending_payment").
    booking.status = Booking.StatusChoices.PENDING_PAYMENT
    booking.save(update_fields=['status', 'updated_at'])

    logger.info(
        f"Payment {payment.uid} created for booking {booking.uid}, "
        f"amount={amount_cents} {currency}, fee={fee_data['fee_amount_cents']}"
    )
    return payment


@transaction.atomic
def confirm_payment(*, payment_uid) -> Payment:
    """Idempotent confirmation — the critical convergence point.

    CRITICAL ORDERING: The Stripe webhook frequently arrives BEFORE the
    customer's browser redirect returns. Both paths call this function.
    Whichever arrives second sees status=COMPLETED and returns immediately.
    """
    payment = Payment.objects.select_for_update().get(uid=payment_uid)

    if payment.status == Payment.STATUS_COMPLETED:
        logger.info(f"Payment {payment.uid} already confirmed (idempotent no-op).")
        return payment

    if payment.status != Payment.STATUS_PENDING:
        logger.warning(
            f"Attempted to confirm payment {payment.uid} in status {payment.status}."
        )
        return payment

    payment.status = Payment.STATUS_COMPLETED
    payment.save(update_fields=['status', 'updated_at'])
    
    booking = payment.booking
    
    # Write to HostLedger for PayStation
    if payment.provider == 'paystation':
        from apps.payments.models import HostLedger
        host = booking.event_type.owner
        
        # 1. Gross Charge
        HostLedger.objects.create(
            host=host, payment=payment, entry_type='charge',
            amount_cents=payment.amount_cents, currency=payment.currency,
            description=f"Payment for booking {booking.uid}"
        )
        
        # 2. Platform Fee (negative)
        if payment.fee_amount_cents > 0:
            HostLedger.objects.create(
                host=host, payment=payment, entry_type='platform_fee',
                amount_cents=-payment.fee_amount_cents, currency=payment.currency,
                description="Kairos platform fee"
            )
            
        # 3. Gateway Fee (negative)
        if payment.gateway_fee_cents > 0:
            HostLedger.objects.create(
                host=host, payment=payment, entry_type='gateway_fee',
                amount_cents=-payment.gateway_fee_cents, currency=payment.currency,
                description="Gateway processing fee"
            )

    if booking.event_type.requires_confirmation:
        booking.status = Booking.StatusChoices.PENDING
    else:
        booking.status = Booking.StatusChoices.CONFIRMED
    booking.save(update_fields=['status', 'updated_at'])

    # Release the SlotHold
    try:
        hold = SlotHold.objects.get(payment=payment)
        hold.is_released = True
        hold.released_at = timezone.now()
        hold.release_reason = 'completed'
        hold.save(update_fields=['is_released', 'released_at', 'release_reason'])
    except SlotHold.DoesNotExist:
        logger.warning(f"No SlotHold found for payment {payment.uid}")

    # Trigger downstream processing (calendar events, emails, etc.)
    if booking.status == Booking.StatusChoices.CONFIRMED:
        from apps.bookings.tasks import process_booking_confirmation
        transaction.on_commit(lambda: process_booking_confirmation.delay(booking.id))

    logger.info(f"Payment {payment.uid} confirmed, booking {booking.uid} -> {booking.status}.")
    return payment


@transaction.atomic
def expire_payment(*, payment_uid) -> Payment:
    """Expire a pending payment when checkout session expires or hold times out."""
    payment = Payment.objects.select_for_update().get(uid=payment_uid)

    if payment.status != Payment.STATUS_PENDING:
        logger.info(f"Payment {payment.uid} not pending ({payment.status}), skip expiration.")
        return payment

    payment.status = Payment.STATUS_FAILED
    payment.save(update_fields=['status', 'updated_at'])

    # Release the SlotHold
    try:
        hold = SlotHold.objects.get(payment=payment)
        hold.is_released = True
        hold.released_at = timezone.now()
        hold.release_reason = 'expired'
        hold.save(update_fields=['is_released', 'released_at', 'release_reason'])
    except SlotHold.DoesNotExist:
        pass

    # Cancel the booking to free the slot
    booking = payment.booking
    if booking.status == Booking.StatusChoices.PENDING_PAYMENT:
        booking.status = Booking.StatusChoices.CANCELLED
        booking.cancelled_by = Booking.CancelledByChoices.SYSTEM
        booking.cancellation_reason = "Payment expired."
        booking.cancelled_at = timezone.now()
        booking.save(update_fields=[
            'status', 'cancelled_by', 'cancellation_reason', 'cancelled_at', 'updated_at'
        ])

    logger.info(f"Payment {payment.uid} expired, booking {booking.uid} cancelled.")
    return payment


def handle_refund(*, payment: Payment, amount_cents: Optional[int] = None):
    """Process a refund through the provider and update local state.

    Platform fee IS refunded proportionally — keeping a fee on a cancelled
    meeting is indefensible. Uses refund_application_fee=True.
    """
    from apps.payments.providers import StripeConnectProvider, PayStationProvider

    if amount_cents is None:
        amount_cents = payment.amount_cents

    if payment.provider == 'paystation':
        provider = PayStationProvider()
    else:
        provider = StripeConnectProvider()
        
    result = provider.refund(payment, amount_cents=amount_cents)

    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        payment.refund_amount_cents = (payment.refund_amount_cents or 0) + result.amount_refunded_cents
        if payment.refund_amount_cents >= payment.amount_cents:
            payment.status = Payment.STATUS_REFUNDED
        else:
            payment.status = Payment.STATUS_PARTIALLY_REFUNDED
        payment.save(update_fields=['status', 'refund_amount_cents', 'updated_at'])
        
        # Write to HostLedger for PayStation
        if payment.provider == 'paystation' and result.amount_refunded_cents > 0:
            from apps.payments.models import HostLedger
            
            HostLedger.objects.create(
                host=payment.booking.event_type.owner,
                payment=payment,
                entry_type='refund',
                amount_cents=-result.amount_refunded_cents,
                currency=payment.currency,
                description=f"Refund for booking {payment.booking.uid}"
            )

    logger.info(
        f"Refunded {result.amount_refunded_cents} cents for payment {payment.uid}, "
        f"status now {payment.status}."
    )
    return result


def handle_dispute(*, payment: Payment, dispute_data: dict) -> None:
    """Handle a charge.dispute.created event.

    The host is merchant of record on direct charges, so THEY handle the
    dispute, not Kairos. We flag it and notify them.
    """
    payment.status = Payment.STATUS_DISPUTED
    payment.metadata = {**(payment.metadata or {}), 'dispute': dispute_data}
    payment.save(update_fields=['status', 'metadata', 'updated_at'])

    booking = payment.booking
    logger.warning(
        f"DISPUTE on payment {payment.uid} (booking {booking.uid}). "
        f"Host {booking.host.email} is merchant of record and must respond. "
        f"Evidence due by: {dispute_data.get('evidence_details', {}).get('due_by', 'unknown')}."
    )


def validate_event_type_currency(*, event_type, payment_account: PaymentAccount) -> dict:
    """Validate that an event type's currency is supported by the host's connected account.

    Returns dict with 'valid', 'warning', and 'error' keys.
    """
    currency = event_type.currency.upper()
    default_currency = (payment_account.default_currency or '').upper()

    if currency == default_currency:
        return {'valid': True, 'warning': None, 'error': None}

    if currency in SUPPORTED_CURRENCIES:
        return {
            'valid': True,
            'warning': (
                f"Event currency ({currency}) differs from your account's settlement "
                f"currency ({default_currency}). Stripe will convert at their rate, "
                f"which costs approximately 1% on top of standard fees."
            ),
            'error': None,
        }

    return {
        'valid': False,
        'warning': None,
        'error': f"Currency {currency} is not supported by your connected Stripe account.",
    }


def sync_payment_account_from_stripe(
    *, payment_account: PaymentAccount, stripe_account_data: dict
) -> PaymentAccount:
    """Sync local PaymentAccount state from Stripe's account.updated webhook or API response."""
    charges_enabled = stripe_account_data.get('charges_enabled', False)

    payment_account.charges_enabled = charges_enabled
    payment_account.payouts_enabled = stripe_account_data.get('payouts_enabled', False)
    payment_account.details_submitted = stripe_account_data.get('details_submitted', False)

    requirements = stripe_account_data.get('requirements', {})
    payment_account.requirements_due = requirements.get('currently_due', [])

    if 'default_currency' in stripe_account_data:
        payment_account.default_currency = stripe_account_data['default_currency'].upper()

    if 'country' in stripe_account_data:
        payment_account.country = stripe_account_data['country']

    if charges_enabled and not payment_account.onboarding_completed_at:
        payment_account.onboarding_completed_at = timezone.now()

    payment_account.save()
    logger.info(
        f"Synced PaymentAccount {payment_account.external_account_id}: "
        f"charges_enabled={charges_enabled}, requirements={payment_account.requirements_due}"
    )
    return payment_account


@transaction.atomic
def generate_payout(*, host, period_start, period_end) -> Optional[Any]:
    """
    Generate a payout for a host on the PayStation route for a given period.
    Sums up all ledger entries in the period, creates a Payout, and adds a payout entry.
    """
    from apps.payments.models import HostLedger, Payout
    from django.db.models import Sum

    # Aggregate un-payout-ed entries in the period
    entries = HostLedger.objects.filter(
        host=host,
        created_at__gte=period_start,
        created_at__lt=period_end,
        payout__isnull=True
    )
    
    total_balance = entries.aggregate(Sum('amount_cents'))['amount_cents__sum'] or 0
    
    if total_balance <= 0:
        logger.info(f"No positive balance for {host.email} in period {period_start} to {period_end}. Skip payout.")
        return None
        
    gross_cents = entries.filter(entry_type='charge').aggregate(Sum('amount_cents'))['amount_cents__sum'] or 0
    fees_cents = abs(entries.filter(entry_type__in=['platform_fee', 'gateway_fee']).aggregate(Sum('amount_cents'))['amount_cents__sum'] or 0)

    payout = Payout.objects.create(
        host=host,
        period_start=period_start,
        period_end=period_end,
        gross_cents=gross_cents,
        fees_cents=fees_cents,
        net_cents=total_balance,
        status="PENDING",
    )
    
    entries.update(payout=payout)
    
    # Create the payout entry in the ledger (negative)
    HostLedger.objects.create(
        host=host,
        payout=payout,
        entry_type='payout',
        amount_cents=-total_balance,
        currency="BDT",  # Paystation is BDT only
        description=f"Payout generated for period {period_start.strftime('%Y-%m-%d')} to {period_end.strftime('%Y-%m-%d')}"
    )
    
    return payout
