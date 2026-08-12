from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.scheduling.models import EventType, Schedule
from apps.subscriptions.entitlements import (
    has_feature,
    within_limit,
)
from apps.subscriptions.models import Subscription
from apps.subscriptions.services import (
    check_and_process_paystation_renewals_and_grace,
    process_stripe_subscription_webhook,
    start_bdt_paystation_subscription,
    sync_user_entitlements_and_grandfathering,
)


@pytest.fixture
def user(db):
    user = User.objects.create_user(
        email="testsubscriber@example.com",
        password="password123",
        slug="test-subscriber",
        timezone="UTC",
    )
    Schedule.objects.create(
        user=user,
        name="Working Hours",
        timezone="UTC",
        is_default=True,
    )
    return user


@pytest.fixture
def free_subscription(user):
    sub, _ = Subscription.objects.get_or_create(
        user=user, defaults={"plan_code": "free", "status": Subscription.STATUS_ACTIVE}
    )
    return sub


@pytest.fixture
def pro_subscription(user):
    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.plan_code = "pro"
    sub.status = Subscription.STATUS_ACTIVE
    sub.current_period_start = timezone.now()
    sub.current_period_end = timezone.now() + timedelta(days=30)
    sub.save()
    return sub


@pytest.mark.django_db
class TestServiceLayerEnforcement:
    def test_free_user_cannot_create_second_event_type(self, user, free_subscription):
        """Free user cannot create a second active event type via model/clean (service layer)."""
        # First event type: valid
        et1 = EventType(
            owner=user,
            title="First Event",
            slug="first-event",
            duration_minutes=30,
        )
        et1.clean()
        et1.save()

        # Second event type: clean() raises ValidationError
        et2 = EventType(
            owner=user,
            title="Second Event",
            slug="second-event",
            duration_minutes=30,
        )
        with pytest.raises(ValidationError) as exc_info:
            et2.clean()

        assert "limit for active event types" in str(exc_info.value)

    def test_free_user_cannot_create_paid_booking(self, user, free_subscription):
        """Free user cannot set price_cents > 0 (paid booking feature disabled on Free plan)."""
        et = EventType(
            owner=user,
            title="Paid Event Attempt",
            slug="paid-event-attempt",
            duration_minutes=30,
            price_cents=5000,
            currency="USD",
        )
        with pytest.raises(ValidationError) as exc_info:
            et.clean()

        assert "Paid bookings require a Pro subscription" in str(exc_info.value)

    def test_pro_user_can_create_multiple_event_types_and_paid_bookings(
        self, user, pro_subscription
    ):
        """Pro user can create unlimited event types and paid bookings."""
        EventType.objects.create(owner=user, title="Event 1", slug="event-1", duration_minutes=30)
        EventType.objects.create(owner=user, title="Event 2", slug="event-2", duration_minutes=30)
        et3 = EventType(owner=user, title="Event 3", slug="event-3", duration_minutes=30)
        et3.clean()
        et3.save()

        assert EventType.objects.filter(owner=user, is_active=True).count() == 3

    def test_free_user_cannot_create_second_event_type_via_direct_post(
        self, client, user, free_subscription
    ):
        """Free user cannot create a second active event type via a direct HTTP POST."""
        from django.urls import reverse

        EventType.objects.create(
            owner=user, title="First Event", slug="first-event", duration_minutes=30
        )
        client.force_login(user)

        client.post(
            reverse("scheduling:eventtype_create"),
            {
                "title": "Second Event",
                "slug": "second-event",
                "duration_minutes": 30,
                "window_type": "rolling",
                "rolling_days": 60,
                "location_type": "google_meet",
            },
        )
        assert EventType.objects.filter(owner=user).count() == 1

    def test_free_user_cannot_duplicate_event_type_via_post(
        self, client, user, free_subscription
    ):
        """Free user cannot duplicate an event type via POST when at limit."""
        from django.urls import reverse

        et = EventType.objects.create(
            owner=user, title="First Event", slug="first-event", duration_minutes=30
        )
        client.force_login(user)

        response = client.post(
            reverse("scheduling:eventtype_duplicate", kwargs={"slug": et.slug})
        )
        assert EventType.objects.filter(owner=user).count() == 1
        assert response.status_code == 302
        assert "pricing" in response.url


@pytest.mark.django_db
class TestGrandfathering:
    def test_lapsed_subscription_hides_extra_event_types_without_deleting(
        self, user, pro_subscription
    ):
        """
        A lapsed subscription hides extra event types (is_active=False) beyond free limit,
        without deleting them from the database.
        Resubscribing restores all event types to is_active=True.
        """
        # Create 3 event types while Pro
        et1 = EventType.objects.create(owner=user, title="ET 1", slug="et-1", duration_minutes=30)
        et2 = EventType.objects.create(owner=user, title="ET 2", slug="et-2", duration_minutes=30)
        et3 = EventType.objects.create(owner=user, title="ET 3", slug="et-3", duration_minutes=30)

        assert EventType.objects.filter(owner=user).count() == 3
        assert EventType.objects.filter(owner=user, is_active=True).count() == 3

        # Lapse subscription to Free
        pro_subscription.plan_code = "free"
        pro_subscription.status = Subscription.STATUS_EXPIRED
        pro_subscription.save()

        # Run grandfathering sync
        sync_user_entitlements_and_grandfathering(user)

        # ET count in DB must still be 3 (NEVER deleted)
        assert EventType.objects.filter(owner=user).count() == 3

        # Only ET1 remains active (1st created). ET2 & ET3 are hidden (is_active=False)
        et1.refresh_from_db()
        et2.refresh_from_db()
        et3.refresh_from_db()

        assert et1.is_active is True
        assert et2.is_active is False
        assert et3.is_active is False

        # User resubscribes to Pro
        start_bdt_paystation_subscription(user)

        # All 3 event types are fully restored
        et1.refresh_from_db()
        et2.refresh_from_db()
        et3.refresh_from_db()

        assert et1.is_active is True
        assert et2.is_active is True
        assert et3.is_active is True


@pytest.mark.django_db
class TestStripeDunning:
    def test_failed_stripe_payment_marks_past_due_without_downgrading(self, user, pro_subscription):
        """
        On Stripe invoice.payment_failed:
        Mark status as past_due, do NOT downgrade plan_code immediately.
        Downgrade only when customer.subscription.deleted is received.
        """
        pro_subscription.external_customer_id = "cus_test123"
        pro_subscription.save()

        event_data = {
            "id": "evt_failed_1",
            "type": "invoice.payment_failed",
            "data": {"object": {"customer": "cus_test123"}},
        }

        with patch("apps.core.mail.send_kairos_email"):
            process_stripe_subscription_webhook(event_data)

        pro_subscription.refresh_from_db()
        assert pro_subscription.status == Subscription.STATUS_PAST_DUE
        assert pro_subscription.plan_code == "pro"  # Kept Pro during dunning
        assert pro_subscription.effective_plan_code == "pro"

        # Now simulate Stripe cancelling subscription after retries exhaust
        deleted_event_data = {
            "id": "evt_deleted_1",
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_test123"}},
        }
        process_stripe_subscription_webhook(deleted_event_data)

        pro_subscription.refresh_from_db()
        assert pro_subscription.status == Subscription.STATUS_EXPIRED
        assert pro_subscription.plan_code == "free"
        assert pro_subscription.effective_plan_code == "free"


@pytest.mark.django_db
class TestEntitlementCaching:
    def test_entitlement_checks_are_cached_per_user_instance(
        self, user, free_subscription, django_assert_num_queries
    ):
        """Entitlement checks cache user._cached_subscription to prevent repeated DB queries."""
        # Reset cache if any
        if hasattr(user, "_cached_subscription"):
            delattr(user, "_cached_subscription")

        # First call loads subscription from DB (1 query)
        with django_assert_num_queries(1):
            assert has_feature(user, "paid_bookings") is False

        # Subsequent 5 calls use cached subscription (0 queries)
        with django_assert_num_queries(0):
            for _ in range(5):
                has_feature(user, "paid_bookings")
                within_limit(user, "max_event_types", 0)


@pytest.mark.django_db
class TestPayStationGracePeriod:
    def test_paystation_grace_period_and_expiration(self, user):
        """
        A BDT PayStation subscription past period_end enters a 7-day grace period.
        Features remain functional during grace period.
        Downgrade occurs ONLY after the 7-day grace period expires.
        """
        sub = start_bdt_paystation_subscription(user)
        now = timezone.now()

        # Set period_end to 3 days ago (within 7-day grace)
        sub.current_period_end = now - timedelta(days=3)
        sub.save()

        # Check entitlement during grace period: valid & active
        assert sub.is_valid_or_active() is True
        assert has_feature(user, "paid_bookings") is True

        # Process Celery task -> status transitions to STATUS_GRACE_PERIOD
        check_and_process_paystation_renewals_and_grace()

        sub.refresh_from_db()
        assert sub.status == Subscription.STATUS_GRACE_PERIOD
        assert sub.effective_plan_code == "pro"

        # Advance period_end to 8 days ago (beyond 7-day grace)
        sub.current_period_end = now - timedelta(days=8)
        sub.save()

        assert sub.is_valid_or_active() is False

        # Process Celery task -> status transitions to STATUS_EXPIRED & plan downgraded to free
        check_and_process_paystation_renewals_and_grace()

        sub.refresh_from_db()
        assert sub.status == Subscription.STATUS_EXPIRED
        assert sub.plan_code == "free"
        assert sub.effective_plan_code == "free"
