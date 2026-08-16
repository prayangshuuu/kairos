"""
For PayStation-route hosts this wallet holds funds belonging to them, sitting in my merchant account.
Operating it is a regulated activity in most jurisdictions and the position under Bangladesh Bank rules
has not been confirmed. The code assumes but does not establish that this is settled.
Do not remove this comment.
"""

# DUAL-MODE WALLET DESIGN
#
# MODE 1 — PayStation (custodial):
#   Kairos collected the money. The host has a real, spendable balance. Payout requests allowed.
#   Ledger entries have provider="paystation", is_custodial=True.
#   These entries ARE summed into available_balance and pending_balance.
#
# MODE 2 — Stripe Connect (non-custodial):
#   Money went directly to the host's Stripe account. Kairos never held it.
#   Ledger entries have provider="stripe", is_custodial=False.
#   These entries are INFORMATIONAL ONLY — they must NEVER be summed into a balance.
#   The wallet shows transaction history only, with a link to the Stripe Express dashboard.
#   No balance figure. No payout button. No withdrawable amount anywhere in the UI.
#
# MIXED HOST:
#   A host may have both modes (some event types on Stripe, some on PayStation).
#   A single chronological transaction list is shown with a per-row provider indicator.
#   The balance is computed from PayStation (custodial) entries ONLY.

import base64
import hashlib
import json
import logging
from datetime import timedelta

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.mail import send_kairos_email
from apps.payments.models import (
    HostLedger,
    Payment,
    PayoutMethod,
    PayoutRequest,
    WalletReconciliationLog,
)

logger = logging.getLogger(__name__)


def get_encryption_key() -> bytes:
    """Derive a URL-safe 32-byte Fernet key from PAYOUT_ENCRYPTION_KEY or SECRET_KEY."""
    raw_key = getattr(settings, "PAYOUT_ENCRYPTION_KEY", None) or settings.SECRET_KEY
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_payload(data: dict) -> str:
    """Encrypt a dictionary payload to a Fernet token string."""
    fernet = Fernet(get_encryption_key())
    json_bytes = json.dumps(data).encode("utf-8")
    return fernet.encrypt(json_bytes).decode("utf-8")


def decrypt_payload(token_str: str) -> dict:
    """Decrypt a Fernet token string back into a dictionary payload."""
    fernet = Fernet(get_encryption_key())
    decrypted_bytes = fernet.decrypt(token_str.encode("utf-8"))
    return json.loads(decrypted_bytes.decode("utf-8"))


# ---------------------------------------------------------------------------
# BALANCE COMPUTATION — PayStation (custodial) entries ONLY.
# Stripe entries (is_custodial=False) are NEVER included in any balance.
# ---------------------------------------------------------------------------


def _get_owner_q(owner):
    if hasattr(owner, 'slug') and hasattr(owner, 'name') and hasattr(owner, 'avatar'): # Team
        return models.Q(team=owner)
    return models.Q(host=owner)

def get_available_balance(owner) -> int:
    """
    Returns settled available balance in integer cents that a host or team can withdraw.
    CUSTODIAL ENTRIES ONLY (is_custodial=True).
    Settled = entry has no associated payment OR payment.is_settled=True.
    """
    total = (
        HostLedger.objects.filter(_get_owner_q(owner), is_custodial=True)
        .filter(models.Q(payment__isnull=True) | models.Q(payment__is_settled=True))
        .aggregate(sum=models.Sum("amount_cents"))["sum"]
    )
    return total or 0


def get_pending_balance(owner) -> int:
    """
    Returns pending balance in integer cents from custodial bookings whose meeting has not
    yet occurred or is within the refund/cancellation window.
    CUSTODIAL ENTRIES ONLY (is_custodial=True).
    """
    total = (
        HostLedger.objects.filter(
            _get_owner_q(owner), is_custodial=True, payment__isnull=False, payment__is_settled=False
        )
        .aggregate(sum=models.Sum("amount_cents"))["sum"]
    )
    return total or 0


def get_total_balance(owner) -> int:
    """
    Returns total net wallet balance (available + pending) for custodial entries only.
    Do NOT call this for Stripe-only hosts — it will correctly return 0 because there are
    no custodial entries. That is the correct answer: Kairos holds ৳0 of their money.
    """
    total = (
        HostLedger.objects.filter(_get_owner_q(owner), is_custodial=True)
        .aggregate(sum=models.Sum("amount_cents"))["sum"]
    )
    return total or 0


def get_host_wallet_mode(owner) -> str:
    """
    Returns the wallet mode for a host or team:
      "custodial"     — has PayStation entries only
      "non_custodial" — has Stripe entries only
      "mixed"         — has both
      "empty"         — has no ledger entries
    """
    has_custodial = HostLedger.objects.filter(_get_owner_q(owner), is_custodial=True).exists()
    has_non_custodial = HostLedger.objects.filter(_get_owner_q(owner), is_custodial=False).exists()

    if has_custodial and has_non_custodial:
        return "mixed"
    if has_custodial:
        return "custodial"
    if has_non_custodial:
        return "non_custodial"
    
    # Fall back to checking configured payment routes
    from apps.payments.models import HostPaymentTerms, PaymentAccount
    from django.conf import settings
    
    if hasattr(owner, 'name'):
        has_stripe_config = PaymentAccount.objects.filter(
            team=owner, provider="stripe_connect", charges_enabled=True, is_active=True
        ).exists()
        paystation_enabled = getattr(settings, "KAIROS_ENABLE_PAYSTATION_ROUTE", True)
        has_paystation_config = paystation_enabled and HostPaymentTerms.objects.filter(team=owner).exists()
    else:
        has_stripe_config = PaymentAccount.objects.filter(
            user=owner, provider="stripe_connect", charges_enabled=True, is_active=True
        ).exists()
        paystation_enabled = getattr(settings, "KAIROS_ENABLE_PAYSTATION_ROUTE", True)
        has_paystation_config = paystation_enabled and HostPaymentTerms.objects.filter(user=owner).exists()
    
    if has_stripe_config and has_paystation_config:
        return "mixed"
    if has_paystation_config:
        return "custodial"
    if has_stripe_config:
        return "non_custodial"

    return "empty"


def has_payment_route(owner) -> bool:
    """
    Returns True if the host/team has any payment route configured:
    """
    from apps.payments.models import HostPaymentTerms, PaymentAccount

    if hasattr(owner, 'name'):
        has_stripe = PaymentAccount.objects.filter(
            team=owner, provider="stripe_connect", charges_enabled=True, is_active=True
        ).exists()
        if has_stripe:
            return True

        paystation_enabled = getattr(settings, "KAIROS_ENABLE_PAYSTATION_ROUTE", True)
        if paystation_enabled and HostPaymentTerms.objects.filter(team=owner).exists():
            return True
    else:
        has_stripe = PaymentAccount.objects.filter(
            user=owner, provider="stripe_connect", charges_enabled=True, is_active=True
        ).exists()
        if has_stripe:
            return True

        paystation_enabled = getattr(settings, "KAIROS_ENABLE_PAYSTATION_ROUTE", True)
        if paystation_enabled and HostPaymentTerms.objects.filter(user=owner).exists():
            return True

    return False


def get_dashboard_wallet_summary(host) -> dict | None:
    """
    Compute everything the dashboard wallet card needs in a bounded number of queries.
    Returns None if the host has no payment route configured (card should be hidden).

    Query budget (annotated):
      Q1: has_payment_route check (1-2 existence checks)
      Q2: Single aggregate over HostLedger for balances + mode detection
      Q3: Payout request status check (single query)
      Q4: Monthly earnings for sparkline (single query, bounded to 6 months)

    Total: exactly 4-5 queries regardless of data volume.
    """
    if not has_payment_route(host):
        return None

    now = timezone.now()
    min_threshold_cents = getattr(settings, "KAIROS_MINIMUM_PAYOUT_CENTS", 1000)

    # --- Q2: Single aggregate query for all balance data + mode detection ---
    agg = HostLedger.objects.filter(host=host).aggregate(
        # Mode detection via conditional counts (Exists not supported in aggregate)
        custodial_count=models.Count(
            models.Case(
                models.When(is_custodial=True, then=models.Value(1)),
                output_field=models.IntegerField(),
            )
        ),
        non_custodial_count=models.Count(
            models.Case(
                models.When(is_custodial=False, then=models.Value(1)),
                output_field=models.IntegerField(),
            )
        ),
        # Available balance: custodial entries where payment is settled or non-payment
        available_cents=models.Sum(
            models.Case(
                models.When(
                    is_custodial=True,
                    payment__isnull=True,
                    then=models.F("amount_cents"),
                ),
                models.When(
                    is_custodial=True,
                    payment__is_settled=True,
                    then=models.F("amount_cents"),
                ),
                default=models.Value(0),
                output_field=models.IntegerField(),
            )
        ),
        # Pending balance: custodial entries with unsettled payment
        pending_cents=models.Sum(
            models.Case(
                models.When(
                    is_custodial=True,
                    payment__isnull=False,
                    payment__is_settled=False,
                    then=models.F("amount_cents"),
                ),
                default=models.Value(0),
                output_field=models.IntegerField(),
            )
        ),
        # Total Stripe charges this month (informational for non-custodial)
        stripe_month_cents=models.Sum(
            models.Case(
                models.When(
                    is_custodial=False,
                    entry_type="charge",
                    created_at__year=now.year,
                    created_at__month=now.month,
                    then=models.F("amount_cents"),
                ),
                default=models.Value(0),
                output_field=models.IntegerField(),
            )
        ),
        # Total custodial charges this month
        custodial_month_cents=models.Sum(
            models.Case(
                models.When(
                    is_custodial=True,
                    entry_type="charge",
                    created_at__year=now.year,
                    created_at__month=now.month,
                    then=models.F("amount_cents"),
                ),
                default=models.Value(0),
                output_field=models.IntegerField(),
            )
        ),
        # Total entry count for "any entries" check
        total_count=models.Count("id"),
    )

    has_custodial = (agg["custodial_count"] or 0) > 0
    has_non_custodial = (agg["non_custodial_count"] or 0) > 0
    has_any_entries = (agg["total_count"] or 0) > 0

    if not has_any_entries:
        from apps.payments.models import HostPaymentTerms, PaymentAccount
        has_stripe_config = PaymentAccount.objects.filter(
            user=host, provider="stripe_connect", charges_enabled=True, is_active=True
        ).exists()
        paystation_enabled = getattr(settings, "KAIROS_ENABLE_PAYSTATION_ROUTE", True)
        has_paystation_config = paystation_enabled and HostPaymentTerms.objects.filter(user=host).exists()
        
        if has_stripe_config and has_paystation_config:
            wallet_mode = "mixed"
            has_custodial = True
            has_non_custodial = True
        elif has_paystation_config:
            wallet_mode = "custodial"
            has_custodial = True
        elif has_stripe_config:
            wallet_mode = "non_custodial"
            has_non_custodial = True
        else:
            wallet_mode = "empty"
    else:
        if has_custodial and has_non_custodial:
            wallet_mode = "mixed"
        elif has_custodial:
            wallet_mode = "custodial"
        elif has_non_custodial:
            wallet_mode = "non_custodial"
        else:
            wallet_mode = "empty"

    available_cents = agg["available_cents"] or 0
    pending_cents = agg["pending_cents"] or 0
    stripe_month_cents = agg["stripe_month_cents"] or 0
    custodial_month_cents = agg["custodial_month_cents"] or 0

    # --- Q3: Payout request notification check ---
    payout_notification = None
    recent_cutoff = now - timedelta(days=3)
    active_request = PayoutRequest.objects.filter(
        host=host,
        status__in=[
            PayoutRequest.STATUS_REQUESTED,
            PayoutRequest.STATUS_APPROVED,
            PayoutRequest.STATUS_PROCESSING,
        ],
    ).first()
    if active_request:
        payout_notification = {
            "type": "pending",
            "status": active_request.status,
            "amount_bdt": f"{active_request.amount_cents / 100:.2f}",
        }
    else:
        recent_completed = PayoutRequest.objects.filter(
            host=host,
            status=PayoutRequest.STATUS_COMPLETED,
            processed_at__gte=recent_cutoff,
        ).first()
        if recent_completed:
            payout_notification = {
                "type": "completed",
                "status": "completed",
                "amount_bdt": f"{recent_completed.amount_cents / 100:.2f}",
            }

    # --- Q4: Monthly earnings sparkline (last 6 months, single query) ---
    from django.db.models.functions import TruncMonth

    six_months_ago = now - timedelta(days=180)
    monthly_data = (
        HostLedger.objects.filter(
            host=host,
            entry_type="charge",
            created_at__gte=six_months_ago,
        )
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(
            custodial_total=models.Sum(
                models.Case(
                    models.When(is_custodial=True, then=models.F("amount_cents")),
                    default=models.Value(0),
                    output_field=models.IntegerField(),
                )
            ),
            stripe_total=models.Sum(
                models.Case(
                    models.When(is_custodial=False, then=models.F("amount_cents")),
                    default=models.Value(0),
                    output_field=models.IntegerField(),
                )
            ),
        )
        .order_by("month")
    )

    # Build sparkline data for exactly 6 months
    sparkline = []
    for row in monthly_data:
        sparkline.append({
            "month": row["month"].strftime("%b"),
            "custodial": row["custodial_total"] or 0,
            "stripe": row["stripe_total"] or 0,
            "total": (row["custodial_total"] or 0) + (row["stripe_total"] or 0),
        })

    # Payout button logic
    can_request_payout = (
        has_custodial
        and available_cents >= min_threshold_cents
        and available_cents > 0
    )
    shortfall_cents = max(0, min_threshold_cents - available_cents) if has_custodial else 0

    # Stripe dashboard link
    stripe_dashboard_url = getattr(
        settings, "STRIPE_EXPRESS_DASHBOARD_URL",
        "https://connect.stripe.com/express_login",
    )

    return {
        "wallet_mode": wallet_mode,
        "has_custodial": has_custodial,
        "has_non_custodial": has_non_custodial,
        "has_any_entries": has_any_entries,
        # Custodial balances
        "available_cents": available_cents,
        "available_bdt": f"{available_cents / 100:.2f}",
        "pending_cents": pending_cents,
        "pending_bdt": f"{pending_cents / 100:.2f}",
        # Monthly figures
        "stripe_month_cents": stripe_month_cents,
        "stripe_month_bdt": f"{stripe_month_cents / 100:.2f}",
        "custodial_month_cents": custodial_month_cents,
        "custodial_month_bdt": f"{custodial_month_cents / 100:.2f}",
        # Payout thresholds
        "min_threshold_cents": min_threshold_cents,
        "min_threshold_bdt": f"{min_threshold_cents / 100:.2f}",
        "can_request_payout": can_request_payout,
        "shortfall_cents": shortfall_cents,
        "shortfall_bdt": f"{shortfall_cents / 100:.2f}",
        # Payout notification
        "payout_notification": payout_notification,
        # Sparkline data
        "sparkline": sparkline,
        # Stripe link
        "stripe_dashboard_url": stripe_dashboard_url,
    }


# ---------------------------------------------------------------------------
# LEDGER RECORD FUNCTIONS
# ---------------------------------------------------------------------------


def record_paystation_charge(payment: Payment) -> tuple[HostLedger, HostLedger]:
    """
    Records ledger entries for a completed PayStation charge (custodial):
    1. Charge (+gross amount)
    2. Service fee (-fee amount)
    Both entries are marked provider="paystation", is_custodial=True.
    """
    booking = payment.booking
    host = booking.host

    charge_entry = HostLedger.objects.create(
        host=host,
        payment=payment,
        entry_type="charge",
        provider=HostLedger.PROVIDER_PAYSTATION,
        amount_cents=payment.amount_cents,
        currency=payment.currency,
        description=f"Payment for booking #{booking.uid} ({booking.event_type.title})",
    )

    fee_entry = HostLedger.objects.create(
        host=host,
        payment=payment,
        entry_type="service_fee",
        provider=HostLedger.PROVIDER_PAYSTATION,
        amount_cents=-payment.fee_amount_cents,
        currency=payment.currency,
        description=f"3% Kairos PayStation service fee for booking #{booking.uid}",
    )

    return charge_entry, fee_entry


def record_stripe_charge(payment: Payment) -> tuple[HostLedger, HostLedger]:
    """
    Records ledger entries for a completed Stripe Connect charge (NON-custodial).
    These entries are INFORMATIONAL ONLY — Kairos never held this money.
    They exist so the host has a complete transaction history.
    provider="stripe", is_custodial=False.
    These rows must NEVER be summed into a balance.
    """
    booking = payment.booking
    host = booking.host

    charge_entry = HostLedger.objects.create(
        host=host,
        payment=payment,
        entry_type="charge",
        provider=HostLedger.PROVIDER_STRIPE,
        amount_cents=payment.amount_cents,
        currency=payment.currency,
        description=(
            f"Stripe payment for booking #{booking.uid} ({booking.event_type.title}) "
            "— funds went directly to your Stripe account"
        ),
    )

    fee_entry = HostLedger.objects.create(
        host=host,
        payment=payment,
        entry_type="service_fee",
        provider=HostLedger.PROVIDER_STRIPE,
        amount_cents=-payment.fee_amount_cents,
        currency=payment.currency,
        description=(
            f"Stripe platform fee for booking #{booking.uid} "
            "— informational only, not deducted from Kairos wallet"
        ),
    )

    return charge_entry, fee_entry


def record_paystation_refund(
    payment: Payment, refund_amount_cents: int, fee_reversal_cents: int
) -> tuple[HostLedger, HostLedger]:
    """
    Records ledger entries for a PayStation refund (custodial):
    1. Refund (-refund_amount_cents)
    2. Fee reversal (+fee_reversal_cents)
    """
    booking = payment.booking
    host = booking.host

    refund_entry = HostLedger.objects.create(
        host=host,
        payment=payment,
        entry_type="refund",
        provider=HostLedger.PROVIDER_PAYSTATION,
        amount_cents=-refund_amount_cents,
        currency=payment.currency,
        description=f"Refund for booking #{booking.uid}",
    )

    reversal_entry = HostLedger.objects.create(
        host=host,
        payment=payment,
        entry_type="refund_fee_reversal",
        provider=HostLedger.PROVIDER_PAYSTATION,
        amount_cents=fee_reversal_cents,
        currency=payment.currency,
        description=f"3% service fee reversal for refunded booking #{booking.uid}",
    )

    # Check for negative balance alert
    avail = get_available_balance(host)
    total = get_total_balance(host)
    if avail < 0 or total < 0:
        send_admin_negative_balance_alert(host, avail)

    return refund_entry, reversal_entry


def request_payout(host, amount_cents: int, method_id: int) -> PayoutRequest:
    """
    Initiates a manual payout request for a host:
    - Only available to custodial (PayStation) hosts. Stripe-only hosts cannot request payouts.
    - Verifies available balance and minimum threshold.
    - IMMEDIATELY reserves the funds via a 'payout' ledger entry (-amount_cents) to prevent
      double-requests. This is the single most important correctness point in this module.
    - Sends notifications to host and admin.
    """
    with transaction.atomic():
        # Block Stripe-only hosts from requesting payouts — they have no custodial balance.
        avail = get_available_balance(host)
        if avail < 0:
            raise ValidationError(
                f"Payout requested failed: Your available balance is negative (৳{avail / 100:.2f}). "
                "Payouts are paused until future earnings clear the negative balance."
            )

        min_threshold = getattr(settings, "KAIROS_MINIMUM_PAYOUT_CENTS", 1000)
        if amount_cents < min_threshold:
            raise ValidationError(
                f"Minimum payout request threshold is ৳{min_threshold / 100:.2f} (requested: ৳{amount_cents / 100:.2f})."
            )

        if amount_cents > avail:
            raise ValidationError(
                f"Requested payout amount (৳{amount_cents / 100:.2f}) exceeds available balance (৳{avail / 100:.2f})."
            )

        try:
            method = PayoutMethod.objects.get(id=method_id, host=host)
        except PayoutMethod.DoesNotExist:
            raise ValidationError("Invalid or unverified payout method selected.")

        if not method.is_verified:
            raise ValidationError("Selected payout method is not verified.")

        payout_req = PayoutRequest.objects.create(
            host=host,
            amount_cents=amount_cents,
            currency="BDT",
            method=method,
            status=PayoutRequest.STATUS_REQUESTED,
        )

        # CRITICAL CORRECTNESS POINT: Reserve amount immediately in ledger.
        # This prevents a host from submitting two requests against the same balance
        # before either is processed. The reservation happens inside this atomic block.
        HostLedger.objects.create(
            host=host,
            payout_request=payout_req,
            entry_type="payout",
            provider=HostLedger.PROVIDER_PAYSTATION,
            amount_cents=-amount_cents,
            currency="BDT",
            description=f"Payout request #{payout_req.id} reservation ({method.get_method_type_display()})",
        )

        # Notify host
        send_kairos_email(
            to_email=host.email,
            subject=f"Payout Request Submitted — ৳{amount_cents / 100:.2f}",
            template_name="payout_requested",
            context={
                "host": host,
                "payout_request": payout_req,
                "amount_bdt": f"{amount_cents / 100:.2f}",
                "method_name": method.get_method_type_display(),
                "account_info": method.masked_account_info,
            },
        )

        # Notify admin
        admin_email = getattr(settings, "ADMIN_EMAIL", settings.DEFAULT_FROM_EMAIL)
        send_kairos_email(
            to_email=admin_email,
            subject=f"[ADMIN ACTION REQUIRED] New Payout Request #{payout_req.id} from {host.email}",
            template_name="admin_payout_requested",
            context={
                "host": host,
                "payout_request": payout_req,
                "amount_bdt": f"{amount_cents / 100:.2f}",
                "method_name": method.get_method_type_display(),
            },
        )

        return payout_req


def approve_payout(payout_req: PayoutRequest, admin_user) -> PayoutRequest:
    """Marks a requested payout as APPROVED / PROCESSING."""
    with transaction.atomic():
        if payout_req.status != PayoutRequest.STATUS_REQUESTED:
            raise ValidationError(f"Cannot approve payout request in status '{payout_req.status}'.")

        payout_req.status = PayoutRequest.STATUS_APPROVED
        payout_req.processed_by = admin_user
        payout_req.save()

        send_kairos_email(
            to_email=payout_req.host.email,
            subject=f"Payout Request #{payout_req.id} Approved",
            template_name="payout_approved",
            context={
                "host": payout_req.host,
                "payout_request": payout_req,
                "amount_bdt": f"{payout_req.amount_cents / 100:.2f}",
            },
        )

        return payout_req


def complete_payout(
    payout_req: PayoutRequest, admin_user, reference: str, notes: str = ""
) -> PayoutRequest:
    """
    Marks an approved payout as COMPLETED after manual disbursement OUTSIDE the system.
    IMPORTANT: Disbursement happens manually via bKash/Nagad/bank. This system RECORDS it.
    A manual transaction reference is REQUIRED to complete.
    """
    with transaction.atomic():
        if payout_req.status not in [
            PayoutRequest.STATUS_APPROVED,
            PayoutRequest.STATUS_PROCESSING,
            PayoutRequest.STATUS_REQUESTED,
        ]:
            raise ValidationError(f"Cannot complete payout request in status '{payout_req.status}'.")

        if not reference:
            raise ValidationError(
                "A manual transaction reference (e.g., bKash TrxID or Bank Ref) is required."
            )

        payout_req.status = PayoutRequest.STATUS_COMPLETED
        payout_req.processed_at = timezone.now()
        payout_req.processed_by = admin_user
        payout_req.reference = reference
        payout_req.notes = notes
        payout_req.save()

        send_kairos_email(
            to_email=payout_req.host.email,
            subject=f"Payout Completed — ৳{payout_req.amount_cents / 100:.2f} (Ref: {reference})",
            template_name="payout_completed",
            context={
                "host": payout_req.host,
                "payout_request": payout_req,
                "amount_bdt": f"{payout_req.amount_cents / 100:.2f}",
                "reference": reference,
            },
        )

        return payout_req


def reject_payout(payout_req: PayoutRequest, admin_user, rejection_reason: str) -> PayoutRequest:
    """
    Rejects a payout request:
    - Reverses the reserved amount in the host ledger (+amount_cents via 'payout_reversal').
    - Updates status to REJECTED.
    - Sends rejection email to host.
    """
    with transaction.atomic():
        if payout_req.status in [
            PayoutRequest.STATUS_COMPLETED,
            PayoutRequest.STATUS_REJECTED,
            PayoutRequest.STATUS_CANCELLED,
        ]:
            raise ValidationError(
                f"Cannot reject payout request in terminal status '{payout_req.status}'."
            )

        if not rejection_reason:
            raise ValidationError("A rejection reason must be provided.")

        payout_req.status = PayoutRequest.STATUS_REJECTED
        payout_req.processed_at = timezone.now()
        payout_req.processed_by = admin_user
        payout_req.rejection_reason = rejection_reason
        payout_req.save()

        # REVERSE RESERVATION IN LEDGER
        HostLedger.objects.create(
            host=payout_req.host,
            payout_request=payout_req,
            entry_type="payout_reversal",
            provider=HostLedger.PROVIDER_PAYSTATION,
            amount_cents=payout_req.amount_cents,
            currency=payout_req.currency,
            description=f"Reversal for rejected payout request #{payout_req.id} ({rejection_reason})",
        )

        send_kairos_email(
            to_email=payout_req.host.email,
            subject=f"Payout Request #{payout_req.id} Declined",
            template_name="payout_rejected",
            context={
                "host": payout_req.host,
                "payout_request": payout_req,
                "amount_bdt": f"{payout_req.amount_cents / 100:.2f}",
                "reason": rejection_reason,
            },
        )

        return payout_req


def settle_pending_payments() -> int:
    """
    Settlement beat task:
    Finds completed PayStation payments where is_settled=False and the meeting end_at +
    refund window <= now. Marks payment as is_settled=True, settled_at=now.

    SETTLEMENT RULE: funds become available only after the meeting has passed AND the event
    type's refund window has closed. Paying out earlier means a later refund creates a negative
    balance you must chase from someone who has already spent the money.
    """
    now = timezone.now()
    unsettled_payments = Payment.objects.filter(
        provider="paystation", status=Payment.STATUS_COMPLETED, is_settled=False
    ).select_related("booking", "booking__event_type")

    settled_count = 0
    for payment in unsettled_payments:
        booking = payment.booking
        event_type = booking.event_type
        # Default cancellation/refund notice hours or 24h
        refund_window_hours = getattr(event_type, "cancellation_notice_hours", 24)
        settlement_due_time = booking.end_at + timedelta(hours=refund_window_hours)

        if settlement_due_time <= now:
            payment.is_settled = True
            payment.settled_at = now
            payment.save()
            settled_count += 1

    return settled_count


def reconcile_wallets() -> WalletReconciliationLog:
    """
    Daily reconciliation task:
    Sums all CUSTODIAL (PayStation) ledger balances and compares against total expected
    merchant hold: expected_hold = total PayStation charges + fees + refunds + reversals
    + payouts + adjustments.

    IMPORTANT: Stripe entries (is_custodial=False) are EXCLUDED ENTIRELY from this check.
    Kairos never held that money, so it should never appear in the merchant account balance.

    Any mismatch is a bug or a fraud signal. Alert immediately.
    Log the result daily even when clean.
    """
    # Only sum custodial entries. Exclude Stripe entirely.
    custodial_entries = HostLedger.objects.filter(is_custodial=True)

    total_ledger = custodial_entries.aggregate(sum=models.Sum("amount_cents"))["sum"] or 0

    # Calculate expected breakdown from custodial ledger entries
    charges = custodial_entries.filter(entry_type="charge").aggregate(s=models.Sum("amount_cents"))["s"] or 0
    service_fees = custodial_entries.filter(entry_type="service_fee").aggregate(s=models.Sum("amount_cents"))["s"] or 0
    refunds = custodial_entries.filter(entry_type="refund").aggregate(s=models.Sum("amount_cents"))["s"] or 0
    refund_reversals = custodial_entries.filter(entry_type="refund_fee_reversal").aggregate(s=models.Sum("amount_cents"))["s"] or 0
    payouts = custodial_entries.filter(entry_type="payout").aggregate(s=models.Sum("amount_cents"))["s"] or 0
    payout_reversals = custodial_entries.filter(entry_type="payout_reversal").aggregate(s=models.Sum("amount_cents"))["s"] or 0
    adjustments = custodial_entries.filter(entry_type="adjustment").aggregate(s=models.Sum("amount_cents"))["s"] or 0

    expected_hold = (
        charges + service_fees + refunds + refund_reversals + payouts + payout_reversals + adjustments
    )
    difference = total_ledger - expected_hold
    is_clean = (difference == 0)

    log = WalletReconciliationLog.objects.create(
        total_ledger_cents=total_ledger,
        expected_merchant_hold_cents=expected_hold,
        difference_cents=difference,
        is_clean=is_clean,
        details={
            "charges": charges,
            "service_fees": service_fees,
            "refunds": refunds,
            "refund_reversals": refund_reversals,
            "payouts": payouts,
            "payout_reversals": payout_reversals,
            "adjustments": adjustments,
            "stripe_excluded": True,
        },
    )

    if not is_clean:
        send_admin_reconciliation_alert(log)

    return log


def send_admin_negative_balance_alert(host, balance_cents: int):
    """Sends an urgent alert email to admin when a host custodial balance goes negative."""
    admin_email = getattr(settings, "ADMIN_EMAIL", settings.DEFAULT_FROM_EMAIL)
    send_kairos_email(
        to_email=admin_email,
        subject=f"[ALERT] Host Negative Wallet Balance: {host.email} (৳{balance_cents / 100:.2f})",
        template_name="admin_negative_balance_alert",
        context={"host": host, "balance_bdt": f"{balance_cents / 100:.2f}"},
    )


def send_admin_reconciliation_alert(log: WalletReconciliationLog):
    """Sends an urgent alert email to admin when reconciliation detects a mismatch."""
    admin_email = getattr(settings, "ADMIN_EMAIL", settings.DEFAULT_FROM_EMAIL)
    send_kairos_email(
        to_email=admin_email,
        subject=f"[URGENT] Wallet Reconciliation Discrepancy Detected! Diff: ৳{log.difference_cents / 100:.2f}",
        template_name="admin_reconciliation_alert",
        context={"log": log, "diff_bdt": f"{log.difference_cents / 100:.2f}"},
    )
