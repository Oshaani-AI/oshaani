"""Django management command to verify an agent API key."""
from django.core.management.base import BaseCommand, CommandError
from agents_app.models import Agent
from agents_app.utils import hash_api_key, verify_api_key


class Command(BaseCommand):
    help = 'Verify if an agent API key is valid'

    def add_arguments(self, parser):
        parser.add_argument(
            'api_key',
            type=str,
            help='The API key to verify'
        )
        parser.add_argument(
            '--agent-id',
            type=int,
            help='Optional: Check specific agent ID'
        )

    def handle(self, *args, **options):
        api_key = options['api_key']
        agent_id = options.get('agent_id')
        
        if not api_key:
            raise CommandError('API key is required')
        
        # Calculate hash for comparison
        api_key_hash = hash_api_key(api_key)
        self.stdout.write(f'API Key Hash: {api_key_hash}')
        self.stdout.write('')
        
        # If agent_id provided, check that specific agent
        if agent_id:
            try:
                agent = Agent.objects.get(id=agent_id)
                self.stdout.write(f'Checking Agent ID {agent_id}: {agent.name}')
                self.stdout.write(f'Status: {agent.status}')
                self.stdout.write(f'Has API Key Hash: {bool(agent.api_key_hash)}')
                
                if agent.api_key_hash:
                    self.stdout.write(f'Stored Hash: {agent.api_key_hash}')
                    is_valid = agent.verify_api_key(api_key)
                    self.stdout.write(f'Key Valid: {is_valid}')
                    
                    if is_valid:
                        self.stdout.write(self.style.SUCCESS('✓ API key is VALID for this agent'))
                    else:
                        self.stdout.write(self.style.ERROR('✗ API key is INVALID for this agent'))
                        self.stdout.write('Possible reasons:')
                        self.stdout.write('  - Key was regenerated')
                        self.stdout.write('  - Wrong agent ID')
                        self.stdout.write('  - Key hash mismatch')
                else:
                    self.stdout.write(self.style.WARNING('Agent has no API key hash stored'))
            except Agent.DoesNotExist:
                raise CommandError(f'Agent with ID {agent_id} does not exist')
        else:
            # Check all published agents
            self.stdout.write('Checking all published agents...')
            self.stdout.write('')
            
            agents = Agent.objects.filter(
                api_key_hash__isnull=False,
                status='published'
            ).exclude(api_key_hash='')
            
            self.stdout.write(f'Found {agents.count()} published agents with API keys')
            self.stdout.write('')
            
            found_match = False
            for agent in agents:
                try:
                    if agent.verify_api_key(api_key):
                        found_match = True
                        self.stdout.write(self.style.SUCCESS(f'✓ MATCH FOUND!'))
                        self.stdout.write(f'  Agent ID: {agent.id}')
                        self.stdout.write(f'  Agent Name: {agent.name}')
                        self.stdout.write(f'  User: {agent.user.username} ({agent.user.email})')
                        self.stdout.write(f'  Status: {agent.status}')
                        self.stdout.write(f'  Published At: {agent.published_at}')
                        self.stdout.write('')
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f'Error checking agent {agent.id}: {str(e)}'))
            
            if not found_match:
                self.stdout.write(self.style.ERROR('✗ No matching agent found for this API key'))
                self.stdout.write('')
                self.stdout.write('Possible reasons:')
                self.stdout.write('  1. API key was regenerated')
                self.stdout.write('  2. Agent is not published')
                self.stdout.write('  3. API key is incorrect')
                self.stdout.write('  4. Agent was deleted')
                self.stdout.write('')
                self.stdout.write('To regenerate the API key:')
                self.stdout.write('  1. Go to Django Admin: /admin/agents_app/agent/')
                self.stdout.write('  2. Select the agent')
                self.stdout.write('  3. Use "Regenerate API key" action')
                self.stdout.write('  OR')
                self.stdout.write('  4. Go to Dashboard and regenerate from agent detail page')











