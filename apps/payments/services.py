# IMPORTANT NOTICE REGARDING PAYSTATION FALLBACK ROUTE:
# Operating this PayStation fallback route at scale involves money-transmission and regulatory licensing 
# questions because Kairos collects funds into its own merchant account on behalf of hosts.
# Before operating this route in production, the legal position must be confirmed with a qualified professional.
# This codebase assumes, but does not establish, that this regulatory position is settled.

import logging
import secrets
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.bookings.models import Booking
from apps.payments.models import Payment, PaymentAccount, SlotHold

logger = logging.getLogger(__name__)

# Named constant for PayStation 3% service fee
PAYSTATION_SERVICE_FEE_PERCENT = Decimal("3.0")

# Minimum payout threshold for PayStation route (default ৳1000 = 100,000 cents)
PAYSTATION_MIN_PAYOUT_THRESHOLD_CENTS = int(getattr(settings, 'PAYSTATION_MIN_PAYOUT_THRESHOLD_CENTS', 100000))

# Currencies widely supported by Stripe for presentment
SUPPORTED_CURRENCIES = {
    'USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'CHF', 'SEK', 'NOK', 'DKK',
    'NZD', 'SGD', 'HKD', 'BRL', 'MXN', 'INR', 'BDT', 'MYR', 'PHP', 'THB',
    'PLN', 'CZK', 'HUF', 'RON', 'BGN', 'HRK', 'ZAR', 'KES', 'NGN',
}


def compute_paystation_service_fee(amount_cents: int) -> int:
    """
    Calculate PayStation 3% service fee in cents using Decimal arithmetic with ROUND_HALF_UP rounding rule.
    
    Rounding rule:
    Amounts are rounded to the nearest integer cent/paisa using ROUND_HALF_UP (e.g., 0.5 cents rounds up to 1 cent).
    
    Gateway cost policy:
    PayStation's own processing cost comes out of Kairos's 3% service fee, NOT the host's 97%.
    The host sees a single clean 3% deduction, and the net amount promised (97%) is the exact amount received.
    """
    amount_dec = Decimal(str(amount_cents))
    fee_dec = (amount_dec * PAYSTATION_SERVICE_FEE_PERCENT / Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(fee_dec)


def compute_platform_fee(amount_cents: int) -> dict:
    """Compute the platform fee for a given amount.

    Uses settings:
      KAIROS_PLATFORM_FEE_PERCENT  (default 5.0)
      KAIROS_PLATFORM_FEE_FIXED_CENTS (default 0)
      KAIROS_PLATFORM_FEE_MIN_CENTS  (default 50)

    Fee = max(min_fee, (amount × percent / 100) + fixed)
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
    """Returns breakdown for host-facing display."""
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
def create_payment_for_booking(*, booking: Booking, payment_account: Optional[PaymentAccount] = None) -> Payment:
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
        
    if not payment_account:
        payment_account = PaymentAccount.objects.filter(
            user=event_type.owner, 
            provider=provider.name,
            is_active=True
        ).first()


    amount_cents = event_type.price_cents
    currency = event_type.currency.upper()

    date_str = timezone.now().strftime('%Y%m%d')
    random_hex = secrets.token_hex(2).upper()
    invoice_number = f"KRS-{date_str}-{random_hex}"

    if provider.name == 'paystation':
        fee_percent_applied = PAYSTATION_SERVICE_FEE_PERCENT
        fee_amount_cents = compute_paystation_service_fee(amount_cents)
        fee_fixed_applied = 0
        gateway_fee_cents = 0  # Kairos absorbs gateway costs out of its 3% fee
        net_owed_cents = amount_cents - fee_amount_cents
    else:
        fee_data = compute_platform_fee(amount_cents)
        fee_percent_applied = Decimal(str(fee_data['fee_percent']))
        fee_amount_cents = fee_data['fee_amount_cents']
        fee_fixed_applied = fee_data['fee_fixed']
        gateway_fee_cents = 0
        net_owed_cents = amount_cents - fee_amount_cents

    payment = Payment.objects.create(
        booking=booking,
        payment_account=payment_account,
        provider=provider.name,
        invoice_number=invoice_number,
        amount_cents=amount_cents,
        currency=currency,
        status=Payment.STATUS_PENDING,
        fee_percent_applied=fee_percent_applied,
        fee_fixed_applied=fee_fixed_applied,
        fee_amount_cents=fee_amount_cents,
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

    booking.status = Booking.StatusChoices.PENDING_PAYMENT
    booking.save(update_fields=['status', 'updated_at'])

    logger.info(
        f"Payment {payment.uid} created for booking {booking.uid}, "
        f"provider={provider.name}, amount={amount_cents} {currency}, fee={fee_amount_cents}"
    )
    return payment


@transaction.atomic
def confirm_payment(*, payment_uid) -> Payment:
    """Idempotent confirmation — the critical convergence point."""
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
    
    # Write to HostLedger for PayStation route
    if payment.provider == 'paystation':
        from apps.payments.models import HostLedger
        host = booking.event_type.owner
        
        # 1. Gross Charge (positive credit to host balance)
        HostLedger.objects.create(
            host=host, payment=payment, entry_type='charge',
            amount_cents=payment.amount_cents, currency=payment.currency,
            description=f"Payment for booking {booking.uid}"
        )
        
        # 2. Service Fee (negative debit to host balance)
        if payment.fee_amount_cents > 0:
            HostLedger.objects.create(
                host=host, payment=payment, entry_type='service_fee',
                amount_cents=-payment.fee_amount_cents, currency=payment.currency,
                description=f"PayStation 3% service charge for payment {payment.uid}"
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
    """Process a refund through the provider and update local state and ledger.

    Platform/Service fee IS refunded proportionally.
    Uses refund_application_fee=True on Stripe, and proportional fee reversal on PayStation.
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
            
            # Calculate proportional fee reversal using Decimal ROUND_HALF_UP
            amount_dec = Decimal(str(result.amount_refunded_cents))
            total_dec = Decimal(str(payment.amount_cents))
            orig_fee_dec = Decimal(str(payment.fee_amount_cents))
            
            fee_reversal_dec = (orig_fee_dec * amount_dec / total_dec).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            fee_reversal_cents = int(fee_reversal_dec)
            
            # 1. Reverse charge (negative debit to host balance)
            HostLedger.objects.create(
                host=payment.booking.event_type.owner,
                payment=payment,
                entry_type='refund',
                amount_cents=-result.amount_refunded_cents,
                currency=payment.currency,
                description=f"Refund for booking {payment.booking.uid}"
            )
            
            # 2. Reverse service fee (positive credit to host balance)
            if fee_reversal_cents > 0:
                HostLedger.objects.create(
                    host=payment.booking.event_type.owner,
                    payment=payment,
                    entry_type='refund_fee_reversal',
                    amount_cents=fee_reversal_cents,
                    currency=payment.currency,
                    description=f"Proportional reversal of 3% service fee for refund on payment {payment.uid}"
                )

    logger.info(
        f"Refunded {result.amount_refunded_cents} cents for payment {payment.uid}, "
        f"status now {payment.status}."
    )
    return result


def handle_dispute(*, payment: Payment, dispute_data: dict) -> None:
    """Handle a charge.dispute.created event."""
    payment.status = Payment.STATUS_DISPUTED
    payment.metadata = {**(payment.metadata or {}), 'dispute': dispute_data}
    payment.save(update_fields=['status', 'metadata', 'updated_at'])

    booking = payment.booking
    logger.warning(
        f"DISPUTE on payment {payment.uid} (booking {booking.uid}). "
        f"Host {booking.host.email} is merchant of record and must respond."
    )


def validate_event_type_currency(*, event_type, payment_account: PaymentAccount) -> dict:
    """Validate that an event type's currency is supported by the host's connected account."""
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
    return payment_account


@transaction.atomic
def generate_payout(*, host, period_start, period_end, notes: str = "") -> Optional[Any]:
    """
    Generate a payout for a host on the PayStation route for a given period.
    Checks host's total ledger balance. If negative or below minimum threshold, blocks payout.
    """
    from apps.payments.models import HostLedger, Payout
    from django.db.models import Sum

    # 1. Total balance across all host ledger entries
    current_balance = HostLedger.objects.filter(host=host).aggregate(Sum('amount_cents'))['amount_cents__sum'] or 0

    if current_balance <= 0:
        raise ValueError("Host balance is zero or negative. Cannot generate payout.")

    if current_balance < PAYSTATION_MIN_PAYOUT_THRESHOLD_CENTS:
        raise ValueError(
            f"Host balance (৳{current_balance/100:.2f}) is below minimum payout threshold "
            f"(৳{PAYSTATION_MIN_PAYOUT_THRESHOLD_CENTS/100:.2f})."
        )

    # 2. Aggregate un-payout-ed entries up to period_end
    entries = HostLedger.objects.filter(
        host=host,
        created_at__lte=period_end,
        payout__isnull=True
    )
    
    period_balance = entries.aggregate(Sum('amount_cents'))['amount_cents__sum'] or 0
    if period_balance <= 0:
        raise ValueError("No positive un-payout-ed balance in specified period.")

    gross_cents = entries.filter(entry_type='charge').aggregate(Sum('amount_cents'))['amount_cents__sum'] or 0
    fees_cents = abs(entries.filter(entry_type='service_fee').aggregate(Sum('amount_cents'))['amount_cents__sum'] or 0)

    payout = Payout.objects.create(
        host=host,
        period_start=period_start,
        period_end=period_end,
        gross_cents=gross_cents,
        fees_cents=fees_cents,
        net_cents=period_balance,
        status="PENDING",
        notes=notes,
    )
    
    entries.update(payout=payout)
    
    # Record payout debit in ledger (negative)
    HostLedger.objects.create(
        host=host,
        payout=payout,
        entry_type='payout',
        amount_cents=-period_balance,
        currency="BDT",
        description=f"Payout generated for period ending {period_end.strftime('%Y-%m-%d')}"
    )
    
    return payout
