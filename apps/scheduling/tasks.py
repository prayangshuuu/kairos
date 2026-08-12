import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.scheduling.models import DateOverride

logger = logging.getLogger(__name__)


@shared_task
def auto_delete_expired_date_overrides():
    ninety_days_ago = timezone.now().date() - timedelta(days=90)
    count, _ = DateOverride.objects.filter(date__lt=ninety_days_ago).delete()
    if count > 0:
        logger.info(f"Deleted {count} expired date overrides.")
