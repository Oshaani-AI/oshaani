"""Signals for blog app."""
import logging
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import BlogPost

logger = logging.getLogger(__name__)


@receiver(post_save, sender=BlogPost)
def on_blog_post_saved(sender, instance, created, **kwargs):
    """When a post is saved as published, queue notification emails (once per post)."""
    if instance.status != 'published':
        return
    if instance.notification_sent:
        return
    # Only send for posts that are visible (published_at in the past or now)
    from django.utils import timezone
    if instance.published_at and instance.published_at > timezone.now():
        return

    from django.conf import settings
    if not getattr(settings, 'SEND_BLOG_NOTIFICATION_EMAILS', True):
        return

    try:
        from .tasks import send_blog_post_notification_emails
        task = send_blog_post_notification_emails.delay(instance.pk)
        BlogPost.objects.filter(pk=instance.pk).update(notification_sent=True)
        logger.info(f"[Blog notification] Queued task {task.id} for post_id={instance.pk} ({instance.title})")
    except Exception as e:
        logger.exception(f"[Blog notification] Failed to queue task for post_id={instance.pk}: {e}")
