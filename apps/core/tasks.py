from celery import shared_task
from apps.core.mail import send_kairos_email
from apps.bookings.models import Booking

@shared_task(bind=True, max_retries=5, rate_limit='10/s')
def send_email_async(self, to_email, subject, template_name, context, reply_to=None, booking_id=None, notification_kind=None, ics_data=None, is_transactional=True, attachments=None):
    booking = None
    if booking_id:
        try:
            booking = Booking.objects.get(id=booking_id)
        except Booking.DoesNotExist:
            pass
            
    try:
        send_kairos_email(
            to_email=to_email,
            subject=subject,
            template_name=template_name,
            context=context,
            reply_to=reply_to,
            booking=booking,
            notification_kind=notification_kind,
            ics_data=ics_data,
            is_transactional=is_transactional,
            attachments=attachments
        )
    except Exception as e:
        # Exponential backoff
        raise self.retry(exc=e, countdown=2 ** self.request.retries)
