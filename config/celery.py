import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')

app = Celery('kairos')

app.config_from_object('django.conf:settings', namespace='CELERY')

app.autodiscover_tasks()

from celery.schedules import crontab

app.conf.beat_schedule = {
    'auto_delete_expired_date_overrides': {
        'task': 'apps.scheduling.tasks.auto_delete_expired_date_overrides',
        'schedule': crontab(hour=0, minute=0),  # run daily at midnight
    },
}
