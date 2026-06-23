"""Django management command to sync available models on-demand."""
from django.core.management.base import BaseCommand
from agents_app.tasks import sync_bedrock_models, sync_ollama_models


class Command(BaseCommand):
    help = 'Sync available models from Bedrock and Ollama to database (on-demand). Only syncs Bedrock models that support ON_DEMAND inference (no provisioning required).'

    def handle(self, *args, **options):
        self.stdout.write('Starting model sync...')
        try:
            # Run sync synchronously for management command
            sync_bedrock_models()
            sync_ollama_models()
            self.stdout.write(self.style.SUCCESS('Model sync completed successfully!'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error syncing models: {str(e)}'))

