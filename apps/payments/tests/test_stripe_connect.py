"""
Comprehensive tests for Stripe Connect payment integration (Task 35).

All Stripe API calls are mocked. These tests verify:
  1. Invalid webhook signatures are rejected
  2. Webhook-before-redirect and redirect-before-webhook produce exactly one booking
  3. Duplicate Stripe event IDs are processed once
  4. Platform fee is frozen at creation time and does not drift with settings changes
  5. Expired checkout sessions release the SlotHold
  6. Refunds also refund the application fee proportionally
  7. Event type currency validation against connected account
"""

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.payments.models import (
    Payment,
    PaymentAccount,
    ProcessedWebhook,
    SlotHold,
)
from apps.payments.providers import StripeConnectProvider
from apps.payments.services import (
    compute_platform_fee,
    confirm_payment,
    create_payment_for_booking,
    expire_payment,
    validate_event_type_currency,
)
from apps.scheduling.models import AvailabilityRule, EventType, Schedule


@pytest.fixture
def host(db):
    """Create a host user with a schedule and paid event type."""
    user = User.objects.create_user(
        email="host@example.com",
        password="testpassword",
        slug="test-host",
        timezone="UTC",
    )
    schedule = Schedule.objects.create(
        user=user,
        name="Working Hours",
        timezone="UTC",
        is_default=True,
    )
    for weekday in range(5):
        AvailabilityRule.objects.create(
            schedule=schedule,
            weekday=weekday,
            start_time="09:00",
            end_time="17:00",
        )
    return user


@pytest.fixture
def paid_event_type(host):
    """Create a paid event type for the host."""
    schedule = host.schedules.first()
    return EventType.objects.create(
        owner=host,
        title="Paid Consultation",
        slug="paid-consultation",
        duration_minutes=30,
        price_cents=5000,  # $50.00
        currency="USD",
        schedule=schedule,
    )


@pytest.fixture
def payment_account(host):
    """Create a connected Stripe payment account."""
    return PaymentAccount.objects.create(
        user=host,
        provider="stripe_connect",
        external_account_id="acct_test123",
        charges_enabled=True,
        payouts_enabled=True,
        details_submitted=True,
        default_currency="USD",
        country="US",
        onboarding_completed_at=timezone.now(),
    )


@pytest.fixture
def booking_with_payment(paid_event_type, payment_account):
    """Create a booking in pending_payment state with a Payment and SlotHold."""

    start = timezone.now() + timedelta(days=1)
    end = start + timedelta(minutes=30)

    booking = Booking(
        event_type=paid_event_type,
        host=paid_event_type.owner,
        start_at=start,
        end_at=end,
        invitee_timezone="UTC",
        status=Booking.StatusChoices.PENDING_PAYMENT,
        invitee_name="Test Invitee",
        invitee_email="invitee@example.com",
        location_type="google_meet",
    )
    booking.save()

    payment = create_payment_for_booking(
        booking=booking,
        payment_account=payment_account,
    )
    # Refresh the booking from DB since create_payment_for_booking updated it
    booking.refresh_from_db()
    return booking, payment


# ---------------------------------------------------------------------------
# Test 1: Invalid webhook signature is rejected and confirms nothing
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestWebhookSignatureRejection:
    def test_invalid_signature_returns_400(self, client):
        """A webhook with an invalid signature is rejected with 400."""
        response = client.post(
            "/payments/webhooks/stripe-connect/",
            data=b'{"id": "evt_fake"}',
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="invalid_sig",
        )
        assert response.status_code == 400

    def test_missing_signature_returns_400(self, client):
        """A webhook without a signature header is rejected with 400."""
        response = client.post(
            "/payments/webhooks/stripe-connect/",
            data=b'{"id": "evt_fake"}',
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_invalid_signature_does_not_create_processed_webhook(self, client):
        """An invalid webhook does not create a ProcessedWebhook record."""
        client.post(
            "/payments/webhooks/stripe-connect/",
            data=b'{"id": "evt_fake"}',
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="invalid_sig",
        )
        assert ProcessedWebhook.objects.count() == 0

    def test_invalid_signature_does_not_confirm_booking(self, client, booking_with_payment):
        """An invalid webhook does not confirm any booking."""
        booking, payment = booking_with_payment

        payload = json.dumps(
            {
                "id": "evt_test123",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "client_reference_id": str(payment.uid),
                        "payment_intent": "pi_test123",
                    }
                },
            }
        )

        client.post(
            "/payments/webhooks/stripe-connect/",
            data=payload.encode(),
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="invalid_sig",
        )

        booking.refresh_from_db()
        assert booking.status == Booking.StatusChoices.PENDING_PAYMENT
        payment.refresh_from_db()
        assert payment.status == Payment.STATUS_PENDING


# ---------------------------------------------------------------------------
# Test 2: Webhook-before-redirect and redirect-before-webhook
# Both produce exactly one confirmed booking
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestWebhookRedirectOrdering:
    def test_webhook_first_then_redirect_produces_one_confirmation(self, booking_with_payment):
        """Webhook arrives first, then browser redirect — exactly one confirmation."""
        booking, payment = booking_with_payment

        # First call: webhook arrives
        result1 = confirm_payment(payment_uid=str(payment.uid))
        assert result1.status == Payment.STATUS_COMPLETED

        booking.refresh_from_db()
        assert booking.status == Booking.StatusChoices.CONFIRMED

        # Second call: browser redirect arrives — should be no-op
        result2 = confirm_payment(payment_uid=str(payment.uid))
        assert result2.status == Payment.STATUS_COMPLETED

        # Still only one confirmed booking
        booking.refresh_from_db()
        assert booking.status == Booking.StatusChoices.CONFIRMED

    def test_redirect_first_then_webhook_produces_one_confirmation(self, booking_with_payment):
        """Browser redirect arrives first, then webhook — exactly one confirmation."""
        booking, payment = booking_with_payment

        # First call: redirect return
        result1 = confirm_payment(payment_uid=str(payment.uid))
        assert result1.status == Payment.STATUS_COMPLETED

        # Second call: webhook (idempotent no-op)
        result2 = confirm_payment(payment_uid=str(payment.uid))
        assert result2.status == Payment.STATUS_COMPLETED

        booking.refresh_from_db()
        assert booking.status == Booking.StatusChoices.CONFIRMED

    def test_slot_hold_released_only_once(self, booking_with_payment):
        """The SlotHold is released on the first confirmation, stays released on the second."""
        booking, payment = booking_with_payment
        hold = SlotHold.objects.get(payment=payment)
        assert hold.is_released is False

        confirm_payment(payment_uid=str(payment.uid))
        hold.refresh_from_db()
        assert hold.is_released is True
        assert hold.release_reason == "completed"
        first_released_at = hold.released_at

        # Second call — still released, same timestamp
        confirm_payment(payment_uid=str(payment.uid))
        hold.refresh_from_db()
        assert hold.is_released is True
        assert hold.released_at == first_released_at


# ---------------------------------------------------------------------------
# Test 3: Same Stripe event ID delivered twice is processed once
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestWebhookIdempotency:
    @patch("apps.payments.views.StripeConnectProvider")
    def test_duplicate_event_id_processed_once(
        self, mock_provider_cls, client, booking_with_payment
    ):
        """The same Stripe event id delivered twice creates only one ProcessedWebhook."""
        booking, payment = booking_with_payment
        event_id = "evt_duplicate_test_12345"

        event_data = {
            "id": event_id,
            "type": "checkout.session.completed",
            "account": "acct_test123",
            "data": {
                "object": {
                    "id": "cs_test_session",
                    "client_reference_id": str(payment.uid),
                    "payment_intent": "pi_test123",
                }
            },
        }

        # Mock the provider instance
        mock_instance = MagicMock()
        mock_instance.verify_webhook_signature.return_value = event_data
        mock_provider_cls.return_value = mock_instance

        # First webhook delivery
        response1 = client.post(
            "/payments/webhooks/stripe-connect/",
            data=json.dumps(event_data).encode(),
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="valid_sig",
        )
        assert response1.status_code == 200
        assert ProcessedWebhook.objects.filter(event_id=event_id).count() == 1

        # Second webhook delivery (Stripe retry)
        response2 = client.post(
            "/payments/webhooks/stripe-connect/",
            data=json.dumps(event_data).encode(),
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="valid_sig",
        )
        assert response2.status_code == 200
        # Still only one record
        assert ProcessedWebhook.objects.filter(event_id=event_id).count() == 1


# ---------------------------------------------------------------------------
# Test 4: Platform fee is frozen at creation — does not change with settings
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestPlatformFeeFreezing:
    @override_settings(
        KAIROS_PLATFORM_FEE_PERCENT=5.0,
        KAIROS_PLATFORM_FEE_FIXED_CENTS=0,
        KAIROS_PLATFORM_FEE_MIN_CENTS=50,
    )
    def test_fee_does_not_change_when_settings_change(self, paid_event_type, payment_account):
        """Platform fee stored on Payment does not change when fee setting changes."""

        start = timezone.now() + timedelta(days=1)
        end = start + timedelta(minutes=30)
        booking = Booking(
            event_type=paid_event_type,
            host=paid_event_type.owner,
            start_at=start,
            end_at=end,
            invitee_timezone="UTC",
            status=Booking.StatusChoices.PENDING_PAYMENT,
            invitee_name="Fee Test Invitee",
            invitee_email="fee@example.com",
            location_type="google_meet",
        )
        booking.save()

        # Create payment with 5% fee
        payment = create_payment_for_booking(
            booking=booking,
            payment_account=payment_account,
        )

        # 5% of 5000 = 250 cents
        assert payment.fee_amount_cents == 250
        assert payment.fee_percent_applied == Decimal("5.0")

        # Now "change" the settings (simulate via override_settings)
        with override_settings(
            KAIROS_PLATFORM_FEE_PERCENT=10.0,
            KAIROS_PLATFORM_FEE_FIXED_CENTS=100,
        ):
            # The stored fee should NOT change
            payment.refresh_from_db()
            assert payment.fee_amount_cents == 250
            assert payment.fee_percent_applied == Decimal("5.0")
            assert payment.fee_fixed_applied == 0

    def test_fee_computation_with_minimum(self):
        """Fee computation respects the minimum fee setting."""
        with override_settings(
            KAIROS_PLATFORM_FEE_PERCENT=1.0,
            KAIROS_PLATFORM_FEE_FIXED_CENTS=0,
            KAIROS_PLATFORM_FEE_MIN_CENTS=100,
        ):
            result = compute_platform_fee(500)  # 1% of 500 = 5 < min 100
            assert result["fee_amount_cents"] == 100


# ---------------------------------------------------------------------------
# Test 5: Expired checkout session releases the SlotHold
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCheckoutExpiration:
    def test_expired_checkout_releases_slot_hold(self, booking_with_payment):
        """An expired checkout session releases the SlotHold and cancels the booking."""
        booking, payment = booking_with_payment
        hold = SlotHold.objects.get(payment=payment)

        assert hold.is_released is False
        assert booking.status == Booking.StatusChoices.PENDING_PAYMENT

        expire_payment(payment_uid=str(payment.uid))

        hold.refresh_from_db()
        assert hold.is_released is True
        assert hold.release_reason == "expired"

        payment.refresh_from_db()
        assert payment.status == Payment.STATUS_FAILED

        booking.refresh_from_db()
        assert booking.status == Booking.StatusChoices.CANCELLED

    def test_expired_payment_already_completed_is_noop(self, booking_with_payment):
        """Expiring an already-completed payment is a no-op."""
        booking, payment = booking_with_payment

        # First: confirm
        confirm_payment(payment_uid=str(payment.uid))

        # Then try to expire — should be no-op
        result = expire_payment(payment_uid=str(payment.uid))
        assert result.status == Payment.STATUS_COMPLETED

        booking.refresh_from_db()
        assert booking.status == Booking.StatusChoices.CONFIRMED

    def test_hold_expiry_task_releases_expired_holds(self, booking_with_payment):
        """The release_expired_slot_holds task finds and expires overdue holds."""
        booking, payment = booking_with_payment
        hold = SlotHold.objects.get(payment=payment)

        # Backdate the hold to be expired
        SlotHold.objects.filter(pk=hold.pk).update(expires_at=timezone.now() - timedelta(minutes=1))

        from apps.payments.tasks import release_expired_slot_holds

        release_expired_slot_holds()

        hold.refresh_from_db()
        assert hold.is_released is True

        payment.refresh_from_db()
        assert payment.status == Payment.STATUS_FAILED

        booking.refresh_from_db()
        assert booking.status == Booking.StatusChoices.CANCELLED


# ---------------------------------------------------------------------------
# Test 6: Refund also refunds the application fee proportionally
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestRefundWithFee:
    @patch("apps.payments.providers.stripe")
    def test_full_refund_uses_refund_application_fee(self, mock_stripe, booking_with_payment):
        """A full refund calls Stripe with refund_application_fee=True."""
        booking, payment = booking_with_payment
        # Simulate completed payment
        confirm_payment(payment_uid=str(payment.uid))
        payment.refresh_from_db()
        payment.external_payment_intent_id = "pi_test_refund"
        payment.save(update_fields=["external_payment_intent_id"])

        mock_refund = MagicMock()
        mock_refund.id = "re_test123"
        mock_refund.status = "succeeded"
        mock_refund.amount = 5000
        mock_stripe.Refund.create.return_value = mock_refund

        from apps.payments.services import handle_refund

        handle_refund(payment=payment)

        # Verify refund_application_fee=True was passed
        mock_stripe.Refund.create.assert_called_once_with(
            payment_intent="pi_test_refund",
            amount=5000,
            refund_application_fee=True,
            stripe_account="acct_test123",
        )

        payment.refresh_from_db()
        assert payment.status == Payment.STATUS_REFUNDED
        assert payment.refund_amount_cents == 5000

    @patch("apps.payments.providers.stripe")
    def test_partial_refund_status(self, mock_stripe, booking_with_payment):
        """A partial refund sets status to PARTIALLY_REFUNDED."""
        booking, payment = booking_with_payment
        confirm_payment(payment_uid=str(payment.uid))
        payment.refresh_from_db()
        payment.external_payment_intent_id = "pi_test_partial"
        payment.save(update_fields=["external_payment_intent_id"])

        mock_refund = MagicMock()
        mock_refund.id = "re_test_partial"
        mock_refund.status = "succeeded"
        mock_refund.amount = 2500
        mock_stripe.Refund.create.return_value = mock_refund

        from apps.payments.services import handle_refund

        handle_refund(payment=payment, amount_cents=2500)

        payment.refresh_from_db()
        assert payment.status == Payment.STATUS_PARTIALLY_REFUNDED
        assert payment.refund_amount_cents == 2500


# ---------------------------------------------------------------------------
# Test 7: Event type cannot be saved with unsupported currency
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCurrencyValidation:
    def test_matching_currency_is_valid_no_warning(self, paid_event_type, payment_account):
        """Currency matching the account's default is valid with no warning."""
        result = validate_event_type_currency(
            event_type=paid_event_type,
            payment_account=payment_account,
        )
        assert result["valid"] is True
        assert result["warning"] is None
        assert result["error"] is None

    def test_supported_non_default_currency_has_warning(self, paid_event_type, payment_account):
        """A supported but non-default currency is valid with a conversion warning."""
        paid_event_type.currency = "EUR"
        paid_event_type.save(update_fields=["currency"])

        result = validate_event_type_currency(
            event_type=paid_event_type,
            payment_account=payment_account,
        )
        assert result["valid"] is True
        assert result["warning"] is not None
        assert "conversion" in result["warning"].lower() or "convert" in result["warning"].lower()

    def test_unsupported_currency_is_invalid(self, paid_event_type, payment_account):
        """An unsupported currency is invalid with an error."""
        paid_event_type.currency = "XYZ"
        paid_event_type.save(update_fields=["currency"])

        result = validate_event_type_currency(
            event_type=paid_event_type,
            payment_account=payment_account,
        )
        assert result["valid"] is False
        assert result["error"] is not None


# ---------------------------------------------------------------------------
# Additional tests for completeness
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestPaymentCreation:
    def test_payment_and_slot_hold_created(self, paid_event_type, payment_account):
        """create_payment_for_booking creates both Payment and SlotHold."""
        start = timezone.now() + timedelta(days=1)
        end = start + timedelta(minutes=30)
        booking = Booking(
            event_type=paid_event_type,
            host=paid_event_type.owner,
            start_at=start,
            end_at=end,
            invitee_timezone="UTC",
            status=Booking.StatusChoices.PENDING_PAYMENT,
            invitee_name="Creation Test",
            invitee_email="create@example.com",
            location_type="google_meet",
        )
        booking.save()

        payment = create_payment_for_booking(
            booking=booking,
            payment_account=payment_account,
        )

        assert payment.status == Payment.STATUS_PENDING
        assert payment.amount_cents == 5000
        assert payment.currency == "USD"
        assert payment.invoice_number.startswith("KRS-")
        assert payment.provider == "stripe_connect"

        hold = SlotHold.objects.get(payment=payment)
        assert hold.is_released is False
        assert hold.booking == booking

        booking.refresh_from_db()
        assert booking.status == Booking.StatusChoices.PENDING_PAYMENT

    def test_free_event_type_raises_error(self, host, payment_account):
        """Attempting to create payment for free event type raises ValueError."""
        schedule = host.schedules.first()
        free_event = EventType.objects.create(
            owner=host,
            title="Free Event",
            slug="free-event",
            duration_minutes=30,
            price_cents=0,
            schedule=schedule,
        )
        start = timezone.now() + timedelta(days=1)
        end = start + timedelta(minutes=30)
        booking = Booking(
            event_type=free_event,
            host=host,
            start_at=start,
            end_at=end,
            invitee_timezone="UTC",
            invitee_name="Free Test",
            invitee_email="free@example.com",
            location_type="google_meet",
        )
        booking.save()

        with pytest.raises(ValueError, match="not paid"):
            create_payment_for_booking(
                booking=booking,
                payment_account=payment_account,
            )


@pytest.mark.django_db
class TestDisputeHandling:
    def test_dispute_flags_payment(self, booking_with_payment):
        """handle_dispute flags the payment as DISPUTED."""
        booking, payment = booking_with_payment
        confirm_payment(payment_uid=str(payment.uid))

        from apps.payments.services import handle_dispute

        dispute_data = {
            "id": "dp_test123",
            "charge": "ch_test123",
            "evidence_details": {"due_by": 1234567890},
        }
        handle_dispute(payment=payment, dispute_data=dispute_data)

        payment.refresh_from_db()
        assert payment.status == Payment.STATUS_DISPUTED
        assert "dispute" in payment.metadata


@pytest.mark.django_db
class TestSyncPaymentAccount:
    def test_sync_updates_capabilities(self, payment_account):
        """sync_payment_account_from_stripe updates charges/payouts/requirements."""
        from apps.payments.services import sync_payment_account_from_stripe

        # Simulate Stripe revoking capabilities
        stripe_data = {
            "id": "acct_test123",
            "charges_enabled": False,
            "payouts_enabled": False,
            "details_submitted": True,
            "default_currency": "usd",
            "country": "US",
            "requirements": {
                "currently_due": ["individual.verification.document"],
            },
        }

        sync_payment_account_from_stripe(
            payment_account=payment_account,
            stripe_account_data=stripe_data,
        )

        payment_account.refresh_from_db()
        assert payment_account.charges_enabled is False
        assert payment_account.payouts_enabled is False
        assert "individual.verification.document" in payment_account.requirements_due

    def test_onboarding_completed_at_set_when_charges_enabled(self, host):
        """onboarding_completed_at is set when charges_enabled becomes True."""
        from apps.payments.services import sync_payment_account_from_stripe

        account = PaymentAccount.objects.create(
            user=host,
            provider="stripe_connect",
            external_account_id="acct_new_test",
        )
        assert account.onboarding_completed_at is None

        stripe_data = {
            "id": "acct_new_test",
            "charges_enabled": True,
            "payouts_enabled": True,
            "details_submitted": True,
            "default_currency": "usd",
            "country": "US",
            "requirements": {"currently_due": []},
        }

        sync_payment_account_from_stripe(
            payment_account=account,
            stripe_account_data=stripe_data,
        )

        account.refresh_from_db()
        assert account.onboarding_completed_at is not None
        assert account.charges_enabled is True


@pytest.mark.django_db
class TestProviderCheckout:
    @patch("apps.payments.providers.stripe")
    def test_create_checkout_uses_direct_charges(self, mock_stripe, booking_with_payment):
        """create_checkout creates a session with stripe_account (direct charge)."""
        booking, payment = booking_with_payment

        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/test"
        mock_session.id = "cs_test_session"
        mock_session.get.return_value = None
        mock_stripe.checkout.Session.create.return_value = mock_session

        provider = StripeConnectProvider()
        result = provider.create_checkout(
            payment,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

        assert result.redirect_url == "https://checkout.stripe.com/test"
        assert result.session_id == "cs_test_session"

        # Verify direct charge: stripe_account passed
        call_kwargs = mock_stripe.checkout.Session.create.call_args
        assert (
            call_kwargs.kwargs.get("stripe_account") == "acct_test123"
            or call_kwargs[1].get("stripe_account") == "acct_test123"
        )

    @patch("apps.payments.providers.stripe")
    def test_create_checkout_sets_application_fee(self, mock_stripe, booking_with_payment):
        """create_checkout sets application_fee_amount to the platform fee."""
        booking, payment = booking_with_payment

        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/test"
        mock_session.id = "cs_test_session"
        mock_session.get.return_value = None
        mock_stripe.checkout.Session.create.return_value = mock_session

        provider = StripeConnectProvider()
        provider.create_checkout(
            payment,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

        call_kwargs = mock_stripe.checkout.Session.create.call_args
        all_kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        pid = all_kwargs.get("payment_intent_data", {})
        assert pid.get("application_fee_amount") == payment.fee_amount_cents

    @patch("apps.payments.providers.stripe")
    def test_create_checkout_sets_idempotency_key(self, mock_stripe, booking_with_payment):
        """create_checkout derives idempotency key from invoice_number."""
        booking, payment = booking_with_payment

        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/test"
        mock_session.id = "cs_test_session"
        mock_session.get.return_value = None
        mock_stripe.checkout.Session.create.return_value = mock_session

        provider = StripeConnectProvider()
        provider.create_checkout(
            payment,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

        call_kwargs = mock_stripe.checkout.Session.create.call_args
        all_kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        assert all_kwargs.get("idempotency_key") == f"checkout_{payment.invoice_number}"
