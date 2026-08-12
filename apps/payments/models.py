import uuid

from django.conf import settings
from django.db import models


class PaymentAccount(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_accounts"
    )
    provider = models.CharField(max_length=30, default="stripe_connect")
    external_account_id = models.CharField(max_length=255, unique=True, db_index=True)
    charges_enabled = models.BooleanField(default=False)
    payouts_enabled = models.BooleanField(default=False)
    details_submitted = models.BooleanField(default=False)
    requirements_due = models.JSONField(default=list, blank=True)
    default_currency = models.CharField(max_length=3, blank=True)
    country = models.CharField(max_length=2, blank=True)
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "provider"], name="unique_payment_account_per_user_provider"
            )
        ]

    def __str__(self):
        return f"{self.user.email} ({self.provider}:{self.external_account_id})"


class Payment(models.Model):
    STATUS_PENDING = "PENDING"
    STATUS_COMPLETED = "COMPLETED"
    STATUS_FAILED = "FAILED"
    STATUS_REFUNDED = "REFUNDED"
    STATUS_PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    STATUS_DISPUTED = "DISPUTED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_REFUNDED, "Refunded"),
        (STATUS_PARTIALLY_REFUNDED, "Partially Refunded"),
        (STATUS_DISPUTED, "Disputed"),
    ]

    uid = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    booking = models.ForeignKey(
        "bookings.Booking", on_delete=models.PROTECT, related_name="payments"
    )
    payment_account = models.ForeignKey(
        PaymentAccount, on_delete=models.PROTECT, null=True, blank=True
    )
    provider = models.CharField(max_length=30)
    invoice_number = models.CharField(max_length=100, unique=True, db_index=True)
    amount_cents = models.PositiveIntegerField()
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING)
    external_session_id = models.CharField(max_length=255, blank=True, db_index=True)
    external_payment_intent_id = models.CharField(max_length=255, blank=True)
    external_charge_id = models.CharField(max_length=255, blank=True)
    fee_percent_applied = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    fee_fixed_applied = models.PositiveIntegerField(default=0)
    fee_amount_cents = models.PositiveIntegerField(default=0)
    gateway_fee_cents = models.PositiveIntegerField(default=0)
    net_owed_cents = models.IntegerField(default=0)
    refund_amount_cents = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Payment {self.uid} ({self.status})"


class SlotHold(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, unique=True)
    booking = models.OneToOneField(
        "bookings.Booking", on_delete=models.CASCADE, related_name="slot_hold"
    )
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="slot_holds")
    expires_at = models.DateTimeField(db_index=True)
    is_released = models.BooleanField(default=False)
    released_at = models.DateTimeField(null=True, blank=True)
    release_reason = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Hold {self.uid} for booking {self.booking_id}"


class ProcessedWebhook(models.Model):
    event_id = models.CharField(max_length=255, unique=True, db_index=True)
    provider = models.CharField(max_length=30)
    event_type = models.CharField(max_length=100)
    processed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.provider}:{self.event_id}"


class ReconciliationFlag(models.Model):
    payment = models.ForeignKey(
        Payment, on_delete=models.CASCADE, related_name="reconciliation_flags"
    )
    flag_type = models.CharField(max_length=50)
    description = models.TextField()
    local_state = models.JSONField(default=dict)
    remote_state = models.JSONField(default=dict)
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Reconciliation: {self.flag_type} for Payment {self.payment.uid}"


# IMPORTANT NOTICE REGARDING PAYSTATION FALLBACK ROUTE:
# Operating this PayStation fallback route at scale involves money-transmission and regulatory licensing
# questions because Kairos collects funds into its own merchant account on behalf of hosts.
# Before operating this route in production, the legal position must be confirmed with a qualified professional.
# This codebase assumes, but does not establish, that this regulatory position is settled.


class HostPaymentTerms(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_terms_accepted"
    )
    terms_version = models.CharField(max_length=20, default="1.0")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "terms_version"], name="unique_host_terms_version"
            )
        ]

    def __str__(self):
        return f"{self.user.email} accepted terms v{self.terms_version} on {self.accepted_at}"


class Payout(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
    ]

    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payouts"
    )
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    gross_cents = models.PositiveIntegerField(default=0)
    fees_cents = models.PositiveIntegerField(default=0)
    net_cents = models.IntegerField(default=0)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="PENDING")
    reference = models.CharField(max_length=100, blank=True)
    initiated_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Payout {self.id} for {self.host.email} ({self.status})"


class HostLedger(models.Model):
    ENTRY_TYPES = [
        ("charge", "Charge"),
        ("service_fee", "Service Fee"),
        ("platform_fee", "Platform Fee"),
        ("gateway_fee", "Gateway Fee"),
        ("refund", "Refund"),
        ("refund_fee_reversal", "Fee Reversal"),
        ("payout", "Payout"),
        ("adjustment", "Adjustment"),
    ]

    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ledger_entries"
    )
    payment = models.ForeignKey(
        "Payment", on_delete=models.PROTECT, null=True, blank=True, related_name="ledger_entries"
    )
    payout = models.ForeignKey(
        Payout, on_delete=models.PROTECT, null=True, blank=True, related_name="ledger_entries"
    )
    entry_type = models.CharField(max_length=30, choices=ENTRY_TYPES)
    amount_cents = (
        models.IntegerField()
    )  # Negative for fees, payouts, and refunds; positive for charges and fee reversals
    currency = models.CharField(max_length=3, default="BDT")
    description = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("HostLedger is append-only. Existing entries cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("HostLedger is append-only. Existing entries cannot be deleted.")

    def __str__(self):
        return f"{self.entry_type} {self.amount_cents} {self.currency} (Host: {self.host.email})"
