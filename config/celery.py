import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")

app = Celery("kairos")

app.config_from_object("django.conf:settings", namespace="CELERY")

app.autodiscover_tasks()

from celery.schedules import crontab

app.conf.beat_schedule = {
    "auto_delete_expired_date_overrides": {
        "task": "apps.scheduling.tasks.auto_delete_expired_date_overrides",
        "schedule": crontab(hour=0, minute=0),  # run daily at midnight
    },
    "release_expired_slot_holds": {
        "task": "apps.payments.tasks.release_expired_slot_holds",
        "schedule": crontab(),  # every minute
    },
    "reconcile_payments": {
        "task": "apps.payments.tasks.reconcile_payments",
        "schedule": crontab(hour=3, minute=0),  # daily at 3 AM
    },
    "process_due_workflow_executions": {
        "task": "apps.workflows.tasks.process_due_workflow_executions",
        "schedule": crontab(),  # every minute
    },
    "rollup_daily_metrics": {
        "task": "apps.analytics.tasks.rollup_daily_metrics",
        "schedule": crontab(hour=2, minute=0),  # daily at 2 AM
    },
    "cleanup_old_events": {
        "task": "apps.analytics.tasks.cleanup_old_events",
        "schedule": crontab(hour=3, minute=30, day_of_week=0),  # weekly on Sunday 3:30 AM
    },
    "send_monthly_analytics_summary": {
        "task": "apps.analytics.tasks.send_monthly_analytics_summary",
        "schedule": crontab(hour=8, minute=0, day_of_month=1),  # monthly on the 1st at 8 AM
    },
}
