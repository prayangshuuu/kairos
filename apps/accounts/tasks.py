from celery import shared_task
from django.utils import timezone as django_timezone
from apps.accounts.models import User
from apps.accounts.services import anonymize_user
from apps.bookings.models import Booking
from apps.scheduling.models import EventType, Schedule
import json
from django.core.mail import EmailMessage

@shared_task
def run_account_anonymization(user_id):
    try:
        user = User.objects.get(id=user_id)
        if user.is_active:  # If user reactivated, abort (though we didn't build reactivation yet)
            anonymize_user(user)
    except User.DoesNotExist:
        pass

@shared_task
def export_user_data(user_id):
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return
        
    data = {
        'profile': {
            'email': user.email,
            'display_name': user.display_name,
            'slug': user.slug,
            'timezone': user.timezone,
            'locale': user.locale,
        },
        'event_types': [],
        'schedules': [],
        'bookings': [],
    }
    
    for et in EventType.objects.filter(owner=user):
        data['event_types'].append({
            'title': et.title,
            'slug': et.slug,
            'duration': et.duration_minutes,
        })
        
    for sc in Schedule.objects.filter(owner=user):
        data['schedules'].append({
            'name': sc.name,
            'timezone': sc.timezone,
        })
        
    for b in Booking.objects.filter(host=user):
        data['bookings'].append({
            'uid': str(b.uid),
            'start_at': b.start_at.isoformat(),
            'end_at': b.end_at.isoformat(),
            'status': b.status,
            'invitee_email': b.invitee_email,
        })
        
    # Mock sending email with link (in reality, we'd upload to S3 and generate signed URL)
    json_data = json.dumps(data, indent=2)
    email = EmailMessage(
        subject="Your Kairos Data Export",
        body="Attached is your data export.",
        to=[user.email]
    )
    email.attach('kairos_export.json', json_data, 'application/json')
    email.send()
