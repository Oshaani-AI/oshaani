"""Management command to clear plaintext API keys from database (security fix)."""
from django.core.management.base import BaseCommand
from agents_app.models import UserAPIKey, UserProfile, Agent


class Command(BaseCommand):
    help = 'Clear plaintext API keys from database (only keep hashes for security)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be cleared without actually clearing it',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))
        
        # Clear UserAPIKey plaintext keys
        user_keys = UserAPIKey.objects.exclude(api_key__isnull=True).exclude(api_key='')
        count_user = user_keys.count()
        if count_user > 0:
            self.stdout.write(f'Found {count_user} UserAPIKey records with plaintext keys')
            if not dry_run:
                user_keys.update(api_key=None)
                self.stdout.write(self.style.SUCCESS(f'✓ Cleared {count_user} UserAPIKey plaintext keys'))
            else:
                self.stdout.write(f'  Would clear {count_user} UserAPIKey plaintext keys')
        else:
            self.stdout.write('✓ No UserAPIKey records with plaintext keys found')
        
        # Clear UserProfile plaintext keys
        profiles = UserProfile.objects.exclude(api_key__isnull=True).exclude(api_key='')
        count_profile = profiles.count()
        if count_profile > 0:
            self.stdout.write(f'Found {count_profile} UserProfile records with plaintext keys')
            if not dry_run:
                profiles.update(api_key=None)
                self.stdout.write(self.style.SUCCESS(f'✓ Cleared {count_profile} UserProfile plaintext keys'))
            else:
                self.stdout.write(f'  Would clear {count_profile} UserProfile plaintext keys')
        else:
            self.stdout.write('✓ No UserProfile records with plaintext keys found')
        
        # Clear Agent plaintext keys
        agents = Agent.objects.exclude(api_key__isnull=True).exclude(api_key='')
        count_agent = agents.count()
        if count_agent > 0:
            self.stdout.write(f'Found {count_agent} Agent records with plaintext keys')
            if not dry_run:
                agents.update(api_key=None)
                self.stdout.write(self.style.SUCCESS(f'✓ Cleared {count_agent} Agent plaintext keys'))
            else:
                self.stdout.write(f'  Would clear {count_agent} Agent plaintext keys')
        else:
            self.stdout.write('✓ No Agent records with plaintext keys found')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nRun without --dry-run to actually clear the keys'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✓ All plaintext keys have been cleared from the database'))
            self.stdout.write('  Note: Only hashes are stored now. Users will need to generate new keys if they lost theirs.')








