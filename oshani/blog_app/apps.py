"""Blog app configuration."""
from django.apps import AppConfig


class BlogAppConfig(AppConfig):
    """Configuration for blog app."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'blog_app'
    verbose_name = 'Blog'

    def ready(self):
        import blog_app.signals  # noqa: F401 - connect post_save for notification emails
