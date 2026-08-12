import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.payments.models import ProcessedWebhook
from apps.subscriptions.entitlements import get_effective_plan_code, get_user_subscription
from apps.subscriptions.plans import PLANS, get_plan
from apps.subscriptions.services import (
    create_stripe_billing_checkout_session,
    create_stripe_customer_portal_session,
    record_subscription_event,
    start_bdt_paystation_subscription,
)

logger = logging.getLogger(__name__)


class PricingView(View):
    """Public pricing page with plan comparison, currency toggle, and FAQ."""

    def get(self, request):
        currency = request.GET.get('currency', '').upper()
        if not currency:
            # Simple currency detection default
            currency = 'BDT' if request.META.get('HTTP_ACCEPT_LANGUAGE', '').find('bn') != -1 else 'USD'

        if currency not in ('USD', 'BDT'):
            currency = 'USD'

        user_plan_code = 'free'
        if request.user.is_authenticated:
            user_plan_code = get_effective_plan_code(request.user)

        context = {
            'plans': PLANS,
            'currency': currency,
            'user_plan_code': user_plan_code,
            'feature_required': request.GET.get('feature_required', ''),
            'limit_reached': request.GET.get('limit_reached', ''),
        }
        return render(request, 'subscriptions/pricing.html', context)


class BillingSettingsView(LoginRequiredMixin, View):
    """Billing settings dashboard showing plan status, usage, and actions."""

    def get(self, request):
        sub = get_user_subscription(request.user)
        plan = get_plan(sub.effective_plan_code)

        from apps.scheduling.models import EventType, Schedule
        event_types_count = EventType.objects.filter(owner=request.user, is_active=True).count()
        schedules_count = Schedule.objects.filter(user=request.user).count()

        context = {
            'subscription': sub,
            'plan': plan,
            'event_types_count': event_types_count,
            'schedules_count': schedules_count,
            'enable_paystation': getattr(settings, 'KAIROS_ENABLE_PAYSTATION_ROUTE', True),
        }
        return render(request, 'subscriptions/billing_settings.html', context)


class StripeBillingCheckoutView(LoginRequiredMixin, View):
    """Start Stripe Checkout for Pro subscription (USD / International)."""

    def get(self, request):
        success_url = request.build_absolute_uri(reverse('subscriptions:billing_settings'))
        cancel_url = request.build_absolute_uri(reverse('subscriptions:pricing'))

        try:
            checkout_url = create_stripe_billing_checkout_session(request.user, success_url, cancel_url)
            return redirect(checkout_url)
        except Exception as e:
            logger.error(f"Error creating Stripe Billing checkout session: {e}")
            messages.error(request, f"Unable to start Stripe Checkout: {e}")
            return redirect('subscriptions:pricing')


class StripeCustomerPortalView(LoginRequiredMixin, View):
    """Redirect host to Stripe Customer Portal for plan/card management and cancellation."""

    def get(self, request):
        return_url = request.build_absolute_uri(reverse('subscriptions:billing_settings'))
        try:
            portal_url = create_stripe_customer_portal_session(request.user, return_url)
            return redirect(portal_url)
        except Exception as e:
            logger.error(f"Error creating Stripe Customer Portal session: {e}")
            messages.error(request, "Stripe Portal is unavailable. Please contact support.")
            return redirect('subscriptions:billing_settings')


class StartPaystationSubscriptionView(LoginRequiredMixin, View):
    """Start or renew BDT Pro subscription via PayStation route."""

    def post(self, request):
        if not getattr(settings, 'KAIROS_ENABLE_PAYSTATION_ROUTE', True):
            messages.error(request, "PayStation subscription route is disabled.")
            return redirect('subscriptions:pricing')

        sub = start_bdt_paystation_subscription(request.user)
        messages.success(request, "Pro subscription activated successfully via PayStation (BDT)!")
        return redirect('subscriptions:billing_settings')


class CancelSubscriptionView(LoginRequiredMixin, View):
    """Cancellation flow with one optional feedback question (no retention gauntlet)."""

    def get(self, request):
        sub = get_user_subscription(request.user)
        return render(request, 'subscriptions/cancel_subscription.html', {'subscription': sub})

    def post(self, request):
        sub = get_user_subscription(request.user)
        reason = request.POST.get('reason', 'No reason provided')

        sub.cancel_at_period_end = True
        record_subscription_event(
            sub,
            'user_requested_cancellation',
            sub.status,
            payload={'reason': reason}
        )

        messages.info(request, "Your subscription cancellation request has been recorded. You will retain Pro features until the end of your current period.")
        return redirect('subscriptions:billing_settings')


@csrf_exempt
@require_POST
def stripe_billing_webhook(request):
    """Stripe Billing webhook endpoint for subscription events."""
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')

    if not sig_header:
        return HttpResponseBadRequest("Missing signature")

    import stripe
    stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', '')
    webhook_secret = getattr(settings, 'STRIPE_BILLING_WEBHOOK_SECRET', '')

    try:
        if webhook_secret:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        else:
            event = json.loads(payload.decode('utf-8'))
    except Exception as e:
        logger.error(f"Invalid Stripe Billing webhook signature: {e}")
        return HttpResponseBadRequest("Invalid signature")

    event_id = event.get('id', '')
    event_type = event.get('type', '')

    if ProcessedWebhook.objects.filter(event_id=event_id).exists():
        return HttpResponse(status=200)

    ProcessedWebhook.objects.create(
        event_id=event_id,
        provider='stripe_billing',
        event_type=event_type,
    )

    from apps.subscriptions.tasks import process_stripe_billing_webhook
    process_stripe_billing_webhook.delay(event)

    return HttpResponse(status=200)
