"""Management command to validate all connectors."""
from django.core.management.base import BaseCommand
from connectors.models import Connector
from connectors.validator import ConnectorValidator
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Validate all connectors and update their status'

    def add_arguments(self, parser):
        parser.add_argument(
            '--connector-id',
            type=int,
            help='Validate a specific connector by ID',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Validate all connectors (default: only connected/error status)',
        )

    def handle(self, *args, **options):
        connector_id = options.get('connector_id')
        validate_all = options.get('all', False)
        
        if connector_id:
            try:
                connector = Connector.objects.get(id=connector_id)
                self.stdout.write(f'Validating connector {connector.id}: {connector.name}')
                result = ConnectorValidator.validate_and_update_status(connector)
                if result['valid']:
                    self.stdout.write(self.style.SUCCESS(f'✓ Valid: {result["message"]}'))
                    if result.get('details', {}).get('user'):
                        self.stdout.write(f'  User: {result["details"]["user"]}')
                else:
                    self.stdout.write(self.style.ERROR(f'✗ Invalid: {result["message"]}'))
            except Connector.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'Connector {connector_id} not found'))
        else:
            if validate_all:
                connectors = Connector.objects.all()
            else:
                connectors = Connector.objects.filter(status__in=['connected', 'error'])
            
            self.stdout.write(f'Validating {connectors.count()} connector(s)...')
            
            validated = 0
            valid_count = 0
            error_count = 0
            
            for connector in connectors:
                try:
                    result = ConnectorValidator.validate_and_update_status(connector)
                    validated += 1
                    
                    if result['valid']:
                        valid_count += 1
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'✓ {connector.name} ({connector.get_connector_type_display()}): {result["status"]}'
                            )
                        )
                    else:
                        error_count += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f'✗ {connector.name} ({connector.get_connector_type_display()}): {result["message"]}'
                            )
                        )
                except Exception as e:
                    error_count += 1
                    self.stdout.write(
                        self.style.ERROR(f'✗ {connector.name}: Error - {str(e)}')
                    )
            
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(f'Validation complete: {validated} connector(s) validated'))
            self.stdout.write(f'  Valid: {valid_count}')
            self.stdout.write(f'  Errors: {error_count}')

