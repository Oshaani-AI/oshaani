"""Celery tasks for blog app."""
import logging
import time
from celery import shared_task
from django.conf import settings
from django.contrib.auth.models import User
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

# SES-friendly: send in small batches with delay to stay under rate limits (e.g. 14/sec)
BLOG_NOTIFICATION_BATCH_SIZE = getattr(settings, 'BLOG_NOTIFICATION_BATCH_SIZE', 14)
BLOG_NOTIFICATION_DELAY_SECONDS = getattr(settings, 'BLOG_NOTIFICATION_DELAY_SECONDS', 1.0)


@shared_task(bind=True)
def send_blog_post_notification_emails(self, post_id):
    """
    Send a beautiful notification email to all active users when a blog post is published.
    Batches sends and adds delay to respect AWS SES rate limits.
    """
    from blog_app.models import BlogPost
    from agents_app.email_utils import send_email_with_fallback

    logger.info(f"[Blog notification] Starting for post_id={post_id}")

    try:
        post = BlogPost.objects.select_related('author', 'category').prefetch_related('tags').get(pk=post_id)
    except BlogPost.DoesNotExist:
        logger.warning(f"[Blog notification] Post {post_id} not found")
        return {'success': False, 'error': 'Post not found', 'post_id': post_id}

    if post.status != 'published':
        logger.info(f"[Blog notification] Post {post_id} is not published, skipping")
        return {'success': False, 'error': 'Post not published', 'post_id': post_id}

    # Recipients: all active users with email, excluding the post author
    author_id = post.author_id
    recipients = list(
        User.objects.filter(is_active=True)
        .exclude(email='')
        .exclude(pk=author_id)
        .values_list('email', flat=True)
        .distinct()
    )

    if not recipients:
        logger.info(f"[Blog notification] No recipients for post_id={post_id}")
        return {'success': True, 'sent': 0, 'post_id': post_id}

    site_url = getattr(settings, 'SITE_URL', 'https://oshaani.com').rstrip('/')
    post_url = f"{site_url}{post.get_absolute_url()}"
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'support@oshaani.com')

    # Build plain text and HTML once
    category_name = post.category.name if post.category else None
    tags_str = ', '.join(t.name for t in post.tags.all()) if post.tags.exists() else None
    excerpt = (post.excerpt or post.content[:200])[:300]

    subject = f"New post: {post.title}"
    plain_message = f"""Hi,

We've just published a new post you might enjoy:

"{post.title}"

{excerpt}...

Read more: {post_url}

—
{getattr(settings, 'SITE_NAME', 'Oshaani')}
"""

    html_message = render_to_string('blog_app/emails/new_post_notification.html', {
        'post': post,
        'post_url': post_url,
        'site_url': site_url,
        'category_name': category_name,
        'tags_str': tags_str,
        'excerpt': excerpt,
    })

    sent = 0
    failed = 0
    batch_size = BLOG_NOTIFICATION_BATCH_SIZE
    delay_sec = BLOG_NOTIFICATION_DELAY_SECONDS

    for i in range(0, len(recipients), batch_size):
        batch = recipients[i:i + batch_size]
        for email in batch:
            try:
                send_email_with_fallback(
                    subject=subject,
                    message=plain_message,
                    from_email=from_email,
                    recipient_list=[email],
                    html_message=html_message,
                    fail_silently=True,
                )
                sent += 1
            except Exception as e:
                logger.warning(f"[Blog notification] Failed to send to {email}: {e}")
                failed += 1
        if i + batch_size < len(recipients):
            time.sleep(delay_sec)

    logger.info(f"[Blog notification] Post {post_id}: sent={sent}, failed={failed}")
    return {'success': True, 'sent': sent, 'failed': failed, 'post_id': post_id}
