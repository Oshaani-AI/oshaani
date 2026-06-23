"""Preload an Ollama model into memory."""
from django.core.management.base import BaseCommand
from django.conf import settings

from agents_app.ollama_integration import get_ollama_client


class Command(BaseCommand):
    help = 'Preload an Ollama model into memory to reduce first-request latency.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--model',
            default=None,
            help='Model name (default: OLLAMA_WARMUP_MODEL or OLLAMA_DEFAULT_MODEL)',
        )

    def handle(self, *args, **options):
        client = get_ollama_client()
        if not client:
            self.stderr.write(self.style.ERROR('Ollama is not configured or not reachable.'))
            return

        model = options['model'] or getattr(settings, 'OLLAMA_WARMUP_MODEL', None) or client.default_model
        self.stdout.write(f'Warming up Ollama model: {model}')

        if client.warmup_model(model):
            self.stdout.write(self.style.SUCCESS(f'Model loaded: {model}'))
        else:
            self.stderr.write(self.style.ERROR(f'Warmup failed for: {model}'))
