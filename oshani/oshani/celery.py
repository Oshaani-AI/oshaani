"""Celery configuration for oshani project."""
import os
import logging
from celery import Celery
from celery.signals import task_failure

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'oshani.settings')

app = Celery('oshani')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
# This will discover tasks in all installed apps that have a 'tasks' module
app.autodiscover_tasks()

logger = logging.getLogger(__name__)


def send_celery_error_email(task_name, error_message, traceback_info, task_id=None, retries=None):
    """Send email notification when a Celery task fails."""
    try:
        from django.conf import settings
        from agents_app.email_utils import send_email_with_fallback
        
        site_url = getattr(settings, 'SITE_URL', 'https://oshaani.com')
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'support@oshaani.com')
        recipient_email = 'support@oshaani.com'
        
        subject = f"⚠️ Celery Task Failure: {task_name}"
        
        html_message = None

        message = f"""
CELERY TASK FAILURE ALERT

Task Name: {task_name}
Task ID: {task_id or 'N/A'}
Retries: {retries or 'N/A'}

Error Message:
{error_message}

Traceback:
{traceback_info or 'No traceback available'}

This is an automated alert from the OSHAANI Celery task monitoring system.
Please investigate this task failure immediately.

Site: {site_url}
        """.strip()
        
        # Send email
        success = send_email_with_fallback(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=[recipient_email],
            html_message=html_message,
            fail_silently=False
        )
        
        logger.info(f"Sent Celery task error email for {task_name} (task_id: {task_id}): {success}")
        return success
        
    except Exception as e:
        logger.error(f"Failed to send Celery task error email: {str(e)}", exc_info=True)
        return False


@task_failure.connect
def task_failure_handler(sender=None, task_id=None, exception=None, traceback=None, einfo=None, **kwargs):
    """Handle Celery task failures and send email notifications."""
    try:
        # Ensure Django is initialized
        import django
        django.setup()
        
        task_name = sender.name if sender else 'Unknown Task'
        
        # Get error details
        error_message = str(exception) if exception else 'Unknown error'
        traceback_info = None
        
        if traceback:
            traceback_info = str(traceback)
        elif einfo:
            traceback_info = str(einfo)
        
        # Get retry information
        retries = None
        if sender and hasattr(sender, 'request') and hasattr(sender.request, 'retries'):
            retries = sender.request.retries
        
        logger.error(
            f"Celery task failure: {task_name} (task_id: {task_id}). "
            f"Error: {error_message}",
            exc_info=True
        )
        
        # Send email notification
        send_celery_error_email(
            task_name=task_name,
            error_message=error_message,
            traceback_info=traceback_info,
            task_id=task_id,
            retries=retries
        )
        
    except Exception as e:
        logger.error(f"Error in task_failure_handler: {str(e)}", exc_info=True)


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')




