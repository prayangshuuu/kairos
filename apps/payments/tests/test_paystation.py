import pytest
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
import uuid

from apps.payments.models import PaymentAccount, Payment, HostLedger, Payout
from apps.payments.routing import select_provider
from apps.payments.services import create_payment_for_booking, confirm_payment, handle_refund, generate_payout
from apps.scheduling.models import EventType
from apps.bookings.models import Booking

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
        currency="BDT"
    )

@pytest.fixture
def booking(event_type):
    from psycopg.types.range import Range
    start = timezone.now()
    end = start + timezone.timedelta(minutes=30)
    return Booking.objects.create(
        event_type=event_type,
        host=event_type.owner,
        invitee_email="invitee@example.com",
        invitee_name="Invitee",
        start_at=start,
        end_at=end,
        buffered_period=Range(start, end)
    )

@pytest.mark.django_db
class TestProviderRouting:
    
    def test_routing_picks_explicit_choice(self, host_user, event_type, settings):
        settings.KAIROS_ENABLE_PAYSTATION_ROUTE = True
        
        PaymentAccount.objects.create(
            user=host_user, provider="stripe_connect", charges_enabled=True, is_active=True, external_account_id="acct_123"
        )
        PaymentAccount.objects.create(
            user=host_user, provider="paystation", is_active=True, external_account_id="internal_123"
        )
        
        event_type.payment_provider = "paystation"
        event_type.save()
        
        provider = select_provider(event_type)
        assert provider.name == "paystation"

    def test_routing_falls_back_correctly(self, host_user, event_type, settings):
        settings.KAIROS_ENABLE_PAYSTATION_ROUTE = True
        
        PaymentAccount.objects.create(
            user=host_user, provider="stripe_connect", charges_enabled=True, is_active=True, external_account_id="acct_456"
        )
        
        # Stripe is available, paystation not configured
        provider = select_provider(event_type)
        assert provider.name == "stripe_connect"
        
    def test_routing_errors_when_no_provider(self, host_user, event_type):
        provider = select_provider(event_type)
        assert provider is None

    def test_paystation_unavailable_when_flag_off(self, host_user, event_type, settings):
        settings.KAIROS_ENABLE_PAYSTATION_ROUTE = False
        
        PaymentAccount.objects.create(
            user=host_user, provider="paystation", is_active=True, external_account_id="internal_789"
        )
        event_type.payment_provider = "paystation"
        event_type.save()
        
        provider = select_provider(event_type)
        assert provider is None

@pytest.mark.django_db
class TestPaystationLedger:
    
    def test_ledger_balance_across_lifecycle(self, host_user, event_type, booking, settings):
        settings.KAIROS_ENABLE_PAYSTATION_ROUTE = True
        settings.KAIROS_PLATFORM_FEE_PERCENT = 5.0
        
        PaymentAccount.objects.create(
            user=host_user, provider="paystation", is_active=True, external_account_id="internal_101"
        )
        event_type.payment_provider = "paystation"
        event_type.save()
        
        # 1. Create Payment
        payment = create_payment_for_booking(booking=booking)
        
        # 2. Confirm Payment
        confirm_payment(payment_uid=str(payment.uid))
        
        # Check Ledger
        entries = HostLedger.objects.filter(host=host_user)
        assert entries.count() == 3  # Charge, Platform Fee, Gateway Fee
        
        charge = entries.get(entry_type="charge")
        assert charge.amount_cents == 10000
        
        platform_fee = entries.get(entry_type="platform_fee")
        assert platform_fee.amount_cents == -500  # 5% of 10000
        
        gateway_fee = entries.get(entry_type="gateway_fee")
        assert gateway_fee.amount_cents == -250  # 2.5% of 10000
        
        from django.db.models import Sum
        balance = HostLedger.objects.filter(host=host_user).aggregate(Sum('amount_cents'))['amount_cents__sum']
        assert balance == 9250
        
        # 3. Refund
        handle_refund(payment=payment, amount_cents=10000)
        
        entries = HostLedger.objects.filter(host=host_user)
        assert entries.count() == 4
        
        refund = entries.get(entry_type="refund")
        assert refund.amount_cents == -10000
        
        new_balance = HostLedger.objects.filter(host=host_user).aggregate(Sum('amount_cents'))['amount_cents__sum']
        assert new_balance == -750  # Host owes us the fees

@pytest.mark.django_db
class TestPaystationPayout:
    
    def test_generate_payout(self, host_user, event_type, booking, settings):
        settings.KAIROS_ENABLE_PAYSTATION_ROUTE = True
        
        PaymentAccount.objects.create(
            user=host_user, provider="paystation", is_active=True, external_account_id="internal_202"
        )
        event_type.payment_provider = "paystation"
        event_type.save()
        
        payment = create_payment_for_booking(booking=booking)
        confirm_payment(payment_uid=str(payment.uid))
        
        period_start = timezone.now() - timezone.timedelta(days=1)
        period_end = timezone.now() + timezone.timedelta(days=1)
        
        payout = generate_payout(host=host_user, period_start=period_start, period_end=period_end)
        
        assert payout is not None
        assert payout.gross_cents == 10000
        assert payout.fees_cents == 750
        assert payout.net_cents == 9250
        
        entries = HostLedger.objects.filter(host=host_user)
        assert entries.count() == 4
        
        payout_entry = entries.get(entry_type="payout")
        assert payout_entry.amount_cents == -9250
        
        # Balance should now be 0
        from django.db.models import Sum
        balance = HostLedger.objects.filter(host=host_user).aggregate(Sum('amount_cents'))['amount_cents__sum']
        assert balance == 0
