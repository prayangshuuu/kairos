from django.contrib import admin

from apps.payments.models import (
    HostLedger,
    Payment,
    PaymentAccount,
    Payout,
    PayoutMethod,
    PayoutRequest,
    ProcessedWebhook,
    ReconciliationFlag,
    SlotHold,
    WalletReconciliationLog,
)


@admin.register(PaymentAccount)
class PaymentAccountAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "provider",
        "external_account_id",
        "charges_enabled",
        "payouts_enabled",
        "is_active",
    )
    list_filter = ("provider", "charges_enabled", "is_active", "country")
    search_fields = ("user__email", "external_account_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "uid",
        "booking",
        "provider",
        "amount_cents",
        "currency",
        "status",
        "is_settled",
        "created_at",
    )
    list_filter = ("provider", "status", "is_settled", "currency", "created_at")
    search_fields = (
        "uid",
        "booking__uid",
        "invoice_number",
        "external_session_id",
        "external_payment_intent_id",
    )
    readonly_fields = (
        "uid",
        "invoice_number",
        "fee_amount_cents",
        "gateway_fee_cents",
        "net_owed_cents",
        "created_at",
        "updated_at",
    )
    actions = ["process_manual_refund"]

    @admin.action(description="Process manual refund for selected payments")
    def process_manual_refund(self, request, queryset):
        from apps.payments.services import handle_refund

        count = 0
        for payment in queryset:
            if payment.status in [Payment.STATUS_COMPLETED, Payment.STATUS_PARTIALLY_REFUNDED]:
                handle_refund(payment=payment)
                count += 1
        self.message_user(request, f"Processed manual refund for {count} payment(s).")


@admin.register(SlotHold)
class SlotHoldAdmin(admin.ModelAdmin):
    list_display = ("uid", "booking", "expires_at", "is_released", "release_reason")
    list_filter = ("is_released", "release_reason")
    search_fields = ("uid", "booking__uid")
    readonly_fields = ("uid", "created_at")


@admin.register(ProcessedWebhook)
class ProcessedWebhookAdmin(admin.ModelAdmin):
    list_display = ("event_id", "provider", "event_type", "processed_at")
    list_filter = ("provider", "event_type")
    search_fields = ("event_id",)
    readonly_fields = ("processed_at",)


@admin.register(ReconciliationFlag)
class ReconciliationFlagAdmin(admin.ModelAdmin):
    list_display = ("payment", "flag_type", "resolved", "created_at")
    list_filter = ("flag_type", "resolved")
    search_fields = ("payment__uid",)
    readonly_fields = ("created_at",)


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "host",
        "period_start",
        "period_end",
        "net_cents",
        "status",
        "initiated_at",
    )
    list_filter = ("status",)
    search_fields = ("host__email", "reference")
    readonly_fields = ("initiated_at", "completed_at")


@admin.register(PayoutMethod)
class PayoutMethodAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "host",
        "method_type",
        "account_name",
        "masked_account_info",
        "is_verified",
        "is_default",
        "created_at",
    )
    list_filter = ("method_type", "is_verified", "is_default")
    search_fields = ("host__email", "account_name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PayoutRequest)
class PayoutRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "host",
        "amount_cents",
        "currency",
        "method",
        "status",
        "requested_at",
        "reference",
    )
    list_filter = ("status", "currency", "requested_at")
    search_fields = ("host__email", "reference", "notes", "rejection_reason")
    readonly_fields = ("requested_at", "processed_at")


@admin.register(HostLedger)
class HostLedgerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "host",
        "entry_type",
        "provider",
        "is_custodial",
        "amount_cents",
        "currency",
        "description",
        "created_at",
    )
    list_filter = ("entry_type", "provider", "is_custodial", "currency", "created_at")
    search_fields = ("host__email", "description")
    readonly_fields = (
        "host",
        "payment",
        "payout_request",
        "payout",
        "entry_type",
        "provider",
        "is_custodial",
        "amount_cents",
        "currency",
        "description",
        "created_by",
        "created_at",
    )

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WalletReconciliationLog)
class WalletReconciliationLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "reconciled_at",
        "total_ledger_cents",
        "expected_merchant_hold_cents",
        "difference_cents",
        "is_clean",
    )
    list_filter = ("is_clean", "reconciled_at")
    readonly_fields = (
        "reconciled_at",
        "total_ledger_cents",
        "expected_merchant_hold_cents",
        "difference_cents",
        "is_clean",
        "details",
    )
