from django.apps import AppConfig


class AgentsAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'agents_app'
    verbose_name = 'Agents'

    def ready(self):
        """Import signals when app is ready."""
        import agents_app.signals  # noqa
