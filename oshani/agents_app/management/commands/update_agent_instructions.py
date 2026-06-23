"""Django management command to update agent instructions."""
from django.core.management.base import BaseCommand, CommandError
from agents_app.models import Agent


class Command(BaseCommand):
    help = 'Update instructions/system prompt for a specific agent'

    def add_arguments(self, parser):
        parser.add_argument(
            'agent_id',
            type=int,
            help='ID of the agent to update'
        )
        parser.add_argument(
            '--instruction',
            type=str,
            help='New instruction text to set'
        )
        parser.add_argument(
            '--file',
            type=str,
            help='Path to file containing instructions'
        )

    def handle(self, *args, **options):
        agent_id = options['agent_id']
        instruction_text = options.get('instruction')
        instruction_file = options.get('file')

        try:
            agent = Agent.objects.get(id=agent_id)
        except Agent.DoesNotExist:
            raise CommandError(f'Agent with ID {agent_id} does not exist')

        # Get instruction from file or direct input
        if instruction_file:
            try:
                with open(instruction_file, 'r', encoding='utf-8') as f:
                    instruction_text = f.read().strip()
            except FileNotFoundError:
                raise CommandError(f'File not found: {instruction_file}')
            except Exception as e:
                raise CommandError(f'Error reading file: {str(e)}')
        elif not instruction_text:
            raise CommandError('Either --instruction or --file must be provided')

        # Initialize configuration if needed
        if not agent.configuration:
            agent.configuration = {}

        # Update instructions
        agent.configuration['instruction'] = instruction_text
        agent.configuration['system_prompt'] = instruction_text  # Keep both for compatibility

        # Save agent
        agent.save()

        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully updated instructions for agent "{agent.name}" (ID: {agent_id})'
            )
        )
        self.stdout.write(f'Current instruction length: {len(instruction_text)} characters')












