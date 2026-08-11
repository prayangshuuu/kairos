import os

# Rewrite root conftest to include factories and host_with_schedule
with open('conftest.py', 'w') as f:
    f.write("""import pytest
import factory
from pytest_factoryboy import register
from django.utils import timezone as django_timezone
import datetime
from apps.accounts.models import User
from apps.scheduling.models import Schedule, AvailabilityRule, EventType
from apps.bookings.models import Booking

class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
    email = factory.Sequence(lambda n: f"user{n}@example.com")
    slug = factory.Sequence(lambda n: f"user-{n}")
    timezone = "Asia/Dhaka"

class ScheduleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Schedule
    owner = factory.SubFactory(UserFactory)
    name = "Test Schedule"
    timezone = "Asia/Dhaka"

class EventTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EventType
    owner = factory.SubFactory(UserFactory)
    title = "Test Event"
    slug = "test-event"
    duration_minutes = 30

register(UserFactory)
register(ScheduleFactory)
register(EventTypeFactory)

@pytest.fixture
def frozen_now():
    return django_timezone.make_aware(datetime.datetime(2026, 8, 12, 12, 0, 0))

@pytest.fixture
def host_with_schedule(db):
    user = User.objects.create_user(
        email="host@example.com",
        password="password",
        slug="host",
        timezone="Asia/Dhaka",
        display_name="Host User"
    )
    
    schedule = Schedule.objects.create(
        owner=user,
        name="Working Hours",
        timezone="Asia/Dhaka",
        is_default=True
    )
    
    for weekday in range(5):
        AvailabilityRule.objects.create(
            schedule=schedule,
            weekday=weekday,
            start_time=datetime.time(9, 0),
            end_time=datetime.time(17, 0)
        )
        
    EventType.objects.create(
        owner=user,
        title="30 Minute Free",
        slug="30-min",
        duration_minutes=30,
        price=0,
        schedule=schedule
    )
    
    EventType.objects.create(
        owner=user,
        title="60 Minute Paid",
        slug="60-min",
        duration_minutes=60,
        price=50.00,
        schedule=schedule
    )
    
    return user
""")

os.makedirs('apps/bookings/tests', exist_ok=True)
with open('apps/bookings/tests/test_e2e.py', 'w') as f:
    f.write("""import pytest
from django.urls import reverse
from apps.bookings.models import Booking
from apps.scheduling.models import EventType

@pytest.mark.django_db
def test_e2e_happy_path(client, host_with_schedule):
    # 1. Visitor loads profile
    res = client.get(reverse('bookings:public_profile', kwargs={'slug': 'host'}))
    assert res.status_code == 200
    
    # 2. Opens booking page
    res = client.get(reverse('bookings:booking_page', kwargs={'host_slug': 'host', 'event_slug': '30-min'}))
    assert res.status_code == 200
    
    # 4. Submits form
    et = EventType.objects.get(slug='30-min')
    from datetime import datetime, timedelta
    from django.utils import timezone
    now = timezone.now()
    future = now + timedelta(days=2)
    start_at = future.replace(hour=10, minute=0, second=0, microsecond=0)
    
    res = client.post(reverse('bookings:booking_page', kwargs={'host_slug': 'host', 'event_slug': '30-min'}), {
        'start_time': start_at.isoformat(),
        'invitee_name': 'Invitee',
        'invitee_email': 'invitee@example.com',
        'invitee_timezone': 'America/Los_Angeles'
    })
    
    # Booking is created and redirects to confirmation
    assert res.status_code == 302
    assert Booking.objects.count() == 1
    
    booking = Booking.objects.first()
    assert booking.invitee_email == 'invitee@example.com'
    assert 'booking/' in res.url
""")

with open('apps/bookings/tests/test_permissions.py', 'w') as f:
    f.write("""import pytest
from django.urls import reverse

@pytest.mark.django_db
def test_dashboard_requires_login(client):
    res = client.get(reverse('bookings:dashboard_bookings'))
    assert res.status_code == 302
    assert 'login' in res.url
""")

