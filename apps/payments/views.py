import csv
import json
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import models
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.payments.models import (
    HostLedger,
    HostPaymentTerms,
    Payment,
    PaymentAccount,
    PayoutMethod,
    PayoutRequest,
    ProcessedWebhook,
)
from apps.payments.providers import StripeConnectProvider
from apps.payments.services import (
    compute_fee_breakdown,
    compute_platform_fee,
    confirm_payment,
    expire_payment,
    sync_payment_account_from_stripe,
)
from apps.payments.wallet import (
    approve_payout,
    complete_payout,
    get_available_balance,
    get_host_wallet_mode,
    get_pending_balance,
    get_total_balance,
    reject_payout,
    request_payout,
)

logger = logging.getLogger(__name__)


class StaffRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_staff


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
    """Host returns from Stripe's hosted onboarding flow."""

    def get(self, request):
        payment_account = PaymentAccount.objects.filter(
            user=request.user, provider="stripe_connect"
        ).first()

        if not payment_account or not payment_account.external_account_id:
            return redirect("payments:stripe_connect_onboard")

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
    """The Account Link expired — regenerate and redirect."""

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
    """Handle Stripe Connect webhooks."""
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE")
    provider = StripeConnectProvider()
    webhook_secret = getattr(settings, "STRIPE_CONNECT_WEBHOOK_SECRET", "")
    try:
        if webhook_secret:
            event = provider.verify_webhook_signature(payload, sig_header, webhook_secret)
        else:
            event = provider.verify_webhook_signature(payload, sig_header)
    except ValueError:
        return HttpResponse(status=400)
    except Exception as e:
        logger.error(f"Webhook signature error: {e}")
        return HttpResponse(status=400)

    event_id = str(event.get("id", ""))
    event_type = str(event.get("type", ""))

    if ProcessedWebhook.objects.filter(event_id=event_id).exists():
        return HttpResponse(status=200)

    ProcessedWebhook.objects.create(
        event_id=event_id,
        provider="stripe_connect",
        event_type=event_type,
    )

    if event_type == "checkout.session.completed":
        from apps.payments.tasks import process_checkout_completed
        process_checkout_completed.delay(event)
    elif event_type == "checkout.session.expired":
        from apps.payments.tasks import process_checkout_expired
        process_checkout_expired.delay(event)

    return HttpResponse(status=200)


class PaymentReturnView(View):
    """Invitee returns after completing Stripe Checkout or PayStation."""

    def get(self, request):
        payment_uid = request.GET.get("payment_uid")
        if payment_uid:
            try:
                payment = confirm_payment(payment_uid=payment_uid)
                return redirect("bookings:booking_detail", uid=payment.booking.uid)
            except Exception as e:
                logger.error(f"Error confirming payment on return: {e}")

        return redirect("dashboard")


class PaymentCancelView(View):
    """Invitee cancelled during Stripe Checkout or PayStation."""

    def get(self, request):
        payment_uid = request.GET.get("payment_uid")
        if payment_uid:
            try:
                payment = expire_payment(payment_uid=payment_uid)
                return redirect("bookings:public_booking", slug=payment.booking.event_type.slug)
            except Exception as e:
                logger.error(f"Error expiring payment on cancel: {e}")

        return redirect("dashboard")


class ConnectDashboardView(LoginRequiredMixin, View):
    """Host payment settings dashboard."""

    def get(self, request):
        payment_account = PaymentAccount.objects.filter(
            user=request.user, provider="stripe_connect"
        ).first()

        terms_accepted = HostPaymentTerms.objects.filter(
            user=request.user, terms_version="1.0"
        ).exists()

        paystation_account = PaymentAccount.objects.filter(
            user=request.user, provider="paystation", is_active=True
        ).first()

        context = {
            "payment_account": payment_account,
            "paystation_account": paystation_account,
            "terms_accepted": terms_accepted,
            "paystation_enabled": getattr(settings, "KAIROS_ENABLE_PAYSTATION_ROUTE", True),
        }
        return render(request, "payments/connect_dashboard.html", context)


class EnablePaystationView(LoginRequiredMixin, View):
    """Host accepts terms to enable the PayStation fallback route."""

    def post(self, request):
        ip = request.META.get("REMOTE_ADDR")
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
        messages.success(request, "PayStation payment route enabled successfully.")
        return redirect("payments:connect_dashboard")


class FeeCalculatorView(View):
    """HTMX endpoint to dynamically calculate fee breakdown for a price."""

    def get(self, request):
        try:
            amount_cents = int(request.GET.get("amount_cents", 0))
            is_paystation = request.GET.get("route") == "paystation"
            breakdown = compute_fee_breakdown(
                amount_cents=amount_cents, is_paystation_route=is_paystation
            )
            return render(request, "payments/partials/fee_breakdown.html", {"breakdown": breakdown})
        except ValueError:
            return HttpResponseBadRequest("Invalid amount")


# ==============================================================================
# TASK 39: HOST WALLET & MANUAL PAYOUTS VIEWS
# ==============================================================================


class HostWalletView(LoginRequiredMixin, View):
    """Host Wallet Dashboard: balances, payout methods, request form, full ledger statement, payout history."""

    def get(self, request):
        user = request.user

        # Determine wallet mode before computing balances.
        wallet_mode = get_host_wallet_mode(user)
        has_custodial = wallet_mode in ("custodial", "mixed")
        has_non_custodial = wallet_mode in ("non_custodial", "mixed")

        # Balance figures are ONLY meaningful for custodial (PayStation) entries.
        # For Stripe-only hosts these will correctly be 0 — Kairos holds ৳0.
        avail_cents = get_available_balance(user) if has_custodial else 0
        pending_cents = get_pending_balance(user) if has_custodial else 0
        total_cents = get_total_balance(user) if has_custodial else 0

        # Retrieve all ledger entries in chronological order for statement.
        # For mixed hosts the list contains both custodial and non-custodial rows.
        raw_entries = HostLedger.objects.filter(host=user).order_by("created_at", "id")
        running = 0
        statement_rows = []
        for entry in raw_entries:
            # Running balance tracks custodial entries only — non-custodial rows
            # do not affect the running balance but are still shown for bookkeeping.
            if entry.is_custodial:
                running += entry.amount_cents
            statement_rows.append(
                {
                    "entry": entry,
                    # running_balance_cents reflects custodial funds only.
                    "running_balance_cents": running if entry.is_custodial else None,
                }
            )

        # Reverse for display (latest first)
        statement_rows.reverse()

        payout_methods = PayoutMethod.objects.filter(host=user)
        payout_requests = PayoutRequest.objects.filter(host=user).order_by("-requested_at")

        min_threshold_cents = getattr(settings, "KAIROS_MINIMUM_PAYOUT_CENTS", 1000)

        # Stripe Express dashboard link for non-custodial / mixed hosts
        stripe_dashboard_url = getattr(
            settings, "STRIPE_EXPRESS_DASHBOARD_URL",
            "https://connect.stripe.com/express_login"
        )

        context = {
            # Wallet mode
            "wallet_mode": wallet_mode,
            "has_custodial": has_custodial,
            "has_non_custodial": has_non_custodial,
            # Custodial balances (only meaningful when has_custodial=True)
            "available_cents": avail_cents,
            "pending_cents": pending_cents,
            "total_cents": total_cents,
            "available_bdt": f"{avail_cents / 100:.2f}",
            "pending_bdt": f"{pending_cents / 100:.2f}",
            "total_bdt": f"{total_cents / 100:.2f}",
            # Statement
            "statement_rows": statement_rows,
            # Payout methods and requests (custodial section only)
            "payout_methods": payout_methods,
            "payout_requests": payout_requests,
            "min_threshold_cents": min_threshold_cents,
            "min_threshold_bdt": f"{min_threshold_cents / 100:.2f}",
            "is_negative": (avail_cents < 0 or total_cents < 0),
            # Stripe dashboard link for non-custodial hosts
            "stripe_dashboard_url": stripe_dashboard_url,
        }
        return render(request, "payments/wallet.html", context)


class AddPayoutMethodView(LoginRequiredMixin, View):
    """Host adds a bKash, Nagad, or Bank Transfer payout method."""

    def post(self, request):
        method_type = request.POST.get("method_type")
        account_name = request.POST.get("account_name", "").strip()

        if not method_type or not account_name:
            messages.error(request, "Please provide account name and select method type.")
            return redirect("payments:wallet")

        details = {}
        if method_type in [PayoutMethod.METHOD_BKASH, PayoutMethod.METHOD_NAGAD]:
            mobile = request.POST.get("mobile_number", "").strip()
            if not mobile:
                messages.error(request, "Mobile number is required for mobile financial service.")
                return redirect("payments:wallet")
            details = {"mobile_number": mobile, "account_holder_name": account_name}
        elif method_type == PayoutMethod.METHOD_BANK:
            bank_name = request.POST.get("bank_name", "").strip()
            account_number = request.POST.get("account_number", "").strip()
            routing = request.POST.get("routing_number", "").strip()
            if not bank_name or not account_number:
                messages.error(request, "Bank name and account number are required for bank transfer.")
                return redirect("payments:wallet")
            details = {
                "bank_name": bank_name,
                "account_number": account_number,
                "routing_number": routing,
                "account_holder_name": account_name,
            }
        else:
            messages.error(request, "Invalid method type selected.")
            return redirect("payments:wallet")

        # Set first method as default automatically
        is_first = not PayoutMethod.objects.filter(host=request.user).exists()

        pm = PayoutMethod(
            host=request.user,
            method_type=method_type,
            account_name=account_name,
            is_verified=True,
            is_default=is_first,
        )
        pm.set_details(details)
        pm.save()

        messages.success(request, f"{pm.get_method_type_display()} payout method added successfully.")
        return redirect("payments:wallet")


class DeletePayoutMethodView(LoginRequiredMixin, View):
    """Host deletes a payout method."""

    def post(self, request, method_id):
        pm = get_object_or_404(PayoutMethod, id=method_id, host=request.user)
        pm.delete()
        messages.success(request, "Payout method deleted.")
        return redirect("payments:wallet")


class RequestPayoutView(LoginRequiredMixin, View):
    """Host submits a payout request."""

    def post(self, request):
        try:
            amount_bdt = Decimal(request.POST.get("amount_bdt", "0"))
            amount_cents = int(amount_bdt * Decimal("100"))
            method_id = int(request.POST.get("method_id", 0))

            request_payout(host=request.user, amount_cents=amount_cents, method_id=method_id)
            messages.success(
                request,
                f"Payout request for ৳{amount_bdt:.2f} submitted successfully! Reserved from available balance.",
            )
        except Exception as e:
            messages.error(request, str(e))

        return redirect("payments:wallet")


class DownloadStatementCSVView(LoginRequiredMixin, View):
    """Download full host ledger statement as CSV."""

    def get(self, request):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="kairos_statement_{timezone.now().strftime("%Y%m%d")}.csv"'

        writer = csv.writer(response)
        writer.writerow([
            "ID", "Date (UTC)", "Provider", "Entry Type",
            "Description", "Amount (BDT)", "Running Custodial Balance (BDT)",
        ])

        entries = HostLedger.objects.filter(host=request.user).order_by("created_at", "id")
        running = 0
        rows = []
        for entry in entries:
            if entry.is_custodial:
                running += entry.amount_cents
            rows.append([
                entry.id,
                entry.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                entry.get_provider_display(),
                entry.get_entry_type_display(),
                entry.description,
                f"{entry.amount_cents / 100:.2f}",
                f"{running / 100:.2f}" if entry.is_custodial else "n/a (Stripe)",
            ])

        for row in reversed(rows):
            writer.writerow(row)

        return response


class DownloadStatementPDFView(LoginRequiredMixin, View):
    """Download clean printable statement HTML formatted view."""

    def get(self, request):
        entries = HostLedger.objects.filter(host=request.user).order_by("created_at", "id")
        running = 0
        rows = []
        for entry in entries:
            if entry.is_custodial:
                running += entry.amount_cents
            rows.append({
                "entry": entry,
                "amount_bdt": f"{entry.amount_cents / 100:.2f}",
                "running_bdt": f"{running / 100:.2f}" if entry.is_custodial else None,
                "is_custodial": entry.is_custodial,
            })
        rows.reverse()

        wallet_mode = get_host_wallet_mode(request.user)
        has_custodial = wallet_mode in ("custodial", "mixed")
        avail_cents = get_available_balance(request.user) if has_custodial else 0
        total_cents = get_total_balance(request.user) if has_custodial else 0

        context = {
            "host": request.user,
            "rows": rows,
            "wallet_mode": wallet_mode,
            "has_custodial": has_custodial,
            "available_bdt": f"{avail_cents / 100:.2f}",
            "total_bdt": f"{total_cents / 100:.2f}",
            "generated_at": timezone.now(),
        }
        return render(request, "payments/statement_pdf.html", context)


# ==============================================================================
# ADMIN PAYOUT QUEUE & MANUAL DISBURSEMENT VIEWS
# ==============================================================================


class AdminPayoutQueueView(StaffRequiredMixin, View):
    """Admin Payout Queue: lists requested, approved, and processing payouts with full decrypted account details."""

    def get(self, request):
        status_filter = request.GET.get("status", "requested")
        payouts_query = PayoutRequest.objects.select_related("host", "method").order_by("-requested_at")

        if status_filter != "all":
            payouts_query = payouts_query.filter(status=status_filter)

        payout_list = []
        for pr in payouts_query:
            details = pr.method.get_details()
            # Fetch host ledger history summary
            host_avail = get_available_balance(pr.host)
            payout_list.append({
                "payout_request": pr,
                "amount_bdt": f"{pr.amount_cents / 100:.2f}",
                "account_details": details,
                "host_available_bdt": f"{host_avail / 100:.2f}",
            })

        context = {
            "payout_list": payout_list,
            "current_status": status_filter,
            "status_choices": PayoutRequest.STATUS_CHOICES,
        }
        return render(request, "payments/admin_payout_queue.html", context)


class AdminPayoutProcessView(StaffRequiredMixin, View):
    """Admin action handler: approve, complete (with reference), or reject (with reason)."""

    def post(self, request, request_id):
        payout_req = get_object_or_404(PayoutRequest, id=request_id)
        action = request.POST.get("action")

        try:
            if action == "approve":
                approve_payout(payout_req, admin_user=request.user)
                messages.success(request, f"Payout request #{request_id} approved for processing.")
            elif action == "complete":
                reference = request.POST.get("reference", "").strip()
                notes = request.POST.get("notes", "").strip()
                complete_payout(payout_req, admin_user=request.user, reference=reference, notes=notes)
                messages.success(request, f"Payout request #{request_id} completed with reference {reference}.")
            elif action == "reject":
                reason = request.POST.get("rejection_reason", "").strip()
                reject_payout(payout_req, admin_user=request.user, rejection_reason=reason)
                messages.success(request, f"Payout request #{request_id} rejected and funds restored.")
            else:
                messages.error(request, "Unknown payout processing action.")
        except Exception as e:
            messages.error(request, str(e))

        return redirect("payments:admin_payout_queue")


class AdminPayoutExportCSVView(StaffRequiredMixin, View):
    """Export approved payout requests formatted for bulk manual processing."""

    def get(self, request):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="bulk_payouts_{timezone.now().strftime("%Y%m%d_%H%M")}.csv"'

        writer = csv.writer(response)
        writer.writerow([
            "Request ID",
            "Host Email",
            "Host Name",
            "Amount BDT",
            "Method Type",
            "Account Name",
            "Mobile / Bank Account",
            "Bank / Branch Info",
            "Requested At",
            "Status",
        ])

        approved_requests = PayoutRequest.objects.filter(
            status__in=[PayoutRequest.STATUS_APPROVED, PayoutRequest.STATUS_REQUESTED]
        ).select_related("host", "method")

        for pr in approved_requests:
            details = pr.method.get_details()
            mobile_or_acc = details.get("mobile_number") or details.get("account_number", "")
            bank_info = details.get("bank_name", "")
            if details.get("routing_number"):
                bank_info += f" (Routing: {details.get('routing_number')})"

            writer.writerow([
                pr.id,
                pr.host.email,
                pr.host.display_name or pr.host.email,
                f"{pr.amount_cents / 100:.2f}",
                pr.method.get_method_type_display(),
                pr.method.account_name,
                mobile_or_acc,
                bank_info,
                pr.requested_at.strftime("%Y-%m-%d %H:%M:%S"),
                pr.status,
            ])

        return response
