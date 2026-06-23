"""Diagnose agent API key issues."""
from django.core.management.base import BaseCommand
from agents_app.models import Agent
from agents_app.utils import hash_api_key, verify_api_key
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Diagnose agent API key issues - checks agent status, API key hash, and provides troubleshooting steps.'

    def add_arguments(self, parser):
        parser.add_argument('api_key', type=str, nargs='?', help='The API key to verify (optional).')
        parser.add_argument('--agent-id', type=int, help='Check specific agent by ID.')
        parser.add_argument('--all-published', action='store_true', help='Show all published agents with API keys.')

    def handle(self, *args, **options):
        api_key = options.get('api_key')
        agent_id = options.get('agent_id')
        show_all = options.get('all_published', False)
        
        if show_all:
            self.show_all_published_agents()
            return
        
        if agent_id:
            self.check_agent_by_id(agent_id, api_key)
            return
        
        if api_key:
            self.verify_api_key(api_key)
        else:
            self.stdout.write(self.style.ERROR("Please provide an API key, --agent-id, or --all-published flag."))
            self.stdout.write("\nUsage:")
            self.stdout.write("  python manage.py diagnose_agent_api_key <api_key>")
            self.stdout.write("  python manage.py diagnose_agent_api_key --agent-id <id>")
            self.stdout.write("  python manage.py diagnose_agent_api_key --agent-id <id> <api_key>")
            self.stdout.write("  python manage.py diagnose_agent_api_key --all-published")
    
    def show_all_published_agents(self):
        """Show all published agents with API keys."""
        agents = Agent.objects.filter(
            api_key_hash__isnull=False,
            status='published'
        ).exclude(api_key_hash='').select_related('user', 'model').order_by('-published_at', '-created_at')
        
        self.stdout.write(f"\n=== Published Agents with API Keys ({agents.count()}) ===\n")
        
        for agent in agents:
            self.stdout.write(f"Agent ID: {agent.id}")
            self.stdout.write(f"  Name: {agent.name}")
            self.stdout.write(f"  Owner: {agent.user.email}")
            self.stdout.write(f"  Status: {agent.status}")
            self.stdout.write(f"  API Key Hash: {agent.api_key_hash}")
            self.stdout.write(f"  API Key Preview: {agent.api_key_preview}")
            self.stdout.write(f"  Published At: {agent.published_at}")
            self.stdout.write(f"  Model: {agent.model.model_name if agent.model else 'None'}")
            self.stdout.write("")
    
    def check_agent_by_id(self, agent_id, api_key=None):
        """Check specific agent by ID."""
        try:
            agent = Agent.objects.get(id=agent_id)
        except Agent.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Agent with ID {agent_id} not found."))
            return
        
        self.stdout.write(f"\n=== Agent {agent_id} Details ===\n")
        self.stdout.write(f"Name: {agent.name}")
        self.stdout.write(f"Owner: {agent.user.email}")
        self.stdout.write(f"Status: {agent.status}")
        self.stdout.write(f"Published At: {agent.published_at}")
        self.stdout.write(f"Model: {agent.model.model_name if agent.model else 'None'}")
        self.stdout.write(f"Has API Key Hash: {bool(agent.api_key_hash)}")
        
        if agent.api_key_hash:
            self.stdout.write(f"API Key Hash: {agent.api_key_hash}")
            self.stdout.write(f"API Key Preview: {agent.api_key_preview}")
        else:
            self.stdout.write(self.style.WARNING("  ⚠️  No API key hash found!"))
        
        # Check if agent is published
        if agent.status != 'published':
            self.stdout.write(self.style.WARNING(f"\n  ⚠️  Agent status is '{agent.status}', not 'published'."))
            self.stdout.write("  Only published agents can be accessed via API key.")
            self.stdout.write("  Solution: Publish the agent to enable API key access.")
        
        # Check for unpublished changes
        if agent.status == 'published':
            has_changes = agent.has_unpublished_changes()
            if has_changes:
                self.stdout.write(self.style.WARNING("\n  ⚠️  Agent has unpublished changes."))
                self.stdout.write("  Note: This doesn't affect API key validity, but you may want to republish.")
            else:
                self.stdout.write(self.style.SUCCESS("\n  ✓  No unpublished changes detected."))
        
        # Verify API key if provided
        if api_key:
            self.stdout.write(f"\n=== Verifying API Key ===\n")
            if agent.api_key_hash:
                if verify_api_key(api_key, agent.api_key_hash):
                    self.stdout.write(self.style.SUCCESS("  ✓  API key is VALID for this agent!"))
                else:
                    self.stdout.write(self.style.ERROR("  ✗  API key does NOT match this agent."))
                    self.stdout.write(f"  Expected hash: {agent.api_key_hash}")
                    self.stdout.write(f"  Provided key hash: {hash_api_key(api_key)}")
            else:
                self.stdout.write(self.style.ERROR("  ✗  Agent has no API key hash to verify against."))
    
    def verify_api_key(self, api_key):
        """Verify API key against all published agents."""
        if not api_key:
            self.stdout.write(self.style.ERROR("API key cannot be empty."))
            return

        hashed_input_key = hash_api_key(api_key)
        self.stdout.write(f"\n=== API Key Verification ===\n")
        self.stdout.write(f"API Key Hash: {hashed_input_key}\n")

        self.stdout.write("Checking all published agents...\n")

        matching_agent = None
        published_agents_with_keys = Agent.objects.filter(
            api_key_hash__isnull=False,
            status='published'
        ).exclude(api_key_hash='').select_related('user', 'model')
        
        self.stdout.write(f"Found {published_agents_with_keys.count()} published agents with API keys\n")

        for agent in published_agents_with_keys:
            if verify_api_key(api_key, agent.api_key_hash):
                matching_agent = agent
                break
        
        if matching_agent:
            self.stdout.write(self.style.SUCCESS(f"✓ Matching agent found: {matching_agent.name} (ID: {matching_agent.id})"))
            self.stdout.write(f"  Owner: {matching_agent.user.email}")
            self.stdout.write(f"  Status: {matching_agent.status}")
            self.stdout.write(f"  Published At: {matching_agent.published_at}")
            self.stdout.write(f"  API Key Hash: {matching_agent.api_key_hash}")
            self.stdout.write(f"  Model: {matching_agent.model.model_name if matching_agent.model else 'None'}")
            
            # Check for unpublished changes
            has_changes = matching_agent.has_unpublished_changes()
            if has_changes:
                self.stdout.write(self.style.WARNING("\n  ⚠️  Agent has unpublished changes."))
                self.stdout.write("  Note: This doesn't affect API key validity.")
        else:
            self.stdout.write(self.style.ERROR("✗ No matching agent found for this API key"))
            self.stdout.write("\nPossible reasons:")
            self.stdout.write("  1. API key was regenerated")
            self.stdout.write("  2. Agent is not published (status != 'published')")
            self.stdout.write("  3. API key is incorrect")
            self.stdout.write("  4. Agent was deleted")
            self.stdout.write("\nTroubleshooting steps:")
            self.stdout.write("  1. Check agent status: python manage.py diagnose_agent_api_key --all-published")
            self.stdout.write("  2. If agent exists but key doesn't match, regenerate: python manage.py generate_agent_api_key <agent_id> --force")
            self.stdout.write("  3. Verify agent is published: python manage.py diagnose_agent_api_key --agent-id <agent_id>")











