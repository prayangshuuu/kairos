import logging
from celery import shared_task
from apps.subscriptions.services import (
    check_and_process_paystation_renewals_and_grace,
    process_stripe_subscription_webhook,
)

logger = logging.getLogger(__name__)


@shared_task
def process_paystation_subscription_reminders():
    """Celery beat task to check PayStation subscription renewals, grace period transitions, and expiration."""
    check_and_process_paystation_renewals_and_grace()


@shared_task
def process_stripe_billing_webhook(event_data: dict):
    """Celery task to asynchronously process Stripe Billing webhook events."""
    process_stripe_subscription_webhook(event_data)
