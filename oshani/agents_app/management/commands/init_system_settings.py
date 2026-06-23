"""Django management command to initialize system settings."""
from django.core.management.base import BaseCommand
from agents_app.models import SystemSettings


class Command(BaseCommand):
    help = 'Initialize default system settings'

    def handle(self, *args, **options):
        self.stdout.write('Initializing system settings...')
        
        # Default conversation retention period (30 days)
        SystemSettings.set_setting(
            key='conversation_retention_days',
            value=30,
            description='Number of days to keep conversations without messages before deletion. Default: 30 days.'
        )
        
        self.stdout.write(self.style.SUCCESS('System settings initialized successfully!'))
        self.stdout.write('  - conversation_retention_days: 30 days')
        self.stdout.write('\nYou can modify these settings in the Django admin panel under "System Settings".')

