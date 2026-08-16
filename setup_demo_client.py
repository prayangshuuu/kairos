import os
import django
from datetime import timedelta
from django.utils import timezone

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User
from apps.bookings.models import Booking, EventType
from apps.clients.models import Client

def setup():
    # Get or create a host
    host, _ = User.objects.get_or_create(email="host@example.com", defaults={"username": "host", "is_active": True})
    
    # Get or create an event type
    event_type, _ = EventType.objects.get_or_create(
        host=host, 
        name="Consultation", 
        defaults={"duration": 30, "price": 100, "currency": "USD"}
    )
    
    # Get or create the client
    client, _ = Client.objects.get_or_create(
        host=host,
        email="dave.demo@example.com",
        defaults={
            "name": "Dave Demo",
            "source": "manual"
        }
    )
    
    # Clear existing bookings for this client
    Booking.objects.filter(client=client).delete()
    
    now = timezone.now()
    
    # Create 4 bookings
    # 1. Completed with payment
    Booking.objects.create(
        host=host,
        event_type=event_type,
        client=client,
        invitee_email=client.email,
        invitee_name=client.name,
        start_time=now - timedelta(days=10),
        end_time=now - timedelta(days=10) + timedelta(minutes=30),
        status='completed',
        payment_status='paid',
        payment_amount=100,
        payment_currency='USD'
    )
    
    # 2. Completed with payment and refund
    Booking.objects.create(
        host=host,
        event_type=event_type,
        client=client,
        invitee_email=client.email,
        invitee_name=client.name,
        start_time=now - timedelta(days=5),
        end_time=now - timedelta(days=5) + timedelta(minutes=30),
        status='completed',
        payment_status='refunded',
        payment_amount=100,
        payment_currency='USD'
    )
    
    # 3. Cancelled
    Booking.objects.create(
        host=host,
        event_type=event_type,
        client=client,
        invitee_email=client.email,
        invitee_name=client.name,
        start_time=now - timedelta(days=2),
        end_time=now - timedelta(days=2) + timedelta(minutes=30),
        status='cancelled'
    )
    
    # 4. No-show
    Booking.objects.create(
        host=host,
        event_type=event_type,
        client=client,
        invitee_email=client.email,
        invitee_name=client.name,
        start_time=now - timedelta(days=1),
        end_time=now - timedelta(days=1) + timedelta(minutes=30),
        status='no_show'
    )
    
    print(f"Setup complete. You can log in as {host.email} and view the client at /dashboard/clients/{client.id}/")

if __name__ == "__main__":
    setup()
