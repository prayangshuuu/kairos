import pytest
from django.utils import timezone

from apps.bookings.models import Booking
from apps.payments.models import HostLedger, HostPaymentTerms, PaymentAccount
from apps.payments.routing import select_provider
from apps.payments.services import (
    confirm_payment,
    create_payment_for_booking,
    generate_payout,
    handle_refund,
)
from apps.scheduling.models import EventType


@pytest.fixture
def host_user(django_user_model):
    return django_user_model.objects.create(email="host@example.com")


@pytest.fixture
def event_type(host_user):
    return EventType.objects.create(
        owner=host_user,
        title="Test Event",
        slug="test-event",
        duration_minutes=30,
        price_cents=10000,
        currency="BDT",
    )


@pytest.fixture
def booking(event_type):
    from psycopg.types.range import Range

    start = timezone.now() + timezone.timedelta(days=1)
    end = start + timezone.timedelta(minutes=30)
    return Booking.objects.create(
        event_type=event_type,
        host=event_type.owner,
        invitee_email="invitee@example.com",
        invitee_name="Invitee",
        start_at=start,
        end_at=end,
        buffered_period=Range(start, end),
    )


@pytest.mark.django_db
class TestProviderRouting:
    def test_routing_picks_explicit_choice(self, host_user, event_type, settings):
        settings.KAIROS_ENABLE_PAYSTATION_ROUTE = True
        HostPaymentTerms.objects.create(user=host_user, terms_version="1.0")

        PaymentAccount.objects.create(
            user=host_user,
            provider="stripe_connect",
            charges_enabled=True,
            is_active=True,
            external_account_id="acct_123",
        )
        PaymentAccount.objects.create(
            user=host_user,
            provider="paystation",
            is_active=True,
            external_account_id="internal_123",
        )

        event_type.payment_provider = "paystation"
        event_type.save()

        provider = select_provider(event_type)
        assert provider.name == "paystation"

    def test_routing_falls_back_correctly(self, host_user, event_type, settings):
        settings.KAIROS_ENABLE_PAYSTATION_ROUTE = True

        PaymentAccount.objects.create(
            user=host_user,
            provider="stripe_connect",
            charges_enabled=True,
            is_active=True,
            external_account_id="acct_456",
        )

        # Stripe is available
        provider = select_provider(event_type)
        assert provider.name == "stripe_connect"

    def test_routing_errors_when_no_provider(self, host_user, event_type):
        provider = select_provider(event_type)
        assert provider is None

    def test_paystation_unavailable_when_flag_off(self, host_user, event_type, settings):
        settings.KAIROS_ENABLE_PAYSTATION_ROUTE = False
        HostPaymentTerms.objects.create(user=host_user, terms_version="1.0")

        PaymentAccount.objects.create(
            user=host_user,
            provider="paystation",
            is_active=True,
            external_account_id="internal_789",
        )
        event_type.payment_provider = "paystation"
        event_type.save()

        provider = select_provider(event_type)
        assert provider is None


@pytest.mark.django_db
class TestPaystationLedger:
    def test_ledger_balance_across_lifecycle(self, host_user, event_type, booking, settings):
        settings.KAIROS_ENABLE_PAYSTATION_ROUTE = True
        HostPaymentTerms.objects.create(user=host_user, terms_version="1.0")

        PaymentAccount.objects.create(
            user=host_user,
            provider="paystation",
            is_active=True,
            external_account_id="internal_101",
        )
        event_type.payment_provider = "paystation"
        event_type.save()

        # 1. Create Payment
        payment = create_payment_for_booking(booking=booking)

        # 2. Confirm Payment
        confirm_payment(payment_uid=str(payment.uid))

        # Check Ledger
        entries = HostLedger.objects.filter(host=host_user)
        assert entries.count() == 2  # Charge, Service Fee

        charge = entries.get(entry_type="charge")
        assert charge.amount_cents == 10000

        service_fee = entries.get(entry_type="service_fee")
        assert service_fee.amount_cents == -300  # 3% of 10000

        from django.db.models import Sum

        balance = HostLedger.objects.filter(host=host_user).aggregate(Sum("amount_cents"))[
            "amount_cents__sum"
        ]
        assert balance == 9700

        # 3. Refund
        handle_refund(payment=payment, amount_cents=10000)

        entries = HostLedger.objects.filter(host=host_user)
        assert entries.count() == 4

        refund = entries.get(entry_type="refund")
        assert refund.amount_cents == -10000

        reversal = entries.get(entry_type="refund_fee_reversal")
        assert reversal.amount_cents == 300

        new_balance = HostLedger.objects.filter(host=host_user).aggregate(Sum("amount_cents"))[
            "amount_cents__sum"
        ]
        assert new_balance == 0


@pytest.mark.django_db
class TestPaystationPayout:
    def test_generate_payout(self, host_user, event_type, booking, settings):
        settings.KAIROS_ENABLE_PAYSTATION_ROUTE = True
        HostPaymentTerms.objects.create(user=host_user, terms_version="1.0")

        # Event type with ৳2000 price to exceed min threshold
        event_type.price_cents = 200000
        event_type.payment_provider = "paystation"
        event_type.save()

        PaymentAccount.objects.create(
            user=host_user,
            provider="paystation",
            is_active=True,
            external_account_id="internal_202",
        )

        payment = create_payment_for_booking(booking=booking)
        confirm_payment(payment_uid=str(payment.uid))

        period_start = timezone.now() - timezone.timedelta(days=1)
        period_end = timezone.now() + timezone.timedelta(days=1)

        payout = generate_payout(host=host_user, period_start=period_start, period_end=period_end)

        assert payout is not None
        assert payout.gross_cents == 200000
        assert payout.fees_cents == 6000
        assert payout.net_cents == 194000

        entries = HostLedger.objects.filter(host=host_user)
        assert entries.count() == 3  # Charge, Service Fee, Payout

        payout_entry = entries.get(entry_type="payout")
        assert payout_entry.amount_cents == -194000

        from django.db.models import Sum

        balance = HostLedger.objects.filter(host=host_user).aggregate(Sum("amount_cents"))[
            "amount_cents__sum"
        ]
        assert balance == 0
