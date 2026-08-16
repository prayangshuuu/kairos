import uuid

from django.conf import settings
from django.db import models

from apps.integrations.models import DedicatedKeyEncryptedTextField


class PaymentAccount(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_accounts", null=True, blank=True
    )
    team = models.ForeignKey(
        "teams.Team", on_delete=models.CASCADE, related_name="payment_accounts", null=True, blank=True
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
                fields=["user", "provider"], condition=models.Q(team__isnull=True), name="unique_payment_account_per_user_provider"
            ),
            models.UniqueConstraint(
                fields=["team", "provider"], condition=models.Q(user__isnull=True), name="unique_payment_account_per_team_provider"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(user__isnull=False, team__isnull=True)
                    | models.Q(user__isnull=True, team__isnull=False)
                ),
                name="payment_account_user_or_team",
            )
        ]

    def __str__(self):
        owner = self.user.email if self.user else self.team.name
        return f"{owner} ({self.provider}:{self.external_account_id})"


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
    is_settled = models.BooleanField(default=False, db_index=True)
    settled_at = models.DateTimeField(null=True, blank=True)
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
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_terms_accepted", null=True, blank=True
    )
    team = models.ForeignKey(
        "teams.Team", on_delete=models.CASCADE, related_name="payment_terms_accepted", null=True, blank=True
    )
    terms_version = models.CharField(max_length=20, default="1.0")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "terms_version"], condition=models.Q(team__isnull=True), name="unique_host_terms_version"
            ),
            models.UniqueConstraint(
                fields=["team", "terms_version"], condition=models.Q(user__isnull=True), name="unique_team_terms_version"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(user__isnull=False, team__isnull=True)
                    | models.Q(user__isnull=True, team__isnull=False)
                ),
                name="payment_terms_host_or_team",
            )
        ]

    def __str__(self):
        owner = self.user.email if self.user else self.team.name
        return f"{owner} accepted terms v{self.terms_version} on {self.accepted_at}"


class Payout(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
    ]

    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payouts", null=True, blank=True
    )
    team = models.ForeignKey(
        "teams.Team", on_delete=models.CASCADE, related_name="payouts", null=True, blank=True
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
    
    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(host__isnull=False, team__isnull=True)
                    | models.Q(host__isnull=True, team__isnull=False)
                ),
                name="payout_host_or_team",
            )
        ]

    def __str__(self):
        owner = self.host.email if self.host else self.team.name
        return f"Payout {self.id} for {owner} ({self.status})"


class PayoutMethod(models.Model):
    METHOD_BKASH = "bkash"
    METHOD_NAGAD = "nagad"
    METHOD_BANK = "bank_transfer"

    METHOD_CHOICES = [
        (METHOD_BKASH, "bKash"),
        (METHOD_NAGAD, "Nagad"),
        (METHOD_BANK, "Bank Transfer"),
    ]

    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payout_methods", null=True, blank=True
    )
    team = models.ForeignKey(
        "teams.Team", on_delete=models.CASCADE, related_name="payout_methods", null=True, blank=True
    )
    method_type = models.CharField(max_length=30, choices=METHOD_CHOICES)
    account_name = models.CharField(max_length=100)
    encrypted_account_details = models.TextField()
    is_verified = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(host__isnull=False, team__isnull=True)
                    | models.Q(host__isnull=True, team__isnull=False)
                ),
                name="payout_method_host_or_team",
            )
        ]

    def set_details(self, details_dict: dict):
        from apps.payments.wallet import encrypt_payload
        self.encrypted_account_details = encrypt_payload(details_dict)

    def get_details(self) -> dict:
        from apps.payments.wallet import decrypt_payload
        return decrypt_payload(self.encrypted_account_details)

    @property
    def masked_account_info(self) -> str:
        try:
            details = self.get_details()
        except Exception:
            return "Encrypted Details"

        if self.method_type in [self.METHOD_BKASH, self.METHOD_NAGAD]:
            mobile = details.get("mobile_number", "")
            if len(mobile) >= 8:
                return f"{mobile[:3]}****{mobile[-3:]}"
            return mobile
        else:
            acc = details.get("account_number", "")
            bank = details.get("bank_name", "")
            if len(acc) >= 4:
                return f"{bank} (*{acc[-4:]})"
            return f"{bank} ({acc})"

    def __str__(self):
        owner = self.host.email if self.host else self.team.name
        return f"{self.get_method_type_display()} for {owner} - {self.account_name} ({self.masked_account_info})"


class PayoutRequest(models.Model):
    STATUS_REQUESTED = "requested"
    STATUS_APPROVED = "approved"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_REQUESTED, "Requested"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payout_requests", null=True, blank=True
    )
    team = models.ForeignKey(
        "teams.Team", on_delete=models.PROTECT, related_name="payout_requests", null=True, blank=True
    )
    amount_cents = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="BDT")
    method = models.ForeignKey(
        PayoutMethod, on_delete=models.PROTECT, related_name="payout_requests"
    )
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default=STATUS_REQUESTED, db_index=True
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="processed_payout_requests",
    )
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(host__isnull=False, team__isnull=True)
                    | models.Q(host__isnull=True, team__isnull=False)
                ),
                name="payout_request_host_or_team",
            )
        ]

    def __str__(self):
        owner = self.host.email if self.host else self.team.name
        return f"PayoutRequest #{self.id} - {owner} - ৳{self.amount_cents / 100:.2f} ({self.status})"


class HostLedger(models.Model):
    ENTRY_TYPES = [
        ("charge", "Charge"),
        ("service_fee", "Service Fee"),
        ("platform_fee", "Platform Fee"),
        ("gateway_fee", "Gateway Fee"),
        ("refund", "Refund"),
        ("refund_fee_reversal", "Fee Reversal"),
        ("payout", "Payout"),
        ("payout_reversal", "Payout Reversal"),
        ("adjustment", "Adjustment"),
    ]

    PROVIDER_PAYSTATION = "paystation"
    PROVIDER_STRIPE = "stripe"
    PROVIDER_CHOICES = [
        (PROVIDER_PAYSTATION, "PayStation"),
        (PROVIDER_STRIPE, "Stripe Connect"),
    ]

    host = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ledger_entries", null=True, blank=True
    )
    team = models.ForeignKey(
        "teams.Team", on_delete=models.PROTECT, related_name="ledger_entries", null=True, blank=True
    )
    payment = models.ForeignKey(
        Payment, on_delete=models.PROTECT, null=True, blank=True, related_name="ledger_entries"
    )
    payout_request = models.ForeignKey(
        PayoutRequest, on_delete=models.PROTECT, null=True, blank=True, related_name="ledger_entries"
    )
    payout = models.ForeignKey(
        Payout, on_delete=models.PROTECT, null=True, blank=True, related_name="ledger_entries"
    )
    entry_type = models.CharField(max_length=30, choices=ENTRY_TYPES)
    # provider: determines whether this entry contributes to the withdrawable balance.
    # PayStation entries are custodial — Kairos holds the funds.
    # Stripe entries are informational ONLY — Kairos never held these funds.
    provider = models.CharField(
        max_length=30,
        choices=PROVIDER_CHOICES,
        default=PROVIDER_PAYSTATION,
        db_index=True,
    )
    # is_custodial: denormalised copy of (provider == "paystation") for fast filtering.
    # True only for PayStation. Never sum Stripe (is_custodial=False) entries into a balance.
    is_custodial = models.BooleanField(default=True, db_index=True)
    amount_cents = (
        models.IntegerField()
    )  # SIGNED IntegerField: Negative for fees, payouts, refunds; positive for charges, reversals
    currency = models.CharField(max_length=3, default="BDT")
    description = models.CharField(max_length=255)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_ledger_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(host__isnull=False, team__isnull=True)
                    | models.Q(host__isnull=True, team__isnull=False)
                ),
                name="host_ledger_host_or_team",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("HostLedger is append-only. Existing entries cannot be updated.")
        # Enforce is_custodial consistency with provider before first save.
        self.is_custodial = (self.provider == self.PROVIDER_PAYSTATION)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("HostLedger is append-only. Existing entries cannot be deleted.")

    def __str__(self):
        owner = self.host.email if self.host else self.team.name
        return f"{self.entry_type} {self.amount_cents} {self.currency} [{self.provider}] (Owner: {owner})"


class WalletReconciliationLog(models.Model):
    reconciled_at = models.DateTimeField(auto_now_add=True)
    total_ledger_cents = models.IntegerField()
    expected_merchant_hold_cents = models.IntegerField()
    difference_cents = models.IntegerField()
    is_clean = models.BooleanField()
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-reconciled_at"]

    def __str__(self):
        status_str = "CLEAN" if self.is_clean else "MISMATCH"
        return f"Reconciliation {self.reconciled_at.strftime('%Y-%m-%d %H:%M')}: {status_str} (Diff: {self.difference_cents} cents)"


class PlatformStripeSettings(models.Model):
    """
    Singleton (pk=1) holding this self-hosted instance's own Stripe Connect platform
    credentials. Every Kairos deployment has a different Stripe platform account, so
    these live in the DB (editable by staff in-app) instead of a shared .env file.
    """

    secret_key = DedicatedKeyEncryptedTextField(blank=True, null=True)
    publishable_key = models.CharField(max_length=255, blank=True, default="")
    webhook_secret = DedicatedKeyEncryptedTextField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls) -> "PlatformStripeSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def is_configured(self) -> bool:
        return bool(self.secret_key)

    @staticmethod
    def _mask(value: str | None) -> str:
        if not value:
            return ""
        tail = value[-4:] if len(value) > 4 else value
        return f"{'•' * 8}{tail}"

    @property
    def masked_secret_key(self) -> str:
        return self._mask(self.secret_key)

    @property
    def masked_webhook_secret(self) -> str:
        return self._mask(self.webhook_secret)

    def __str__(self):
        return "Platform Stripe Settings"

