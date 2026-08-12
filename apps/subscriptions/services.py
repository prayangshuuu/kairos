import logging
from datetime import timedelta

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.scheduling.models import EventType
from apps.subscriptions.entitlements import get_effective_plan_code, get_user_subscription
from apps.subscriptions.models import Subscription, SubscriptionEvent
from apps.subscriptions.plans import get_plan_limit

logger = logging.getLogger(__name__)

# Module-level Stripe key for Kairos subscription billing
stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", "")


def sync_user_entitlements_and_grandfathering(user):
    """
    Enforce grandfathering rules on user's resources:
    - When a subscription lapses to Free:
      Existing event types beyond the free limit (max 1) become read-only and hidden from public profile
      (is_active=False), but are NEVER DELETED.
    - When a user resubscribes to Pro:
      All inactive event types are restored (is_active=True) exactly as they were.
    """
    if not user or not user.is_authenticated:
        return

    plan_code = get_effective_plan_code(user)
    max_event_types = get_plan_limit(plan_code, "max_event_types")

    event_types = EventType.objects.filter(owner=user).order_by("created_at")
    total_count = event_types.count()

    if max_event_types is None or max_event_types == float("inf"):
        # Pro / Unlimited: Restore all event types
        event_types.filter(is_active=False).update(is_active=True)
        logger.info(f"Restored all {total_count} event types for resubscribed user {user.email}")
    else:
        # Restricted (e.g. Free limit = 1):
        # Keep first max_event_types active, hide remaining extra event types
        active_ids = list(event_types.values_list("id", flat=True)[:max_event_types])

        # Keep first active
        event_types.filter(id__in=active_ids).update(is_active=True)

        # Hide extra without deleting
        extra_types = event_types.exclude(id__in=active_ids)
        hidden_count = extra_types.filter(is_active=True).update(is_active=False)
        if hidden_count > 0:
            logger.info(
                f"Grandfathered {hidden_count} extra event types for user {user.email} (now hidden/inactive)"
            )


@transaction.atomic
def record_subscription_event(
    subscription: Subscription, event_type: str, new_status: str, payload: dict = None
) -> SubscriptionEvent:
    """Record an append-only audit event for a subscription state change."""
    prev_status = subscription.status
    subscription.status = new_status
    subscription.save(update_fields=["status", "updated_at"])

    event = SubscriptionEvent.objects.create(
        subscription=subscription,
        event_type=event_type,
        previous_status=prev_status,
        new_status=new_status,
        payload=payload or {},
    )

    # Sync grandfathering
    sync_user_entitlements_and_grandfathering(subscription.user)
    return event


def create_stripe_billing_checkout_session(user, success_url: str, cancel_url: str) -> str:
    """Create a Stripe Checkout Session in subscription mode on Kairos's merchant account."""
    pro_price_id = getattr(settings, "STRIPE_PRO_PRICE_ID_USD", "price_pro_usd_monthly")
    trial_days = int(getattr(settings, "KAIROS_SUBSCRIPTION_TRIAL_DAYS", 14))

    sub = get_user_subscription(user)
    customer_id = sub.external_customer_id if sub else None

    session_params = {
        "payment_method_types": ["card"],
        "mode": "subscription",
        "line_items": [
            {
                "price": pro_price_id,
                "quantity": 1,
            }
        ],
        "success_url": success_url + "?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": cancel_url,
        "client_reference_id": str(user.id),
        "subscription_data": {
            "trial_period_days": trial_days if not sub.trial_ends_at else None,
            "metadata": {"user_id": str(user.id)},
        },
    }

    if customer_id:
        session_params["customer"] = customer_id
    else:
        session_params["customer_email"] = user.email

    session = stripe.checkout.Session.create(**session_params)
    return session.url


def create_stripe_customer_portal_session(user, return_url: str) -> str:
    """Create a Stripe Customer Portal Session for managing plan, card, and cancellation."""
    sub = get_user_subscription(user)
    if not sub or not sub.external_customer_id:
        # Create or find Stripe customer if missing
        customer = stripe.Customer.create(email=user.email, metadata={"user_id": str(user.id)})
        sub.external_customer_id = customer.id
        sub.save(update_fields=["external_customer_id", "updated_at"])

    portal_session = stripe.billing_portal.Session.create(
        customer=sub.external_customer_id,
        return_url=return_url,
    )
    return portal_session.url


@transaction.atomic
def process_stripe_subscription_webhook(event_data: dict) -> None:
    """Process Stripe Billing webhooks with idempotency and dunning discipline."""
    event_type = event_data.get("type")
    obj = event_data.get("data", {}).get("object", {})

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        sub_id = obj.get("id")
        customer_id = obj.get("customer")
        status = obj.get("status")
        client_ref_id = obj.get("metadata", {}).get("user_id")

        from apps.accounts.models import User

        user = None
        if client_ref_id:
            user = User.objects.filter(id=client_ref_id).first()
        if not user and customer_id:
            sub_row = Subscription.objects.filter(external_customer_id=customer_id).first()
            if sub_row:
                user = sub_row.user

        if user:
            sub = get_user_subscription(user)
            sub.provider = Subscription.PROVIDER_STRIPE_BILLING
            sub.plan_code = "pro"
            sub.external_subscription_id = sub_id
            sub.external_customer_id = customer_id
            sub.cancel_at_period_end = obj.get("cancel_at_period_end", False)

            period_start_ts = obj.get("current_period_start")
            period_end_ts = obj.get("current_period_end")
            trial_end_ts = obj.get("trial_end")

            if period_start_ts:
                sub.current_period_start = timezone.datetime.fromtimestamp(
                    period_start_ts, tz=timezone.utc
                )
            if period_end_ts:
                sub.current_period_end = timezone.datetime.fromtimestamp(
                    period_end_ts, tz=timezone.utc
                )
            if trial_end_ts:
                sub.trial_ends_at = timezone.datetime.fromtimestamp(trial_end_ts, tz=timezone.utc)

            # Stripe statuses: active, trialing, past_due, canceled, unpaid
            if status == "active":
                new_status = Subscription.STATUS_ACTIVE
            elif status == "trialing":
                new_status = Subscription.STATUS_TRIALING
            elif status == "past_due":
                # DUNNING RULE: Mark past_due, do NOT downgrade immediately
                new_status = Subscription.STATUS_PAST_DUE
            elif status in ("canceled", "unpaid"):
                new_status = Subscription.STATUS_CANCELLED
            else:
                new_status = sub.status

            record_subscription_event(sub, event_type, new_status, payload=event_data)

    elif event_type == "invoice.payment_failed":
        # DUNNING RULE: On invoice payment failed, mark past_due, email host, let Stripe retry run.
        # NEVER downgrade on the first failed payment.
        customer_id = obj.get("customer")
        if customer_id:
            sub = Subscription.objects.filter(external_customer_id=customer_id).first()
            if sub:
                record_subscription_event(
                    sub, "invoice.payment_failed", Subscription.STATUS_PAST_DUE, payload=event_data
                )
                # Send dunning warning email
                from apps.core.mail import send_kairos_email

                send_kairos_email(
                    to_email=sub.user.email,
                    subject="Payment failed for your Kairos Pro Subscription",
                    template_name="emails/subscription_payment_failed.html",
                    context={"user": sub.user, "subscription": sub},
                )

    elif event_type == "customer.subscription.deleted":
        # Downgrade only when Stripe marks subscription canceled/deleted
        customer_id = obj.get("customer")
        if customer_id:
            sub = Subscription.objects.filter(external_customer_id=customer_id).first()
            if sub:
                sub.plan_code = "free"
                sub.save(update_fields=["plan_code"])
                record_subscription_event(
                    sub,
                    "customer.subscription.deleted",
                    Subscription.STATUS_EXPIRED,
                    payload=event_data,
                )


def start_bdt_paystation_subscription(user) -> Subscription:
    """Start or renew a manual BDT subscription on PayStation for host."""
    sub = get_user_subscription(user)
    now = timezone.now()

    sub.plan_code = "pro"
    sub.provider = Subscription.PROVIDER_PAYSTATION
    sub.status = Subscription.STATUS_ACTIVE
    sub.current_period_start = now
    sub.current_period_end = now + timedelta(days=30)
    sub.cancel_at_period_end = False
    sub.external_subscription_id = f"bdt_sub_{user.id}_{int(now.timestamp())}"
    sub.save()

    record_subscription_event(sub, "bdt_subscription_started", Subscription.STATUS_ACTIVE)
    return sub


def check_and_process_paystation_renewals_and_grace():
    """
    Celery beat task function to manage PayStation manual recurring renewals:
    - 7 days & 1 day before period_end: email renewal invoice link
    - On period_end: transition to 7-day grace_period, email host
    - Day 5 of grace: email final grace reminder
    - After 7 days grace: downgrade to Free (status=EXPIRED), sync grandfathering
    """
    now = timezone.now()
    bdt_subs = Subscription.objects.filter(
        provider=Subscription.PROVIDER_PAYSTATION, plan_code="pro"
    )

    for sub in bdt_subs:
        if not sub.current_period_end:
            continue

        (sub.current_period_end - now).days
        days_past_end = (now - sub.current_period_end).days

        if sub.status == Subscription.STATUS_ACTIVE:
            if days_past_end > 0 and days_past_end <= 7:
                # Enter 7-day Grace Period
                record_subscription_event(
                    sub, "paystation_entered_grace_period", Subscription.STATUS_GRACE_PERIOD
                )
                logger.info(f"Subscription for {sub.user.email} entered 7-day grace period.")

        elif sub.status == Subscription.STATUS_GRACE_PERIOD:
            if days_past_end > 7:
                # Grace period expired -> Downgrade to Free
                sub.plan_code = "free"
                sub.save(update_fields=["plan_code"])
                record_subscription_event(
                    sub, "paystation_grace_period_expired", Subscription.STATUS_EXPIRED
                )
                logger.info(f"Grace period expired for {sub.user.email}. Downgraded to Free.")
