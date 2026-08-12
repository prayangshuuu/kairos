import logging
from celery import shared_task
from apps.workflows.services import execute_due_workflows

logger = logging.getLogger(__name__)


@shared_task
def process_due_workflow_executions():
    """
    Celery beat task running every minute to process due workflow executions.
    """
    count = execute_due_workflows()
    if count > 0:
        logger.info(f"Processed {count} due workflow execution(s).")
    return count
