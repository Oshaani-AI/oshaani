"""Django management command to sync training_data_count for all agents."""
from django.core.management.base import BaseCommand
from agents_app.models import Agent


class Command(BaseCommand):
    help = 'Sync training_data_count field with actual count from database for all agents'

    def add_arguments(self, parser):
        parser.add_argument(
            '--agent-id',
            type=int,
            help='Sync only a specific agent by ID',
        )

    def handle(self, *args, **options):
        agent_id = options.get('agent_id')
        
        if agent_id:
            try:
                agent = Agent.objects.get(id=agent_id)
                old_count = agent.training_data_count
                updated = agent.sync_training_data_count()
                actual_count = agent.training_data.count()
                
                if updated:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Agent {agent_id} ({agent.name}): '
                            f'Updated count from {old_count} to {actual_count}'
                        )
                    )
                else:
                    self.stdout.write(
                        f'Agent {agent_id} ({agent.name}): '
                        f'Count already correct ({actual_count})'
                    )
            except Agent.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'Agent with ID {agent_id} not found')
                )
        else:
            self.stdout.write('Syncing training_data_count for all agents...')
            agents = Agent.objects.all()
            updated_count = 0
            total_agents = agents.count()
            
            for agent in agents:
                old_count = agent.training_data_count
                updated = agent.sync_training_data_count()
                if updated:
                    updated_count += 1
                    actual_count = agent.training_data.count()
                    self.stdout.write(
                        f'  Agent {agent.id} ({agent.name}): '
                        f'{old_count} -> {actual_count}'
                    )
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'\nSync completed: {updated_count}/{total_agents} agents updated'
                )
            )

