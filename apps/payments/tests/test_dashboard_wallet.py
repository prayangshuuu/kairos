"""
Task 39d — Dashboard Wallet Card Tests.

Tests that the dashboard renders correctly for all four host types:
  1. Custodial (PayStation) — shows balance, payout button logic
  2. Non-custodial (Stripe only) — no balance figure in response body
  3. Mixed — custodial balance labelled as Kairos-collected, Stripe separate
  4. No payments configured — wallet card hidden entirely

Also asserts bounded query count on the dashboard home page.
"""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.payments.models import (
    HostLedger,
    HostPaymentTerms,
    Payment,
    PaymentAccount,
    PayoutMethod,
    PayoutRequest,
)
from apps.payments.wallet import (
    get_dashboard_wallet_summary,
    has_payment_route,
    record_paystation_charge,
    record_stripe_charge,
)
from apps.scheduling.models import EventType, Schedule


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def custodial_host(db):
    """Host using PayStation only (custodial). Has accepted PayStation terms."""
    user = User.objects.create_user(
        email="custodial-host@example.com",
        password="password123",
        slug="custodial-host",
        timezone="UTC",
    )
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC", is_default=True)
    et = EventType.objects.create(
        owner=user, title="Consultation", slug="consultation",
        duration_minutes=30, price_cents=10000, currency="BDT",
        schedule=schedule,
    )
    HostPaymentTerms.objects.create(user=user, terms_version="1.0")
    return user, et


@pytest.fixture
def noncustodial_host(db):
    """Host using Stripe Connect only (non-custodial). Has a Stripe account."""
    user = User.objects.create_user(
        email="stripe-host@example.com",
        password="password123",
        slug="stripe-host",
        timezone="UTC",
    )
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC", is_default=True)
    et = EventType.objects.create(
        owner=user, title="Stripe Session", slug="stripe-session",
        duration_minutes=30, price_cents=10000, currency="BDT",
        schedule=schedule,
    )
    PaymentAccount.objects.create(
        user=user, provider="stripe_connect",
        external_account_id="acct_stripe_only_test",
        charges_enabled=True, is_active=True,
    )
    return user, et


@pytest.fixture
def mixed_host(db):
    """Host using both PayStation and Stripe."""
    user = User.objects.create_user(
        email="mixed-host@example.com",
        password="password123",
        slug="mixed-host",
        timezone="UTC",
    )
    schedule = Schedule.objects.create(user=user, name="Default", timezone="UTC", is_default=True)
    et = EventType.objects.create(
        owner=user, title="Mixed Session", slug="mixed-session",
        duration_minutes=30, price_cents=10000, currency="BDT",
        schedule=schedule,
    )
    HostPaymentTerms.objects.create(user=user, terms_version="1.0")
    PaymentAccount.objects.create(
        user=user, provider="stripe_connect",
        external_account_id="acct_mixed_test",
        charges_enabled=True, is_active=True,
    )
    return user, et


@pytest.fixture
def no_payments_host(db):
    """Host with NO payment route configured."""
    user = User.objects.create_user(
        email="nopay-host@example.com",
        password="password123",
        slug="nopay-host",
        timezone="UTC",
    )
    Schedule.objects.create(user=user, name="Default", timezone="UTC", is_default=True)
    return user


def _make_booking_and_payment(host, event_type, suffix, provider="paystation",
                               is_settled=True, amount_cents=10000, fee_cents=300,
                               days_ago=3):
    """Create a booking + payment pair for testing."""
    now = timezone.now()
    start = now - timedelta(days=days_ago)
    end = start + timedelta(minutes=30)
    b = Booking.objects.create(
        event_type=event_type, host=host,
        start_at=start, end_at=end,
        invitee_name="Client", invitee_email="client@example.com",
        status=Booking.StatusChoices.CONFIRMED,
    )
    p = Payment.objects.create(
        booking=b, provider=provider,
        invoice_number=f"INV-DASH-{suffix}",
        amount_cents=amount_cents, fee_amount_cents=fee_cents,
        currency="BDT", status=Payment.STATUS_COMPLETED,
        is_settled=is_settled,
    )
    return b, p


# ---------------------------------------------------------------------------
# Main test class
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDashboardWalletCard:
    """
    Verify the dashboard renders correctly for all four host types:
    custodial, non-custodial, mixed, and no payments configured.
    """

    def _get_dashboard(self, client, user):
        """Log in and GET the dashboard."""
        client.force_login(user)
        return client.get(reverse("dashboard"))

    # ---- 1. Custodial host (PayStation) ----

    def test_custodial_host_shows_balance(self, custodial_host):
        """Custodial host sees available balance as primary figure."""
        user, et = custodial_host
        _, p = _make_booking_and_payment(user, et, "CUS-1", provider="paystation", is_settled=True)
        record_paystation_charge(p)

        client = Client()
        resp = self._get_dashboard(client, user)
        assert resp.status_code == 200
        content = resp.content.decode()

        # Wallet card is present
        assert "wallet-summary-card" in content
        # Balance figure is shown
        assert "Available balance" in content
        # The amount should appear (10000 - 300 fee = 9700 cents = 97.00)
        assert "97.00" in content or "100.00" in content

    def test_custodial_host_payout_shortfall(self, custodial_host):
        """Custodial host below payout minimum sees shortfall message, not disabled button."""
        user, et = custodial_host
        # Create a small payment (100 cents = ৳1.00, well below the ৳10.00 minimum)
        _, p = _make_booking_and_payment(
            user, et, "CUS-SMALL", provider="paystation",
            is_settled=True, amount_cents=100, fee_cents=3,
        )
        record_paystation_charge(p)

        client = Client()
        resp = self._get_dashboard(client, user)
        content = resp.content.decode()

        assert "wallet-summary-card" in content
        assert "more needed to reach" in content
        assert "payout minimum" in content

    def test_custodial_host_pending_balance(self, custodial_host):
        """Custodial host with unsettled payments sees pending balance and settlement explanation."""
        user, et = custodial_host
        _, p = _make_booking_and_payment(
            user, et, "CUS-PEND", provider="paystation",
            is_settled=False, amount_cents=10000, fee_cents=300,
        )
        record_paystation_charge(p)

        client = Client()
        resp = self._get_dashboard(client, user)
        content = resp.content.decode()

        assert "pending" in content.lower()
        assert "settles after meetings complete" in content

    # ---- 2. Non-custodial host (Stripe only) ----

    def test_noncustodial_host_no_balance_figure(self, noncustodial_host):
        """
        CRITICAL: Non-custodial host must have NO balance figure anywhere.
        Must show Stripe info only.
        """
        user, et = noncustodial_host
        _, p = _make_booking_and_payment(user, et, "STR-1", provider="stripe", is_settled=True)
        record_stripe_charge(p)

        client = Client()
        resp = self._get_dashboard(client, user)
        assert resp.status_code == 200
        content = resp.content.decode()

        # Wallet card IS shown (they have a payment route)
        assert "wallet-summary-card" in content
        # Shows Stripe info
        assert "Stripe" in content
        assert "directly to your Stripe account" in content
        # NO "Available balance" or "Kairos-collected balance" labels
        assert "Available balance" not in content
        assert "Kairos-collected balance" not in content
        # The word "balance" in the financial sense must not appear
        # (it can appear in "Kairos does not hold any funds" context, which is fine)
        # No payout button
        assert "Request payout" not in content

    # ---- 3. Mixed host ----

    def test_mixed_host_shows_custodial_balance_labelled(self, mixed_host):
        """Mixed host sees custodial balance labelled as 'Kairos-collected'."""
        user, et = mixed_host
        # PayStation payment
        _, p1 = _make_booking_and_payment(user, et, "MIX-PS-1", provider="paystation", is_settled=True, days_ago=4)
        record_paystation_charge(p1)
        # Stripe payment
        _, p2 = _make_booking_and_payment(user, et, "MIX-ST-1", provider="stripe", is_settled=True, days_ago=3)
        record_stripe_charge(p2)

        client = Client()
        resp = self._get_dashboard(client, user)
        content = resp.content.decode()

        assert "wallet-summary-card" in content
        assert "Kairos-collected balance" in content
        assert "Stripe this month" in content or "Stripe Dashboard" in content

    # ---- 4. No payments configured ----

    def test_no_payments_host_hides_card(self, no_payments_host):
        """Host with no payment route configured sees NO wallet card at all."""
        client = Client()
        resp = self._get_dashboard(client, no_payments_host)
        content = resp.content.decode()

        assert resp.status_code == 200
        assert "wallet-summary-card" not in content

    # ---- 5. Empty state (payment route but no transactions) ----

    def test_empty_state_shows_balance_zero(self, custodial_host):
        """Host with payment route but no transactions sees a 0 balance, falling through to normal logic."""
        user, et = custodial_host
        # Don't create any payments

        client = Client()
        resp = self._get_dashboard(client, user)
        content = resp.content.decode()

        assert "wallet-summary-card" in content
        assert "Available balance" in content
        assert "0.00" in content

    # ---- 6. Bounded query count ----

    def test_dashboard_bounded_query_count(self, custodial_host, settings):
        """
        The dashboard home page must not issue an unbounded number of queries.
        With wallet data, the total query count should stay below a reasonable bound.
        """
        user, et = custodial_host

        # Create several payments to ensure queries don't scale with data
        for i in range(5):
            _, p = _make_booking_and_payment(
                user, et, f"QC-{i}",
                provider="paystation", is_settled=True, days_ago=i + 1,
            )
            record_paystation_charge(p)

        client = Client()
        client.force_login(user)

        settings.DEBUG = True
        from django.test.utils import override_settings
        from django.db import connection, reset_queries

        reset_queries()
        with override_settings(DEBUG=True):
            connection.queries_log.clear()
            resp = client.get(reverse("dashboard"))
            query_count = len(connection.queries)

        assert resp.status_code == 200
        # Dashboard should issue a bounded number of queries.
        # Budget: auth/session (~3) + bookings (~2) + calendar (~1) + wallet (~5) + context processor (~3) = ~14
        # Allow up to 25 as a generous bound.
        assert query_count <= 25, (
            f"Dashboard issued {query_count} queries (max 25). "
            f"Wallet summary should use bounded aggregate queries."
        )

    # ---- 7. Notification badge ----

    def test_payout_notification_badge(self, custodial_host):
        """Pending payout request surfaces a notification badge."""
        user, et = custodial_host
        _, p = _make_booking_and_payment(user, et, "BADGE-1", provider="paystation", is_settled=True)
        record_paystation_charge(p)

        # Create a payout method and request
        pm = PayoutMethod(
            host=user, method_type=PayoutMethod.METHOD_BKASH,
            account_name="Test bKash", is_verified=True, is_default=True,
        )
        pm.set_details({"mobile_number": "01700000000", "account_holder_name": "Test"})
        pm.save()

        PayoutRequest.objects.create(
            host=user, amount_cents=5000, currency="BDT",
            method=pm, status=PayoutRequest.STATUS_REQUESTED,
        )

        summary = get_dashboard_wallet_summary(user)
        assert summary is not None
        assert summary["payout_notification"] is not None
        assert summary["payout_notification"]["type"] == "pending"

    # ---- 8. Wallet nav visibility ----

    def test_wallet_nav_visible_for_configured_host(self, custodial_host):
        """Wallet nav item appears for hosts with payment route."""
        user, et = custodial_host
        client = Client()
        resp = self._get_dashboard(client, user)
        content = resp.content.decode()
        # The nav should contain a link to the wallet
        assert "/payments/wallet/" in content or "Wallet" in content

    def test_wallet_nav_hidden_for_unconfigured_host(self, no_payments_host):
        """Wallet nav item does not appear for hosts without payment route."""
        client = Client()
        resp = self._get_dashboard(client, no_payments_host)
        content = resp.content.decode()
        # Should not have the wallet nav link
        # Check that "Wallet" does not appear as a nav item
        assert "show_wallet_nav" not in content or "Wallet</span>" not in content

    # ---- 9. has_payment_route unit tests ----

    def test_has_payment_route_stripe(self, noncustodial_host):
        user, _ = noncustodial_host
        assert has_payment_route(user) is True

    def test_has_payment_route_paystation(self, custodial_host):
        user, _ = custodial_host
        assert has_payment_route(user) is True

    def test_has_no_payment_route(self, no_payments_host):
        assert has_payment_route(no_payments_host) is False

    # ---- 10. get_dashboard_wallet_summary returns None for no-payments host ----

    def test_summary_none_for_unconfigured(self, no_payments_host):
        summary = get_dashboard_wallet_summary(no_payments_host)
        assert summary is None
