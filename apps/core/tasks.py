import contextlib
import logging
import smtplib

from celery import shared_task

from apps.bookings.models import Booking
from apps.core.mail import send_kairos_email

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=5, rate_limit="10/s")
def send_email_async(
    self,
    to_email,
    subject,
    template_name,
    context,
    reply_to=None,
    booking_id=None,
    notification_kind=None,
    ics_data=None,
    is_transactional=True,
    attachments=None,
):
    booking = None
    if booking_id:
        with contextlib.suppress(Booking.DoesNotExist):
            booking = Booking.objects.get(id=booking_id)

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
            attachments=attachments,
        )
    except smtplib.SMTPResponseException as e:
        if 400 <= e.smtp_code < 500:
            raise self.retry(exc=e, countdown=2**self.request.retries)
        else:
            logger.error(
                f"Permanent email failure to {to_email} for booking {booking.uid if booking else 'None'}: {e}"
            )
    except smtplib.SMTPConnectError as e:
        raise self.retry(exc=e, countdown=2**self.request.retries)
    except smtplib.SMTPServerDisconnected as e:
        raise self.retry(exc=e, countdown=2**self.request.retries)
    except OSError as e:
        # Transient network issues
        raise self.retry(exc=e, countdown=2**self.request.retries)
    except Exception as e:
        logger.error(
            f"Unexpected email failure to {to_email} for booking {booking.uid if booking else 'None'}: {e}"
        )
        # Only retry some types of generic exceptions? We will assume others are not transient.
