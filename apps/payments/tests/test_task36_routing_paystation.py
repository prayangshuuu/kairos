from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.payments.models import HostLedger, HostPaymentTerms, Payment, PaymentAccount
from apps.payments.routing import is_paystation_eligible, select_provider
from apps.payments.services import (
    compute_paystation_service_fee,
    confirm_payment,
    create_payment_for_booking,
    generate_payout,
    handle_refund,
)
from apps.scheduling.models import AvailabilityRule, EventType, Schedule


@pytest.fixture
def host(db):
    user = User.objects.create_user(
        email="task36host@example.com",
        password="password123",
        slug="task36-host",
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
def bdt_event_type(host):
    schedule = host.schedules.first()
    return EventType.objects.create(
        owner=host,
        title="BDT Consultation",
        slug="bdt-consultation",
        duration_minutes=30,
        price_cents=100000,  # ৳1000.00 (100,000 cents)
        currency="BDT",
        schedule=schedule,
    )


@pytest.fixture
def usd_event_type(host):
    schedule = host.schedules.first()
    return EventType.objects.create(
        owner=host,
        title="USD Consultation",
        slug="usd-consultation",
        duration_minutes=30,
        price_cents=5000,  # $50.00
        currency="USD",
        schedule=schedule,
    )


def create_test_booking(event_type, offset_days=1):
    from psycopg.types.range import Range

    start = timezone.now() + timedelta(days=offset_days)
    end = start + timedelta(minutes=30)
    return Booking.objects.create(
        event_type=event_type,
        host=event_type.owner,
        start_at=start,
        end_at=end,
        invitee_timezone="UTC",
        status=Booking.StatusChoices.PENDING_PAYMENT,
        invitee_name="Test Invitee",
        invitee_email="invitee@example.com",
        location_type="google_meet",
        buffered_period=Range(start, end),
    )


@pytest.mark.django_db
class TestTask36ProviderRouting:
    def test_routing_prefers_stripe_when_connected(self, host, bdt_event_type):
        """Routing prefers Stripe when host connected Stripe and charges are enabled."""
        PaymentAccount.objects.create(
            user=host,
            provider="stripe_connect",
            charges_enabled=True,
            is_active=True,
            external_account_id="acct_stripe_1",
        )
        HostPaymentTerms.objects.create(user=host, terms_version="1.0")

        provider = select_provider(bdt_event_type)
        assert provider is not None
        assert provider.name == "stripe_connect"

    def test_routing_falls_back_to_paystation_when_stripe_not_connected(self, host, bdt_event_type):
        """Routing falls back to PayStation when Stripe is not connected, host accepted terms, and currency is BDT."""
        HostPaymentTerms.objects.create(user=host, terms_version="1.0")

        provider = select_provider(bdt_event_type)
        assert provider is not None
        assert provider.name == "paystation"

    def test_routing_errors_when_neither_available(self, host, bdt_event_type):
        """Routing returns None when host neither connected Stripe nor accepted PayStation terms."""
        provider = select_provider(bdt_event_type)
        assert provider is None

    def test_non_bdt_event_cannot_route_to_paystation(self, host, usd_event_type):
        """Non-BDT event types cannot route to PayStation even if terms are accepted."""
        HostPaymentTerms.objects.create(user=host, terms_version="1.0")

        provider = select_provider(usd_event_type)
        assert provider is None

    def test_terms_acceptance_required_for_paystation_fallback(self, host, bdt_event_type):
        """PayStation fallback cannot be used without HostPaymentTerms acceptance."""
        assert not HostPaymentTerms.objects.filter(user=host).exists()
        assert not is_paystation_eligible(host, "BDT")
        assert select_provider(bdt_event_type) is None

        # Record terms
        HostPaymentTerms.objects.create(user=host, terms_version="1.0", ip_address="127.0.0.1")
        assert is_paystation_eligible(host, "BDT")
        assert select_provider(bdt_event_type).name == "paystation"


@pytest.mark.django_db
class TestTask36ServiceFeeArithmetic:
    def test_service_fee_exactness_and_rounding(self):
        """
        Verify exactness of 3% service fee calculation with Decimal ROUND_HALF_UP:
        - ৳1000 (100,000 cents) -> 3000 cents (৳30) fee
        - ৳999 (99,900 cents) -> 2997 cents (৳29.97) fee
        - ৳1 (100 cents) -> 3 cents (৳0.03) fee
        - 33 cents -> 1 cent fee (0.99 rounded up)
        - 50 cents -> 2 cents fee (1.5 rounded up to 2)
        """
        assert compute_paystation_service_fee(100000) == 3000
        assert compute_paystation_service_fee(99900) == 2997
        assert compute_paystation_service_fee(100) == 3
        assert compute_paystation_service_fee(33) == 1
        assert compute_paystation_service_fee(50) == 2

    def test_stored_fee_percentage_invariant_when_constant_changes(self, host, bdt_event_type):
        """The fee percentage and fee amount stored on Payment row remain unchanged if constant changes later."""
        HostPaymentTerms.objects.create(user=host, terms_version="1.0")
        booking = create_test_booking(bdt_event_type)

        payment = create_payment_for_booking(booking=booking)
        assert payment.fee_percent_applied == Decimal("3.0")
        assert payment.fee_amount_cents == 3000
        assert payment.net_owed_cents == 97000

        # Simulate global constant change (e.g. to 5.0%)
        # Existing payment stored fee must NOT shift
        payment.refresh_from_db()
        assert payment.fee_percent_applied == Decimal("3.0")
        assert payment.fee_amount_cents == 3000
        assert payment.net_owed_cents == 97000


@pytest.mark.django_db
class TestTask36LedgerAndRefunds:
    def test_ledger_lifecycle_charge_fee_refund_fee_reversal(self, host, bdt_event_type):
        """
        Verify ledger entries across booking confirmation and full refund:
        - Charge: +100,000 cents
        - Service Fee: -3,000 cents
        - Balance before refund: +97,000 cents
        - Full Refund: -100,000 cents
        - Fee Reversal: +3,000 cents
        - Balance after refund: 0 cents
        """
        HostPaymentTerms.objects.create(user=host, terms_version="1.0")
        booking = create_test_booking(bdt_event_type)

        payment = create_payment_for_booking(booking=booking)
        confirm_payment(payment_uid=str(payment.uid))

        entries = HostLedger.objects.filter(host=host)
        assert entries.count() == 2

        charge_entry = entries.get(entry_type="charge")
        assert charge_entry.amount_cents == 100000

        fee_entry = entries.get(entry_type="service_fee")
        assert fee_entry.amount_cents == -3000

        from django.db.models import Sum

        balance_before = entries.aggregate(Sum("amount_cents"))["amount_cents__sum"]
        assert balance_before == 97000

        # Refund booking
        handle_refund(payment=payment, amount_cents=100000)

        entries = HostLedger.objects.filter(host=host)
        assert entries.count() == 4

        refund_entry = entries.get(entry_type="refund")
        assert refund_entry.amount_cents == -100000

        reversal_entry = entries.get(entry_type="refund_fee_reversal")
        assert reversal_entry.amount_cents == 3000

        balance_after = entries.aggregate(Sum("amount_cents"))["amount_cents__sum"]
        assert balance_after == 0

    def test_refund_after_payout_produces_negative_balance_not_payout(self, host, bdt_event_type):
        """
        If host was already paid out, a refund produces a negative balance on HostLedger,
        and generate_payout refuses to create a payout for a negative balance.
        """
        HostPaymentTerms.objects.create(user=host, terms_version="1.0")

        # Use price high enough to meet min payout threshold (৳2000)
        bdt_event_type.price_cents = 200000
        bdt_event_type.save()

        booking = create_test_booking(bdt_event_type)

        payment = create_payment_for_booking(booking=booking)
        confirm_payment(payment_uid=str(payment.uid))

        # Generate payout for period
        period_start = timezone.now() - timedelta(days=1)
        period_end = timezone.now() + timedelta(days=1)
        payout = generate_payout(host=host, period_start=period_start, period_end=period_end)
        assert payout.net_cents == 194000  # 200,000 - 6,000 fee

        from django.db.models import Sum

        balance_after_payout = HostLedger.objects.filter(host=host).aggregate(Sum("amount_cents"))[
            "amount_cents__sum"
        ]
        assert balance_after_payout == 0

        # Refund booking after payout was disbursed
        handle_refund(payment=payment, amount_cents=200000)

        balance_after_refund = HostLedger.objects.filter(host=host).aggregate(Sum("amount_cents"))[
            "amount_cents__sum"
        ]
        assert balance_after_refund == -194000  # Host owes ৳1940

        # Attempting to generate payout now must fail with ValueError
        with pytest.raises(ValueError, match="zero or negative"):
            generate_payout(host=host, period_start=period_start, period_end=period_end)


@pytest.mark.django_db
class TestTask36FullHostStatementScenario:
    def test_full_host_statement_four_bookings_one_refund_one_payout(self, host, bdt_event_type):
        """
        Full Scenario Demonstration required by prompt:
        - 4 bookings of ৳1000 (100,000 cents) each
        - Booking 1, 2, 3, 4 confirmed -> each adds +100,000 charge and -3,000 service_fee (+97,000 net)
        - Total earnings on 4 bookings = 4 * 97,000 = 388,000 cents (৳3880)
        - 1 Refund on Booking 4 -> adds -100,000 refund and +3,000 refund_fee_reversal (-97,000 net)
        - Balance before payout = 3 * 97,000 = 291,000 cents (৳2910)
        - 1 Payout generated for period -> creates Payout of 291,000 cents and -291,000 payout entry
        - Closing balance = 0 cents.
        """
        HostPaymentTerms.objects.create(user=host, terms_version="1.0")

        bookings = []
        payments = []

        # Create and confirm 4 bookings
        for i in range(4):
            b = create_test_booking(bdt_event_type, offset_days=i + 1)
            p = create_payment_for_booking(booking=b)
            confirm_payment(payment_uid=str(p.uid))
            bookings.append(b)
            payments.append(p)

        from django.db.models import Sum

        bal1 = HostLedger.objects.filter(host=host).aggregate(Sum("amount_cents"))[
            "amount_cents__sum"
        ]
        assert bal1 == 388000  # 4 * 97000

        # 1 Refund on Booking 4
        handle_refund(payment=payments[3], amount_cents=100000)

        bal2 = HostLedger.objects.filter(host=host).aggregate(Sum("amount_cents"))[
            "amount_cents__sum"
        ]
        assert bal2 == 291000  # 3 * 97000

        # 1 Payout
        period_start = timezone.now() - timedelta(days=1)
        period_end = timezone.now() + timedelta(days=10)
        payout = generate_payout(host=host, period_start=period_start, period_end=period_end)

        assert payout is not None
        assert payout.gross_cents == 400000  # 4 total charges of 100,000
        assert payout.net_cents == 291000  # ৳2910 net payout

        closing_balance = HostLedger.objects.filter(host=host).aggregate(Sum("amount_cents"))[
            "amount_cents__sum"
        ]
        assert closing_balance == 0


@pytest.mark.django_db
class TestTask36ProviderSwitchingAndSafety:
    def test_provider_switching_retains_original_provider_on_existing_payments(
        self, host, bdt_event_type
    ):
        """
        A host using PayStation fallback later connects Stripe.
        New bookings use Stripe, existing paid bookings retain PayStation so refunds route correctly.
        """
        HostPaymentTerms.objects.create(user=host, terms_version="1.0")

        # Booking 1 created on PayStation route
        booking1 = create_test_booking(bdt_event_type, offset_days=1)
        payment1 = create_payment_for_booking(booking=booking1)
        confirm_payment(payment_uid=str(payment1.uid))
        assert payment1.provider == "paystation"

        # Host connects Stripe
        PaymentAccount.objects.create(
            user=host,
            provider="stripe_connect",
            charges_enabled=True,
            is_active=True,
            external_account_id="acct_switch_1",
        )

        # Booking 2 created after Stripe connection
        booking2 = create_test_booking(bdt_event_type, offset_days=2)
        payment2 = create_payment_for_booking(booking=booking2)
        assert payment2.provider == "stripe_connect"

        # Refund on Booking 1 routes through PayStation and updates HostLedger
        handle_refund(payment=payment1, amount_cents=100000)
        payment1.refresh_from_db()
        assert payment1.status == Payment.STATUS_REFUNDED
        assert HostLedger.objects.filter(payment=payment1, entry_type="refund").exists()

    def test_host_ledger_append_only_invariant(self, host, bdt_event_type):
        """HostLedger entries cannot be updated or deleted."""
        HostPaymentTerms.objects.create(user=host, terms_version="1.0")
        booking = create_test_booking(bdt_event_type)
        payment = create_payment_for_booking(booking=booking)
        confirm_payment(payment_uid=str(payment.uid))

        entry = HostLedger.objects.filter(host=host).first()
        assert entry is not None

        # Updating entry must raise ValueError
        entry.amount_cents = 99999
        with pytest.raises(ValueError, match="append-only"):
            entry.save()

        # Deleting entry must raise ValueError
        with pytest.raises(ValueError, match="append-only"):
            entry.delete()
