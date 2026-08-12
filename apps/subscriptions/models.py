from django.conf import settings
from django.db import models
from django.utils import timezone


class Subscription(models.Model):
    STATUS_TRIALING = "trialing"
    STATUS_ACTIVE = "active"
    STATUS_PAST_DUE = "past_due"
    STATUS_GRACE_PERIOD = "grace_period"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_TRIALING, "Trialing"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_PAST_DUE, "Past Due"),
        (STATUS_GRACE_PERIOD, "Grace Period"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_EXPIRED, "Expired"),
    ]

    PROVIDER_NONE = "none"
    PROVIDER_STRIPE_BILLING = "stripe_billing"
    PROVIDER_PAYSTATION = "paystation"

    PROVIDER_CHOICES = [
        (PROVIDER_NONE, "None"),
        (PROVIDER_STRIPE_BILLING, "Stripe Billing"),
        (PROVIDER_PAYSTATION, "PayStation"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscription"
    )
    plan_code = models.CharField(max_length=30, default="free")
    provider = models.CharField(max_length=30, choices=PROVIDER_CHOICES, default=PROVIDER_NONE)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_ACTIVE)

    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    trial_ends_at = models.DateTimeField(null=True, blank=True)

    external_subscription_id = models.CharField(max_length=255, blank=True, db_index=True)
    external_customer_id = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_valid_or_active(self) -> bool:
        """Returns True if user currently has valid Pro/active entitlement."""
        if self.plan_code == "free":
            return False

        now = timezone.now()

        if self.status == self.STATUS_TRIALING:
            return not (self.trial_ends_at and now > self.trial_ends_at)

        if self.status in (self.STATUS_ACTIVE, self.STATUS_PAST_DUE, self.STATUS_GRACE_PERIOD):
            if self.current_period_end and now > self.current_period_end:
                if self.status == self.STATUS_GRACE_PERIOD:
                    # In grace period, check if grace period (7 days after period_end) expired
                    grace_end = self.current_period_end + timezone.timedelta(days=7)
                    return now <= grace_end
                elif self.status == self.STATUS_ACTIVE and self.provider == "paystation":
                    # Allow PayStation 7-day grace period from period_end
                    grace_end = self.current_period_end + timezone.timedelta(days=7)
                    return now <= grace_end
                return False
            return True

        return False

    @property
    def effective_plan_code(self) -> str:
        """Return 'free' if subscription is expired/cancelled, otherwise return plan_code."""
        if self.is_valid_or_active():
            return self.plan_code
        return "free"

    def __str__(self):
        return f"Subscription for {self.user.email}: {self.plan_code} ({self.status})"


class SubscriptionEvent(models.Model):
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=100)
    previous_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"SubscriptionEvent {self.event_type} for Sub {self.subscription_id}"
