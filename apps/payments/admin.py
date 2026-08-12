from django.contrib import admin

from apps.payments.models import (
    Payment,
    PaymentAccount,
    ProcessedWebhook,
    ReconciliationFlag,
    SlotHold,
)


@admin.register(PaymentAccount)
class PaymentAccountAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'provider', 'external_account_id', 'charges_enabled',
        'payouts_enabled', 'country', 'default_currency', 'is_active',
        'onboarding_completed_at',
    ]
    list_filter = ['provider', 'charges_enabled', 'payouts_enabled', 'is_active', 'country']
    search_fields = ['user__email', 'external_account_id']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = [
        'uid', 'invoice_number', 'provider', 'amount_cents', 'currency',
        'status', 'fee_amount_cents', 'refund_amount_cents', 'created_at',
    ]
    list_filter = ['status', 'provider', 'currency']
    search_fields = [
        'uid', 'invoice_number', 'external_session_id',
        'external_payment_intent_id', 'external_charge_id',
        'booking__uid',
    ]
    readonly_fields = [
        'uid', 'created_at', 'updated_at', 'fee_percent_applied',
        'fee_fixed_applied', 'fee_amount_cents',
    ]
    raw_id_fields = ['booking', 'payment_account']


@admin.register(SlotHold)
class SlotHoldAdmin(admin.ModelAdmin):
    list_display = ['uid', 'booking', 'payment', 'expires_at', 'is_released', 'release_reason']
    list_filter = ['is_released', 'release_reason']
    readonly_fields = ['uid', 'created_at']
    raw_id_fields = ['booking', 'payment']


@admin.register(ProcessedWebhook)
class ProcessedWebhookAdmin(admin.ModelAdmin):
    list_display = ['event_id', 'provider', 'event_type', 'processed_at']
    list_filter = ['provider', 'event_type']
    search_fields = ['event_id']
    readonly_fields = ['processed_at']


@admin.register(ReconciliationFlag)
class ReconciliationFlagAdmin(admin.ModelAdmin):
    list_display = ['payment', 'flag_type', 'resolved', 'created_at']
    list_filter = ['flag_type', 'resolved']
    search_fields = ['payment__uid', 'payment__invoice_number']
    readonly_fields = ['created_at']
    raw_id_fields = ['payment', 'resolved_by']
