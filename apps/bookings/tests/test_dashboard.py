from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone as django_timezone
from datetime import timedelta
import uuid

from apps.accounts.models import User
from apps.scheduling.models import EventType
from apps.bookings.models import Booking, Attendee

class BookingQueryCountTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.host = User.objects.create_user(email="host@example.com", password="password")
        self.host.slug = "host"
        self.host.save()
        
        self.event_type = EventType.objects.create(
            owner=self.host,
            title="Test Event",
            slug="test-event",
            duration_minutes=30
        )
        
        # Create 25 bookings with attendees
        now = django_timezone.now()
        for i in range(25):
            start = now + timedelta(days=1, hours=i)
            b = Booking.objects.create(
                event_type=self.event_type,
                host=self.host,
                start_at=start,
                end_at=start + timedelta(minutes=30),
                invitee_timezone="UTC",
                status=Booking.StatusChoices.CONFIRMED,
                invitee_name=f"Invitee {i}",
                invitee_email=f"invitee{i}@example.com"
            )
            Attendee.objects.create(booking=b, name=f"Invitee {i}", email=f"invitee{i}@example.com")
            Attendee.objects.create(booking=b, name="Host", email="host@example.com", is_organizer=True)
            
        self.client.force_login(self.host)
        
    def test_dashboard_bookings_list_query_count(self):
        # Expected queries:
        # 1. Session/User lookup
        # 2. Aggregate count for tabs
        # 3. Pagination count (sometimes skipped if total is known, but paginator will do count)
        # 4. Fetch bookings (select_related host/event_type)
        # 5. Prefetch attendees
        # 6. Fetch EventTypes for filters
        
        url = reverse('bookings:dashboard_bookings')
        
        # We expect a bounded number of queries, regardless of 25 bookings.
        # It should be well under 15 queries.
        with self.assertNumQueriesLessThan(15):
            response = self.client.get(url, {'tab': 'upcoming'})
            
        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.context['page_obj']) <= 25)

    def assertNumQueriesLessThan(self, num):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        
        class _AssertNumQueriesLessThanContext(CaptureQueriesContext):
            def __init__(self, connection):
                super().__init__(connection)
                
            def __exit__(self, exc_type, exc_value, traceback):
                super().__exit__(exc_type, exc_value, traceback)
                if exc_type is not None:
                    return
                executed = len(self)
                if executed >= num:
                    self.test_case.fail(
                        f"{executed} queries executed, {num} expected. "
                        f"Queries were: {[q['sql'] for q in self.captured_queries]}"
                    )
                    
        context = _AssertNumQueriesLessThanContext(connection)
        context.test_case = self
        return context
