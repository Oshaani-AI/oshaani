"""Management command to clear agent response cache."""
from django.core.management.base import BaseCommand, CommandError
from agents_app.models import Agent
from agents_app.cache_utils import invalidate_agent_cache


class Command(BaseCommand):
    help = 'Clear cached responses for one or all agents'

    def add_arguments(self, parser):
        parser.add_argument(
            '--agent-id',
            type=int,
            help='Clear cache for a specific agent ID',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Clear cache for all agents',
        )

    def handle(self, *args, **options):
        agent_id = options.get('agent_id')
        clear_all = options.get('all', False)
        
        if not agent_id and not clear_all:
            raise CommandError('You must specify either --agent-id or --all')
        
        if agent_id and clear_all:
            raise CommandError('Cannot specify both --agent-id and --all')
        
        if agent_id:
            try:
                agent = Agent.objects.get(id=agent_id)
                invalidate_agent_cache(agent_id)
                self.stdout.write(
                    self.style.SUCCESS(f'Successfully cleared cache for agent {agent_id} ({agent.name})')
                )
            except Agent.DoesNotExist:
                raise CommandError(f'Agent with ID {agent_id} does not exist')
        
        if clear_all:
            agents = Agent.objects.all()
            count = 0
            for agent in agents:
                invalidate_agent_cache(agent.id)
                count += 1
            self.stdout.write(
                self.style.SUCCESS(f'Successfully cleared cache for {count} agents')
            )








