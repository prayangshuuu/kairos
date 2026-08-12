import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.payments.models import (
    Payment,
    PaymentAccount,
    ReconciliationFlag,
    SlotHold,
)
from apps.payments.providers import StripeConnectProvider
from apps.payments.services import (
    confirm_payment,
    expire_payment,
    handle_dispute,
    sync_payment_account_from_stripe,
)

logger = logging.getLogger(__name__)


@shared_task
def process_checkout_completed(event_data: dict):
    """Handle checkout.session.completed — confirm the booking.

    The same confirm_payment() function is called by the browser redirect
    return view. Whichever arrives second is a no-op.
    """
    session = event_data.get('data', {}).get('object', {})
    payment_uid = session.get('client_reference_id')

    if not payment_uid:
        # Also check metadata as fallback
        payment_uid = session.get('metadata', {}).get('payment_uid')

    if not payment_uid:
        logger.error(
            "checkout.session.completed: no payment UID found",
            extra={"session_id": session.get("id")},
        )
        return

    try:
        payment = confirm_payment(payment_uid=payment_uid)

        # Store the external IDs if not already set
        if session.get('payment_intent'):
            Payment.objects.filter(uid=payment_uid).update(
                external_payment_intent_id=session['payment_intent'],
                external_session_id=session.get('id', ''),
            )

        logger.info(f"checkout.session.completed processed for payment {payment_uid}")
    except Payment.DoesNotExist:
        logger.error(f"Payment {payment_uid} does not exist")
    except Exception as e:
        logger.error(f"Error confirming payment {payment_uid}: {e}", exc_info=True)
        raise


@shared_task
def process_checkout_expired(event_data: dict):
    """Handle checkout.session.expired — release the SlotHold."""
    session = event_data.get('data', {}).get('object', {})
    payment_uid = session.get('client_reference_id') or session.get('metadata', {}).get('payment_uid')

    if not payment_uid:
        logger.error(
            "checkout.session.expired: no payment UID found",
            extra={"session_id": session.get("id")},
        )
        return

    try:
        expire_payment(payment_uid=payment_uid)
        logger.info(f"checkout.session.expired processed for payment {payment_uid}")
    except Payment.DoesNotExist:
        logger.error(f"Payment {payment_uid} does not exist")
    except Exception as e:
        logger.error(f"Error expiring payment {payment_uid}: {e}", exc_info=True)
        raise


@shared_task
def process_charge_refunded(event_data: dict):
    """Handle charge.refunded — update Payment status."""
    charge = event_data.get('data', {}).get('object', {})
    charge_id = charge.get('id', '')
    payment_intent_id = charge.get('payment_intent', '')

    payment = None
    if charge_id:
        payment = Payment.objects.filter(external_charge_id=charge_id).first()
    if not payment and payment_intent_id:
        payment = Payment.objects.filter(external_payment_intent_id=payment_intent_id).first()

    if not payment:
        logger.error(
            f"charge.refunded: Payment not found for charge={charge_id}, intent={payment_intent_id}"
        )
        return

    refunded_amount = charge.get('amount_refunded', 0)
    total_amount = charge.get('amount', 0)

    if refunded_amount >= total_amount:
        payment.status = Payment.STATUS_REFUNDED
    elif refunded_amount > 0:
        payment.status = Payment.STATUS_PARTIALLY_REFUNDED

    payment.refund_amount_cents = refunded_amount
    payment.save(update_fields=['status', 'refund_amount_cents', 'updated_at'])
    logger.info(f"charge.refunded: Payment {payment.uid} -> {payment.status}")


@shared_task
def process_dispute_created(event_data: dict):
    """Handle charge.dispute.created — flag the booking, alert the host.

    The host is merchant of record on direct charges. They handle the dispute,
    NOT Kairos.
    """
    dispute = event_data.get('data', {}).get('object', {})
    charge_id = dispute.get('charge', '')

    if not charge_id:
        logger.error("charge.dispute.created: no charge_id in dispute data")
        return

    payment = Payment.objects.filter(external_charge_id=charge_id).select_related('booking__host').first()
    if not payment:
        # Try via payment_intent
        pi = dispute.get('payment_intent', '')
        if pi:
            payment = Payment.objects.filter(external_payment_intent_id=pi).select_related('booking__host').first()

    if not payment:
        logger.error(f"charge.dispute.created: Payment not found for charge {charge_id}")
        return

    handle_dispute(payment=payment, dispute_data=dispute)

    deadline = dispute.get('evidence_details', {}).get('due_by')
    booking = payment.booking
    logger.warning(
        f"DISPUTE on payment {payment.uid}, booking {booking.uid}. "
        f"Host {booking.host.email} is merchant of record and must respond. "
        f"Evidence deadline: {deadline}"
    )


@shared_task
def process_account_updated(event_data: dict):
    """Handle account.updated — refresh PaymentAccount state.

    This is how we learn that onboarding completed or capabilities were revoked.
    """
    account_data = event_data.get('data', {}).get('object', {})
    external_account_id = account_data.get('id', '')

    if not external_account_id:
        logger.error("account.updated: no account id in event data")
        return

    payment_account = PaymentAccount.objects.filter(
        external_account_id=external_account_id
    ).first()

    if not payment_account:
        logger.error(f"account.updated: PaymentAccount not found for {external_account_id}")
        return

    try:
        sync_payment_account_from_stripe(
            payment_account=payment_account,
            stripe_account_data=account_data,
        )
        logger.info(f"account.updated: synced PaymentAccount for {external_account_id}")
    except Exception as e:
        logger.error(f"Error syncing account {external_account_id}: {e}", exc_info=True)


@shared_task
def release_expired_slot_holds():
    """Periodic task — runs every minute to clean up expired holds."""
    now = timezone.now()
    expired_holds = SlotHold.objects.filter(
        expires_at__lte=now,
        is_released=False,
    ).select_related('payment')

    count = 0
    for hold in expired_holds:
        try:
            expire_payment(payment_uid=str(hold.payment.uid))
            count += 1
        except Exception as e:
            logger.error(f"Error releasing expired hold {hold.uid}: {e}", exc_info=True)

    if count > 0:
        logger.info(f"Released {count} expired slot holds")


@shared_task
def reconcile_payments():
    """Daily reconciliation — compare our Payment rows against Stripe.

    Checks the previous 3 days of payments. Flags mismatches for manual review.
    Payment systems drift; find it before a customer does.
    """
    three_days_ago = timezone.now() - timedelta(days=3)
    payments = Payment.objects.filter(
        status__in=[
            Payment.STATUS_COMPLETED,
            Payment.STATUS_REFUNDED,
            Payment.STATUS_PARTIALLY_REFUNDED,
        ],
        created_at__gte=three_days_ago,
        provider='stripe_connect',
    ).select_related('payment_account')

    provider = StripeConnectProvider()
    checked = 0
    mismatches = 0

    for payment in payments:
        checked += 1
        try:
            connected_account_id = (
                payment.payment_account.external_account_id
                if payment.payment_account
                else None
            )

            if payment.external_session_id:
                session_status = provider.get_session_status(
                    payment.external_session_id,
                    connected_account_id=connected_account_id,
                )
                remote_payment_status = session_status.get('payment_status', '')

                # Detect mismatch: we say COMPLETED but Stripe says unpaid
                if remote_payment_status == 'unpaid' and payment.status == Payment.STATUS_COMPLETED:
                    ReconciliationFlag.objects.create(
                        payment=payment,
                        flag_type='status_mismatch',
                        description=(
                            f"Local status is {payment.status} but Stripe session "
                            f"payment_status is '{remote_payment_status}'."
                        ),
                        local_state={'status': payment.status},
                        remote_state=session_status,
                    )
                    mismatches += 1

        except Exception as e:
            logger.error(f"Reconciliation error for payment {payment.uid}: {e}")

    logger.info(
        f"Payment reconciliation complete: {checked} payments checked, {mismatches} mismatches found"
    )
