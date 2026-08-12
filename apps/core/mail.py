import logging
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from apps.bookings.models import NotificationLog
from apps.core.models import BouncedEmail

logger = logging.getLogger(__name__)

def send_kairos_email(
    to_email: str,
    subject: str,
    template_name: str,
    context: dict,
    reply_to: str = None,
    booking=None,
    notification_kind: str = None,
    ics_data: str = None,
    is_transactional: bool = True,
    attachments: list = None
):
    """
    Central wrapper for all outbound email in Kairos.
    - Handles idempotency via NotificationLog if booking and notification_kind are provided.
    - Suppresses sending to known bounced addresses.
    - Renders HTML and Plaintext variants.
    - Attaches ICS if provided.
    """
    if BouncedEmail.objects.filter(email=to_email).exists():
        logger.warning(f"Suppressed email to {to_email} (on bounce list).")
        return False
        
    # Idempotency check
    if booking and notification_kind:
        if NotificationLog.objects.filter(booking=booking, kind=notification_kind).exists():
            logger.info(f"Skipping {notification_kind} for booking {booking.uid}: already sent.")
            return False

    # Render templates
    html_content = render_to_string(f"emails/{template_name}.html", context)
    text_content = render_to_string(f"emails/{template_name}.txt", context)

    headers = {}
    if not is_transactional:
        # Example header for non-transactional mail, could be enhanced with real unsubscribe URL
        headers["List-Unsubscribe"] = f"<mailto:{settings.DEFAULT_FROM_EMAIL}?subject=unsubscribe>"
        
    reply_to_list = [reply_to] if reply_to else []

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
        reply_to=reply_to_list,
        headers=headers,
    )
    msg.attach_alternative(html_content, "text/html")
    
    if ics_data:
        # Attaching as an alternative with method=REQUEST tells Gmail/Outlook to show the native calendar widget
        msg.attach_alternative(ics_data, "text/calendar; method=REQUEST")
        
    if attachments:
        for attachment in attachments:
            # attachment is a tuple of (filename, content, mimetype)
            msg.attach(*attachment)

    try:
        msg.send(fail_silently=False)
        
        # Log success
        if booking and notification_kind:
            NotificationLog.objects.create(booking=booking, kind=notification_kind)
            
        return True
    except Exception as e:
        # We don't catch transient errors completely here if we want celery to retry.
        # But we log it. Celery task will catch and retry if we raise it.
        logger.error(f"Failed to send email to {to_email}: {e}")
        raise e
