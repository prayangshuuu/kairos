from celery import shared_task
from .models import PageView

@shared_task(ignore_result=True)
def record_page_view(user_id, event_type_id=None, referrer=""):
    PageView.objects.create(
        user_id=user_id,
        event_type_id=event_type_id,
        referrer=referrer[:500]
    )
