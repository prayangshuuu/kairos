import pytest
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
