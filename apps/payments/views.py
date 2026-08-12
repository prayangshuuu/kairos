import json
import logging

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.payments.models import Payment, PaymentAccount, ProcessedWebhook
from apps.payments.providers import StripeConnectProvider
from apps.payments.services import (
    compute_fee_breakdown,
    compute_platform_fee,
    confirm_payment,
    expire_payment,
    sync_payment_account_from_stripe,
)

logger = logging.getLogger(__name__)


class StripeConnectOnboardView(LoginRequiredMixin, View):
    """Start or resume Stripe Connect Express onboarding for the logged-in host."""

    def get(self, request):
        provider = StripeConnectProvider()

        payment_account = PaymentAccount.objects.filter(
            user=request.user, provider="stripe_connect"
        ).first()

        if not payment_account:
            account_id = provider.create_connected_account(request.user)
            payment_account = PaymentAccount.objects.create(
                user=request.user,
                provider="stripe_connect",
                external_account_id=account_id,
            )
        elif not payment_account.external_account_id:
            account_id = provider.create_connected_account(request.user)
            payment_account.external_account_id = account_id
            payment_account.save(update_fields=["external_account_id", "updated_at"])

        account_id = payment_account.external_account_id

        return_url = settings.WEBHOOK_BASE_URL + reverse("payments:stripe_connect_return")
        refresh_url = settings.WEBHOOK_BASE_URL + reverse("payments:stripe_connect_refresh")

        link_url = provider.create_account_link(account_id, return_url, refresh_url)
        return redirect(link_url)


class StripeConnectReturnView(LoginRequiredMixin, View):
    """Host returns from Stripe's hosted onboarding flow.

    Onboarding is often incomplete on the first return. We sync the account
    state and show the host exactly what Stripe still needs.
    """

    def get(self, request):
        payment_account = PaymentAccount.objects.filter(
            user=request.user, provider="stripe_connect"
        ).first()

        if not payment_account or not payment_account.external_account_id:
            return redirect("payments:stripe_connect_onboard")

        # Retrieve fresh account data from Stripe and sync locally
        provider = StripeConnectProvider()
        try:
            account_data = provider.retrieve_account(payment_account.external_account_id)
            sync_payment_account_from_stripe(
                payment_account=payment_account,
                stripe_account_data=account_data,
            )
        except Exception as e:
            logger.error(f"Failed to sync account on return: {e}")

        return redirect("payments:connect_dashboard")


class StripeConnectRefreshView(LoginRequiredMixin, View):
    """The Account Link expired — regenerate and redirect.

    The refresh URL fires when the link expires. We NEVER show an error;
    we just regenerate the link and redirect immediately.
    """

    def get(self, request):
        payment_account = PaymentAccount.objects.filter(
            user=request.user, provider="stripe_connect"
        ).first()

        if not payment_account or not payment_account.external_account_id:
            return redirect("payments:stripe_connect_onboard")

        provider = StripeConnectProvider()
        return_url = settings.WEBHOOK_BASE_URL + reverse("payments:stripe_connect_return")
        refresh_url = settings.WEBHOOK_BASE_URL + reverse("payments:stripe_connect_refresh")

        link_url = provider.create_account_link(
            payment_account.external_account_id, return_url, refresh_url
        )
        return redirect(link_url)


@csrf_exempt
@require_POST
def stripe_connect_webhook(request):
    """Stripe Connect webhook endpoint.

    Verifies signature, deduplicates via ProcessedWebhook, dispatches to
    Celery tasks, and returns 200 immediately. A slow webhook endpoint gets
    disabled by Stripe.
    """
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    if not sig_header:
        logger.warning("Stripe webhook received without signature header.")
        return HttpResponseBadRequest("Missing signature")

    webhook_secret = getattr(settings, "STRIPE_CONNECT_WEBHOOK_SECRET", "")
    provider = StripeConnectProvider()

    try:
        event = provider.verify_webhook_signature(payload, sig_header, webhook_secret)
    except ValueError:
        logger.warning("Stripe webhook signature verification failed.")
        return HttpResponseBadRequest("Invalid signature")

    event_id = event.get("id", "")
    event_type = event.get("type", "")
    event.get("account", "")

    # Idempotency: Stripe retries for days, assume duplicates
    if ProcessedWebhook.objects.filter(event_id=event_id).exists():
        return HttpResponse(status=200)

    ProcessedWebhook.objects.create(
        event_id=event_id,
        provider="stripe_connect",
        event_type=event_type,
    )

    # Serialize the event data for Celery.
    # Real Stripe Event objects support dict-style access and can be converted
    # via json.dumps. Plain dicts (in tests) serialize directly.
    if hasattr(event, "to_dict_recursive"):
        event_data = event.to_dict_recursive()
    elif isinstance(event, dict):
        event_data = event
    else:
        event_data = json.loads(json.dumps(event, default=str))

    # Dispatch to Celery — return 200 fast, do the work async
    from apps.payments.tasks import (
        process_account_updated,
        process_charge_refunded,
        process_checkout_completed,
        process_checkout_expired,
        process_dispute_created,
    )

    if event_type == "checkout.session.completed":
        process_checkout_completed.delay(event_data)
    elif event_type == "checkout.session.expired":
        process_checkout_expired.delay(event_data)
    elif event_type == "charge.refunded":
        process_charge_refunded.delay(event_data)
    elif event_type == "charge.dispute.created":
        process_dispute_created.delay(event_data)
    elif event_type == "account.updated":
        process_account_updated.delay(event_data)
    else:
        logger.info(f"Unhandled Stripe webhook event type: {event_type}")

    return HttpResponse(status=200)


class PaymentReturnView(View):
    """Customer returns from Stripe Checkout or PayStation (success/failure URL).

    CRITICAL: The webhook frequently arrives BEFORE this redirect. Both paths
    converge on the same idempotent confirm_payment() function. Whichever
    arrives second is a no-op.
    """

    def get(self, request):
        session_id = request.GET.get("session_id", "")
        invoice_number = request.GET.get("invoice_number", "")

        payment = None
        if session_id:
            payment = Payment.objects.filter(external_session_id=session_id).first()
        if not payment and invoice_number:
            payment = Payment.objects.filter(invoice_number=invoice_number).first()

        if not payment:
            logger.warning(
                f"No payment found for session_id={session_id}, invoice_number={invoice_number}"
            )
            return HttpResponseBadRequest("Payment not found")

        status_param = request.GET.get("status", "").lower()
        message_param = request.GET.get("message", "")

        # Check if payment failed or was cancelled
        if (
            status_param in ["failed", "cancel", "cancelled", "error"]
            or "invalid" in message_param.lower()
            or "fail" in message_param.lower()
        ):
            logger.info(
                f"Payment {payment.uid} failed/cancelled via return URL: status={status_param}, msg={message_param}"
            )
            if payment.status == Payment.STATUS_PENDING:
                expire_payment(payment_uid=str(payment.uid))

            host_slug = payment.booking.event_type.owner.slug
            event_slug = payment.booking.event_type.slug
            return redirect(
                f"/{host_slug}/{event_slug}/?payment_error={message_param or 'Payment was not completed'}"
            )

        confirm_payment(payment_uid=str(payment.uid))

        # Redirect to the booking confirmation page
        from apps.bookings.tokens import make_manage_token

        token = make_manage_token(payment.booking)
        return redirect(f"/booking/{payment.booking.uid}/?t={token}")


class PaymentCancelView(View):
    """Customer cancelled checkout."""

    def get(self, request):
        payment_uid = request.GET.get("payment_uid", "")
        invoice_number = request.GET.get("invoice_number", "")

        payment = None
        if payment_uid:
            payment = Payment.objects.filter(uid=payment_uid).first()
        if not payment and invoice_number:
            payment = Payment.objects.filter(invoice_number=invoice_number).first()

        if not payment:
            return redirect("/")

        if payment.status == Payment.STATUS_PENDING:
            expire_payment(payment_uid=str(payment.uid))

        return redirect(
            f"/{payment.booking.event_type.owner.slug}/{payment.booking.event_type.slug}/?payment_cancelled=1"
        )


class ConnectDashboardView(LoginRequiredMixin, View):
    """Dashboard showing Stripe Connect status for the host.

    Shows connection status, capabilities, outstanding requirements,
    and a link to the Stripe Express dashboard. We do NOT rebuild payout
    reporting — Stripe already gives them that.
    """

    def get(self, request):
        payment_account = PaymentAccount.objects.filter(
            user=request.user, provider="stripe_connect"
        ).first()

        paystation_account = PaymentAccount.objects.filter(
            user=request.user, provider="paystation"
        ).first()

        express_dashboard_url = None

        if (
            payment_account
            and payment_account.external_account_id
            and payment_account.charges_enabled
        ):
            provider = StripeConnectProvider()
            try:
                express_dashboard_url = provider.create_express_login_link(
                    payment_account.external_account_id
                )
            except Exception as e:
                logger.error(f"Failed to create Express dashboard link: {e}")

        from apps.payments.models import HostPaymentTerms

        has_accepted_terms = HostPaymentTerms.objects.filter(user=request.user).exists()

        context = {
            "account": payment_account,
            "paystation_account": paystation_account,
            "express_dashboard_url": express_dashboard_url,
            "enable_paystation": getattr(settings, "KAIROS_ENABLE_PAYSTATION_ROUTE", True),
            "platform_fee_percent": getattr(settings, "KAIROS_PLATFORM_FEE_PERCENT", 5.0),
            "has_accepted_terms": has_accepted_terms,
        }
        return render(request, "payments/connect_dashboard.html", context)


class FeeCalculatorView(LoginRequiredMixin, View):
    """HTMX endpoint for live fee calculation in the event type editor.

    Displays: "You receive ৳X, Kairos fee ৳Y, Stripe fee ~৳Z"
    """

    def get(self, request):
        try:
            amount_cents = int(request.GET.get("amount_cents", 0))
        except (ValueError, TypeError):
            amount_cents = 0

        currency = request.GET.get("currency", "USD").upper()

        if amount_cents <= 0:
            return render(request, "payments/partials/fee_breakdown.html", {"breakdown": None})

        fee_data = compute_platform_fee(amount_cents)
        breakdown = compute_fee_breakdown(amount_cents, fee_data["fee_amount_cents"], currency)

        return render(request, "payments/partials/fee_breakdown.html", {"breakdown": breakdown})


class EnablePaystationView(LoginRequiredMixin, View):
    """Enable Paystation route for the host after accepting terms."""

    def post(self, request):
        if not getattr(settings, "KAIROS_ENABLE_PAYSTATION_ROUTE", True):
            return HttpResponseBadRequest("Paystation is not enabled.")

        from apps.payments.models import HostPaymentTerms

        # Get client IP
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0].strip()
        else:
            ip = request.META.get("REMOTE_ADDR")

        # Record terms acceptance
        HostPaymentTerms.objects.get_or_create(
            user=request.user, terms_version="1.0", defaults={"ip_address": ip}
        )

        PaymentAccount.objects.get_or_create(
            user=request.user,
            provider="paystation",
            defaults={
                "external_account_id": f"internal_{request.user.id}",
                "charges_enabled": True,
                "payouts_enabled": True,
                "default_currency": "BDT",
                "country": "BD",
                "is_active": True,
            },
        )
        return redirect("payments:connect_dashboard")


class HostLedgerView(LoginRequiredMixin, View):
    """View statement/ledger for PayStation route."""

    def get(self, request):
        if not getattr(settings, "KAIROS_ENABLE_PAYSTATION_ROUTE", True):
            return redirect("payments:connect_dashboard")

        from django.db.models import Sum

        from apps.payments.models import HostLedger, Payout

        entries = HostLedger.objects.filter(host=request.user).order_by("-created_at")
        balance = entries.aggregate(Sum("amount_cents"))["amount_cents__sum"] or 0
        payouts = Payout.objects.filter(host=request.user).order_by("-initiated_at")

        context = {
            "entries": entries,
            "balance": balance,
            "payouts": payouts,
            "min_threshold_cents": getattr(
                settings, "PAYSTATION_MIN_PAYOUT_THRESHOLD_CENTS", 100000
            ),
        }
        return render(request, "payments/host_ledger.html", context)


class GeneratePayoutView(LoginRequiredMixin, View):
    """Host/Admin triggers payout generation for a period."""

    def post(self, request):
        from datetime import timedelta

        from django.contrib import messages

        from apps.payments.services import generate_payout

        period_end = timezone.now()
        period_start = period_end - timedelta(days=30)

        try:
            payout = generate_payout(
                host=request.user, period_start=period_start, period_end=period_end
            )
            if payout:
                messages.success(
                    request, f"Payout #{payout.id} generated for ৳{payout.net_cents / 100:.2f}."
                )
            else:
                messages.error(request, "No positive balance available for payout.")
        except ValueError as e:
            messages.error(request, str(e))

        return redirect("payments:host_ledger")
