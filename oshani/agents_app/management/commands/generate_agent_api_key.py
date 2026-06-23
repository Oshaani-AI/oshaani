"""Django management command to generate API key for an agent."""
from django.core.management.base import BaseCommand, CommandError
from agents_app.models import Agent


class Command(BaseCommand):
    help = 'Generate API key for an agent'

    def add_arguments(self, parser):
        parser.add_argument(
            'agent_id',
            type=int,
            help='ID of the agent to generate API key for'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force regenerate even if key already exists'
        )

    def handle(self, *args, **options):
        agent_id = options['agent_id']
        force = options.get('force', False)
        
        try:
            agent = Agent.objects.get(id=agent_id)
        except Agent.DoesNotExist:
            raise CommandError(f'Agent with ID {agent_id} does not exist')
        
        self.stdout.write(f'Agent: {agent.name}')
        self.stdout.write(f'Status: {agent.status}')
        self.stdout.write(f'User: {agent.user.username} ({agent.user.email})')
        self.stdout.write(f'Has API Key: {bool(agent.api_key_hash)}')
        self.stdout.write('')
        
        if agent.status != 'published':
            raise CommandError(
                f'Agent is not published (status: {agent.status}). '
                'Only published agents can have API keys. Please publish the agent first.'
            )
        
        if agent.api_key_hash and not force:
            self.stdout.write(self.style.WARNING(
                'Agent already has an API key. Use --force to regenerate.'
            ))
            self.stdout.write(f'Current API Key Preview: {agent.api_key_preview}')
            return
        
        # Generate new API key
        try:
            new_key = agent.generate_api_key()
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('=' * 60))
            self.stdout.write(self.style.SUCCESS('✅ NEW API KEY GENERATED'))
            self.stdout.write(self.style.SUCCESS('=' * 60))
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(new_key))
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('⚠️  IMPORTANT: Save this key now - it will not be shown again!'))
            self.stdout.write('')
            self.stdout.write(f'API Key Hash: {agent.api_key_hash}')
            self.stdout.write(f'API Key Preview: {agent.api_key_preview}')
        except Exception as e:
            raise CommandError(f'Error generating API key: {str(e)}')











