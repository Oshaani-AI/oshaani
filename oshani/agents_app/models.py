"""Models for agents_app."""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from .utils import generate_api_key, hash_api_key


class AIModel(models.Model):
    """AI Model information from Bedrock and Ollama."""
    
    PROVIDER_CHOICES = [
        ('bedrock', 'Amazon Bedrock'),
        ('ollama', 'Ollama'),
    ]
    
    model_id = models.CharField(max_length=255, unique=True, db_index=True)
    model_name = models.CharField(max_length=255)
    provider = models.CharField(max_length=50, choices=PROVIDER_CHOICES)
    description = models.TextField(blank=True)
    input_modalities = models.JSONField(default=list, blank=True)
    output_modalities = models.JSONField(default=list, blank=True)
    use_cases = models.JSONField(default=list, blank=True, help_text="Best use cases for this model")
    is_available = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    last_checked = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['provider', 'model_name']
        unique_together = [['model_id', 'provider']]
    
    def __str__(self):
        return f"{self.model_name} ({self.get_provider_display()})"


class Agent(models.Model):
    """AI Agent model."""
    
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('training', 'Training'),
        ('testing', 'Testing'),
        ('published', 'Published'),
    ]
    
    AGENT_TYPE_CHOICES = [
        ('chat_agent', 'Chat Agent'),
        ('quick_bot', 'Chat Bot'),
        ('quick_flows', 'Quick Flows'),
        ('quick_automate', 'Quick Automate'),
        ('quick_research', 'Quick Research'),
        ('quick_index', 'Quick Index'),
    ]
    
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True, db_index=True,
                           help_text="URL-friendly identifier (auto-generated from name)")
    description = models.TextField(blank=True)
    agent_type = models.CharField(max_length=50, choices=AGENT_TYPE_CHOICES, default='chat_agent')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    configuration = models.JSONField(default=dict, blank=True)
    api_key = models.CharField(max_length=255, unique=True, null=True, blank=True, db_index=True)
    api_key_hash = models.CharField(max_length=64, null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='agents')
    model = models.ForeignKey(AIModel, on_delete=models.SET_NULL, null=True, blank=True, related_name='agents')
    inference_profile = models.ForeignKey('InferenceProfile', on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name='agents',
                                         help_text="Inference profile for models that require provisioning")
    quick_suite_space_id = models.CharField(max_length=255, null=True, blank=True)
    quick_suite_agent_id = models.CharField(max_length=255, null=True, blank=True)
    training_data_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)
    # Track published state for change detection
    published_configuration = models.JSONField(default=dict, blank=True, null=True,
                                             help_text="Snapshot of configuration when last published")
    published_training_data_count = models.IntegerField(default=0, null=True,
                                                       help_text="Training data count when last published")
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.name} ({self.status})"
    
    def save(self, *args, **kwargs):
        """Override save to auto-generate slug from name."""
        if not self.slug:
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)
    
    def _generate_unique_slug(self):
        """Generate a unique slug from the agent name."""
        from django.utils.text import slugify
        base_slug = slugify(self.name)
        if not base_slug:
            base_slug = 'agent'
        slug = base_slug
        counter = 1
        # Check for existing slugs and add suffix if needed
        while Agent.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        return slug
    
    def get_absolute_url(self):
        """Return the canonical URL for this agent."""
        from django.urls import reverse
        return reverse('agent_detail', kwargs={'slug': self.slug})
    
    def generate_api_key(self):
        """Rotate agent API access: revoke all existing keys and create one new key.

        Plaintext is returned once; hashes are stored on AgentAPIKey and synced to
        api_key_hash for backward compatibility.
        """
        from django.apps import apps
        from django.db import transaction

        AgentAPIKey = apps.get_model('agents_app', 'AgentAPIKey')
        key = generate_api_key()
        key_hash = hash_api_key(key)
        with transaction.atomic():
            self.agent_api_keys.filter(is_active=True).update(is_active=False)
            self.api_key = None
            self.api_key_hash = None
            self.save(update_fields=['api_key', 'api_key_hash'])
            AgentAPIKey.objects.create(
                agent=self,
                name='Primary',
                api_key_hash=key_hash,
                is_active=True,
            )
            self.api_key_hash = key_hash
            self.save(update_fields=['api_key_hash'])
        return key

    def add_api_key(self, name=None):
        """Create an additional API key without revoking existing keys."""
        from django.apps import apps

        AgentAPIKey = apps.get_model('agents_app', 'AgentAPIKey')
        row = AgentAPIKey.objects.create(
            agent=self,
            name=(name or '').strip() or None,
            is_active=True,
        )
        return row.generate_api_key()

    def verify_api_key(self, provided_key):
        """Verify a provided API key against any active AgentAPIKey or legacy agent hash."""
        from .utils import verify_api_key as verify_key

        for row in self.agent_api_keys.filter(is_active=True).exclude(api_key_hash__isnull=True).exclude(api_key_hash=''):
            if verify_key(provided_key, row.api_key_hash):
                return True
        if self.api_key_hash:
            return verify_key(provided_key, self.api_key_hash)
        return False

    @property
    def api_key_preview(self):
        """Short preview of the newest active key (or legacy hash)."""
        row = self.agent_api_keys.filter(is_active=True).exclude(api_key_hash__isnull=True).exclude(api_key_hash='').order_by('-created_at').first()
        if row:
            return row.preview
        if self.api_key_hash:
            return f"{self.api_key_hash[:8]}...{self.api_key_hash[-4:]}"
        return None
    
    def has_unpublished_changes(self):
        """Check if there are unpublished changes to configuration or training data."""
        import json
        
        # If never published, no unpublished changes to compare
        if not self.published_at:
            return False
        
        # Check if training data count changed
        if self.published_training_data_count is not None:
            if self.training_data_count != self.published_training_data_count:
                return True
        
        # Check if configuration changed
        if self.published_configuration:
            # Deep comparison of configuration
            current_config = json.dumps(self.configuration or {}, sort_keys=True)
            published_config = json.dumps(self.published_configuration or {}, sort_keys=True)
            if current_config != published_config:
                return True
        
        return False
    
    def get_change_summary(self):
        """Get a summary of what changed since last publication."""
        changes = []
        import json
        
        if not self.published_at:
            return changes
        
        # Check training data changes
        if self.published_training_data_count is not None:
            if self.training_data_count != self.published_training_data_count:
                diff = self.training_data_count - self.published_training_data_count
                if diff > 0:
                    changes.append(f"Training data: Added {diff} item(s)")
                else:
                    changes.append(f"Training data: Removed {abs(diff)} item(s)")
        
        # Check configuration changes
        if self.published_configuration:
            current_config = self.configuration or {}
            published_config = self.published_configuration or {}
            
            # Check for key differences
            current_keys = set(current_config.keys())
            published_keys = set(published_config.keys())
            
            added_keys = current_keys - published_keys
            removed_keys = published_keys - current_keys
            changed_keys = []
            
            for key in current_keys & published_keys:
                if json.dumps(current_config.get(key), sort_keys=True) != json.dumps(published_config.get(key), sort_keys=True):
                    changed_keys.append(key)
            
            if added_keys:
                changes.append(f"Configuration: Added keys: {', '.join(added_keys)}")
            if removed_keys:
                changes.append(f"Configuration: Removed keys: {', '.join(removed_keys)}")
            if changed_keys:
                changes.append(f"Configuration: Modified keys: {', '.join(changed_keys)}")
        
        return changes
    
    def sync_training_data_count(self):
        """Sync training_data_count with actual count from database."""
        actual_count = self.training_data.count()
        if self.training_data_count != actual_count:
            self.training_data_count = actual_count
            self.save(update_fields=['training_data_count'])
            return True  # Indicates count was updated
        return False  # Count was already in sync
    
    def publish(self):
        """Publish the agent and save current state as published snapshot."""
        if self.status != 'testing':
            raise ValueError("Agent must be in testing status to publish")
        self.status = 'published'
        self.published_at = timezone.now()
        
        # Sync training data count before publishing to ensure accuracy
        self.sync_training_data_count()
        
        # Save current configuration and training data count as published snapshot
        import json
        self.published_configuration = json.loads(json.dumps(self.configuration or {}))
        self.published_training_data_count = self.training_data_count
        
        # Only generate API key if none exist (legacy field or AgentAPIKey rows)
        if not self.api_key_hash and not self.agent_api_keys.filter(is_active=True).exists():
            self.generate_api_key()
        self.save()


class TrainingData(models.Model):
    """Training data for agents."""
    
    DATA_TYPE_CHOICES = [
        ('text', 'Text'),
        ('knowledge_base', 'Knowledge Base'),
        ('structured', 'Structured Data'),
        ('file', 'File Upload'),
    ]
    
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='training_data')
    data_type = models.CharField(max_length=50, choices=DATA_TYPE_CHOICES, default='text')
    content = models.JSONField(default=dict, blank=True)
    file_path = models.FileField(upload_to='training_data/', null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f"{self.agent.name} - {self.data_type} ({self.uploaded_at})"
    
    def is_indexed(self) -> bool:
        """Check if this training data has been indexed for RAG."""
        # This will be checked against RAGIndexStatus
        return RAGIndexStatus.objects.filter(
            agent=self.agent,
            training_data=self,
            is_active=True
        ).exists()


class TestResult(models.Model):
    """Test results for agents."""
    
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='test_results')
    test_query = models.TextField()
    expected_response = models.TextField(blank=True)
    actual_response = models.TextField(blank=True)
    score = models.FloatField(null=True, blank=True)
    passed = models.BooleanField(default=False)
    tested_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-tested_at']
    
    def __str__(self):
        return f"{self.agent.name} - Test {self.id} ({'Passed' if self.passed else 'Failed'})"


class AgentFeedback(models.Model):
    """User feedback for agent responses to help optimize answers."""
    
    FEEDBACK_CHOICES = [
        ('positive', 'Positive (Helpful)'),
        ('negative', 'Negative (Not Helpful)'),
        ('neutral', 'Neutral'),
    ]
    
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='feedbacks')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='agent_feedbacks')
    query = models.TextField(help_text="The user's query")
    response = models.TextField(help_text="The agent's response")
    feedback_type = models.CharField(max_length=20, choices=FEEDBACK_CHOICES)
    feedback_text = models.TextField(blank=True, help_text="Optional detailed feedback")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['agent', 'feedback_type']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"Feedback {self.id} for {self.agent.name} - {self.get_feedback_type_display()}"


class CustomTool(models.Model):
    """Custom tool configuration for agents."""
    
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='custom_tools')
    display_name = models.CharField(max_length=200, help_text="Name visible to users")
    function_name = models.CharField(max_length=100, help_text="Function name visible to agent (lowercase, underscores)")
    description = models.TextField(help_text="What the tool does")
    instructions = models.TextField(blank=True, help_text="Additional instructions for the agent")
    url = models.URLField(help_text="Tool endpoint URL")
    method = models.CharField(max_length=10, choices=[('GET', 'GET'), ('POST', 'POST'), ('PUT', 'PUT'), ('DELETE', 'DELETE')], default='POST')
    headers = models.JSONField(default=dict, blank=True, help_text="HTTP headers (e.g., for authentication)")
    parameters = models.JSONField(default=list, blank=True, help_text="Tool parameters configuration")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['display_name']
        unique_together = [['agent', 'function_name']]
    
    def __str__(self):
        return f"{self.display_name} ({self.agent.name})"


class AgentTool(models.Model):
    """Tool enable/disable state for agents (stored in database instead of JSON)."""
    
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='agent_tools')
    tool_name = models.CharField(max_length=100, help_text="Name of the tool")
    is_enabled = models.BooleanField(default=True, help_text="Whether the tool is enabled for this agent")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['tool_name']
        unique_together = [['agent', 'tool_name']]
        indexes = [
            models.Index(fields=['agent', 'is_enabled']),
        ]
    
    def __str__(self):
        status = "enabled" if self.is_enabled else "disabled"
        return f"{self.tool_name} ({status}) for {self.agent.name}"


class Conversation(models.Model):
    """Conversation model for managing multi-turn conversations."""
    
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='conversations')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='conversations')
    conversation_id = models.CharField(max_length=255, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=[('active', 'Active'), ('completed', 'Completed'), ('archived', 'Archived')], default='active')
    session_state = models.JSONField(
        default=dict,
        blank=True,
        help_text='Structured session data (e.g. exam progress: question number, answers)',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-updated_at']
    
    def __str__(self):
        return f"Conversation {self.conversation_id} for {self.agent.name}"


class ConversationMessage(models.Model):
    """Messages in a conversation."""
    
    MESSAGE_TYPES = [
        ('user', 'User Message'),
        ('agent', 'Agent Message'),
        ('tool_call', 'Tool Call'),
        ('tool_result', 'Tool Result'),
    ]
    
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    message_type = models.CharField(max_length=20, choices=MESSAGE_TYPES)
    content = models.TextField()
    tool_name = models.CharField(max_length=100, blank=True, help_text="Tool name if message_type is tool_call or tool_result")
    tool_parameters = models.JSONField(default=dict, blank=True)
    tool_result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['created_at']
    
    def __str__(self):
        return f"{self.message_type} message in conversation {self.conversation.conversation_id}"


class ToolCall(models.Model):
    """Tool call execution tracking."""
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('executing', 'Executing'),
        ('done', 'Done'),
        ('error', 'Error'),
    ]
    
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='tool_calls', null=True, blank=True)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='tool_calls')
    tool_name = models.CharField(max_length=100)
    parameters = models.JSONField(default=dict)
    result_content = models.TextField(blank=True)
    result_files = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)
    state = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Tool call: {self.tool_name} ({self.state})"


class AgentRequest(models.Model):
    """Model to track async agent API requests and their status."""
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    request_id = models.CharField(max_length=255, unique=True, db_index=True, help_text="Unique request ID for polling")
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='requests')
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='requests', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    message = models.TextField(help_text="User message that triggered this request")
    response = models.TextField(blank=True, help_text="Agent response (when completed)")
    tool_calls = models.JSONField(default=list, blank=True, help_text="Tool calls made during processing")
    iterations = models.IntegerField(default=0, help_text="Number of iterations")
    error_message = models.TextField(blank=True, help_text="Error message if status is failed")
    celery_task_id = models.CharField(max_length=255, blank=True, db_index=True, help_text="Celery task ID for tracking")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['request_id']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['celery_task_id']),
        ]
    
    def __str__(self):
        return f"Request {self.request_id} ({self.status}) for {self.agent.name}"


class ConversationFile(models.Model):
    """Files uploaded for conversations."""
    
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='files', null=True, blank=True)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='conversation_files')
    file_name = models.CharField(max_length=255)
    file_path = models.FileField(upload_to='conversation_files/', null=True, blank=True)
    file_type = models.CharField(max_length=100, blank=True)
    file_size = models.BigIntegerField(default=0)
    file_id = models.CharField(max_length=255, unique=True, db_index=True)
    upload_url = models.URLField(blank=True, help_text="Temporary upload URL")
    download_url = models.URLField(blank=True, help_text="Temporary download URL")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f"File: {self.file_name} ({self.file_id})"


class MCPServer(models.Model):
    """MCP (Model Context Protocol) server configuration for agents."""
    
    TRANSPORT_CHOICES = [
        ('stdio', 'STDIO'),
        ('http', 'HTTP'),
        ('sse', 'SSE'),
    ]
    
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='mcp_servers')
    name = models.CharField(max_length=200, help_text="Name of the MCP server")
    description = models.TextField(blank=True, help_text="Description of what this MCP server provides")
    transport_type = models.CharField(max_length=20, choices=TRANSPORT_CHOICES, default='stdio')
    
    # STDIO transport configuration
    command = models.CharField(max_length=500, blank=True, help_text="Command to run the MCP server (e.g., 'npx', 'uvx')")
    args = models.JSONField(default=list, blank=True, help_text="Arguments for the command (e.g., ['-y', '@modelcontextprotocol/server-filesystem', '.'])")
    
    # HTTP/SSE transport configuration
    url = models.URLField(blank=True, help_text="URL for HTTP/SSE transport")
    headers = models.JSONField(default=dict, blank=True, help_text="HTTP headers for authentication")
    
    # Configuration
    is_active = models.BooleanField(default=True)
    auto_connect = models.BooleanField(default=True, help_text="Automatically connect when agent is used")
    configuration = models.JSONField(default=dict, blank=True, help_text="Additional configuration")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
        unique_together = [['agent', 'name']]
    
    def __str__(self):
        return f"{self.name} ({self.agent.name})"


class UserProfile(models.Model):
    """User profile with API key management."""
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    api_key = models.CharField(max_length=255, unique=True, null=True, blank=True, db_index=True)
    api_key_hash = models.CharField(max_length=64, null=True, blank=True)
    api_key_created_at = models.DateTimeField(null=True, blank=True)
    api_key_last_used = models.DateTimeField(null=True, blank=True)
    onboarding_tour_completed = models.BooleanField(default=False, help_text="Whether user has completed the onboarding tour")
    mobile_number = models.CharField(max_length=20, null=True, blank=True, help_text="User's mobile number")
    profile_picture = models.ImageField(upload_to='profile_pictures/', null=True, blank=True, help_text="User's profile picture")
    terms_accepted = models.BooleanField(default=False, help_text="Whether user has accepted terms & conditions")
    terms_accepted_at = models.DateTimeField(null=True, blank=True, help_text="When user accepted terms")
    privacy_accepted = models.BooleanField(default=False, help_text="Whether user has accepted privacy policy")
    privacy_accepted_at = models.DateTimeField(null=True, blank=True, help_text="When user accepted privacy policy")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Profile for {self.user.username}"
    
    def generate_api_key(self):
        """Generate and store a new API key.
        
        Note: The plaintext key is returned but NOT stored in the database.
        Only the hash is stored for security. The key must be saved by the caller
        immediately as it won't be available again.
        """
        key = generate_api_key()
        # Store hash only - never store plaintext key in database
        self.api_key_hash = hash_api_key(key)
        # Clear any existing plaintext key
        self.api_key = None
        self.api_key_created_at = timezone.now()
        self.save(update_fields=['api_key', 'api_key_hash', 'api_key_created_at'])
        return key
    
    def verify_api_key(self, provided_key):
        """Verify a provided API key."""
        if not self.api_key_hash:
            return False
        from .utils import verify_api_key
        return verify_api_key(provided_key, self.api_key_hash)
    
    def revoke_api_key(self):
        """Revoke the current API key."""
        self.api_key = None
        self.api_key_hash = None
        self.api_key_created_at = None
        self.save(update_fields=['api_key', 'api_key_hash', 'api_key_created_at'])
    
    def update_last_used(self):
        """Update the last used timestamp."""
        self.api_key_last_used = timezone.now()
        self.save(update_fields=['api_key_last_used'])


class UserAPIKey(models.Model):
    """Multiple API keys per user."""
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='api_keys')
    name = models.CharField(max_length=100, null=True, blank=True, help_text="Optional name/description for this key")
    api_key = models.CharField(max_length=255, unique=True, null=True, blank=True, db_index=True)
    api_key_hash = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'User API Key'
        verbose_name_plural = 'User API Keys'
    
    def __str__(self):
        name = self.name or f"Key {self.id}"
        return f"{name} ({self.user.username})"
    
    def generate_api_key(self):
        """Generate and store a new API key."""
        key = generate_api_key()
        self.api_key = key
        self.api_key_hash = hash_api_key(key)
        self.save(update_fields=['api_key', 'api_key_hash'])
        return key
    
    def verify_api_key(self, provided_key):
        """Verify a provided API key."""
        if not self.api_key_hash or not self.is_active:
            return False
        from .utils import verify_api_key
        return verify_api_key(provided_key, self.api_key_hash)
    
    def update_last_used(self):
        """Update the last used timestamp."""
        self.last_used = timezone.now()
        self.save(update_fields=['last_used'])
    
    def revoke(self):
        """Revoke this API key."""
        self.is_active = False
        self.save(update_fields=['is_active'])
    
    @property
    def preview(self):
        """Get a preview of the API key (hash-based since plaintext is not stored)."""
        # Since we don't store plaintext keys, show a hash-based preview
        if self.api_key_hash:
            return f"{self.api_key_hash[:8]}...{self.api_key_hash[-4:]}"
        return "No key"


class AgentAPIKey(models.Model):
    """Multiple API keys per agent (hash-only; plaintext shown once at creation)."""

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='agent_api_keys')
    name = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text='Optional label (e.g. production, CI, partner)',
    )
    api_key_hash = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['api_key_hash', 'is_active']),
            models.Index(fields=['agent', 'is_active']),
        ]
        verbose_name = 'Agent API Key'
        verbose_name_plural = 'Agent API Keys'

    def __str__(self):
        label = self.name or f'Key {self.pk}'
        return f'{label} ({self.agent.name})'

    def generate_api_key(self):
        """Set hash from a new random key; return plaintext once."""
        key = generate_api_key()
        self.api_key_hash = hash_api_key(key)
        self.save(update_fields=['api_key_hash'])
        return key

    def verify_api_key(self, provided_key):
        if not self.api_key_hash or not self.is_active:
            return False
        from .utils import verify_api_key as verify_key
        return verify_key(provided_key, self.api_key_hash)

    def update_last_used(self):
        self.last_used = timezone.now()
        self.save(update_fields=['last_used'])

    def revoke(self):
        self.is_active = False
        self.save(update_fields=['is_active'])

    @property
    def preview(self):
        if self.api_key_hash:
            return f'{self.api_key_hash[:8]}...{self.api_key_hash[-4:]}'
        return None


class Notification(models.Model):
    """User notifications for various events."""
    
    NOTIFICATION_TYPES = [
        ('rag_indexed', 'RAG Indexing Complete'),
        ('rag_failed', 'RAG Indexing Failed'),
        ('training_complete', 'Training Complete'),
        ('agent_published', 'Agent Published'),
        ('test_complete', 'Test Complete'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='notifications', null=True, blank=True)
    notification_type = models.CharField(max_length=50, choices=NOTIFICATION_TYPES)
    title = models.CharField(max_length=200)
    message = models.TextField()
    data = models.JSONField(default=dict, blank=True, help_text="Additional notification data")
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"{self.get_notification_type_display()} - {self.user.username} ({'read' if self.is_read else 'unread'})"


class RAGIndexStatus(models.Model):
    """Track RAG indexing status for agents."""
    
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='rag_index_status')
    training_data = models.ForeignKey(TrainingData, on_delete=models.CASCADE, null=True, blank=True, 
                                      related_name='rag_index_status', 
                                      help_text="Specific training data item indexed (null means all)")
    chunks_count = models.IntegerField(default=0, help_text="Number of chunks indexed")
    indexed_at = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)
    embedding_provider = models.CharField(max_length=50, default='bedrock', 
                                         help_text="Provider used for embeddings")
    is_active = models.BooleanField(default=True, help_text="Whether this index is active")
    
    class Meta:
        ordering = ['-indexed_at']
        unique_together = [['agent', 'training_data']]
    
    def __str__(self):
        if self.training_data:
            return f"RAG Index for {self.agent.name} - {self.training_data.data_type} ({self.chunks_count} chunks)"
        return f"RAG Index for {self.agent.name} ({self.chunks_count} chunks)"


class AgentShare(models.Model):
    """Model for sharing agents with other users via email."""
    
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='shares')
    shared_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shared_agents')
    email = models.EmailField(help_text="Email address of the user to share with")
    token = models.CharField(max_length=64, unique=True, db_index=True, 
                           help_text="Unique token for accessing the shared agent")
    message = models.TextField(blank=True, help_text="Optional message from the sharer")
    expires_at = models.DateTimeField(null=True, blank=True, 
                                     help_text="Expiration date for the share (optional)")
    is_accepted = models.BooleanField(default=False, help_text="Whether the share has been accepted")
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='accepted_agent_shares',
                                    help_text="User who accepted the share")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = [['agent', 'email']]
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['email', 'is_accepted']),
        ]
    
    def __str__(self):
        return f"{self.agent.name} shared with {self.email} by {self.shared_by.username}"
    
    def is_expired(self):
        """Check if the share has expired."""
        if self.expires_at:
            return timezone.now() > self.expires_at
        return False
    
    def is_valid(self):
        """Check if the share is still valid (not expired and not accepted)."""
        return not self.is_expired() and not self.is_accepted


class AgentPublicShare(models.Model):
    """Model for public share URLs that can be shared with anyone."""
    
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='public_shares')
    shared_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='public_shared_agents')
    token = models.CharField(max_length=64, unique=True, db_index=True,
                           help_text="Unique token for accessing the shared agent")
    is_active = models.BooleanField(default=True, help_text="Whether the public share is active")
    expires_at = models.DateTimeField(null=True, blank=True,
                                     help_text="Expiration date for the share (optional)")
    access_count = models.IntegerField(default=0, help_text="Number of times the share URL was accessed")
    last_accessed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['agent', 'is_active']),
        ]
    
    def __str__(self):
        return f"Public share for {self.agent.name} by {self.shared_by.username}"
    
    def is_expired(self):
        """Check if the share has expired."""
        if self.expires_at:
            return timezone.now() > self.expires_at
        return False
    
    def is_valid(self):
        """Check if the share is still valid (active and not expired)."""
        return self.is_active and not self.is_expired()
    
    def increment_access(self):
        """Increment access count and update last accessed timestamp."""
        self.access_count += 1
        self.last_accessed_at = timezone.now()
        self.save(update_fields=['access_count', 'last_accessed_at'])


class SharedAgentUsage(models.Model):
    """Track usage of shared agents by users."""
    
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='shared_usage')
    shared_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shared_agent_usage',
                                  help_text="Owner of the agent")
    used_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='used_shared_agents',
                                help_text="User who is using the shared agent")
    share = models.ForeignKey(AgentShare, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='usage_records',
                             help_text="The share record that granted access")
    
    # Usage statistics
    message_count = models.IntegerField(default=0, help_text="Number of messages sent")
    conversation_count = models.IntegerField(default=0, help_text="Number of conversations")
    last_used_at = models.DateTimeField(null=True, blank=True, help_text="Last time the agent was used")
    first_used_at = models.DateTimeField(auto_now_add=True, help_text="First time the agent was used")
    
    # Period tracking (for monthly/daily stats)
    period_start = models.DateTimeField(help_text="Start of the usage period")
    period_end = models.DateTimeField(help_text="End of the usage period")
    is_daily = models.BooleanField(default=False, help_text="True for daily metrics, False for monthly")
    
    class Meta:
        ordering = ['-last_used_at', '-first_used_at']
        unique_together = [['agent', 'used_by', 'period_start', 'is_daily']]
        indexes = [
            models.Index(fields=['agent', 'used_by', 'period_start']),
            models.Index(fields=['shared_by', 'period_start']),
            models.Index(fields=['agent', 'last_used_at']),
        ]
        verbose_name = 'Shared Agent Usage'
        verbose_name_plural = 'Shared Agent Usage Records'
    
    def __str__(self):
        period_type = "daily" if self.is_daily else "monthly"
        return f"{self.agent.name} used by {self.used_by.username} ({period_type}): {self.message_count} messages"


class AgentLike(models.Model):
    """Simple like/dislike feedback for public agents."""
    
    FEEDBACK_CHOICES = [
        ('like', 'Like'),
        ('dislike', 'Dislike'),
    ]
    
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='likes')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='agent_likes')
    feedback_type = models.CharField(max_length=10, choices=FEEDBACK_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = [['agent', 'user']]
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['agent', 'feedback_type']),
            models.Index(fields=['agent', 'user']),
        ]
    
    def __str__(self):
        return f"{self.user.username} {self.feedback_type} {self.agent.name}"


class SystemSettings(models.Model):
    """System-wide settings configurable from admin panel."""
    
    key = models.CharField(max_length=100, unique=True, db_index=True,
                          help_text="Setting key (unique identifier)")
    value = models.TextField(help_text="Setting value (can be JSON, number, or text)")
    description = models.TextField(blank=True, help_text="Description of what this setting does")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='updated_settings',
                                  help_text="User who last updated this setting")
    
    class Meta:
        verbose_name = "System Setting"
        verbose_name_plural = "System Settings"
        ordering = ['key']
    
    def __str__(self):
        return f"{self.key} = {self.value}"
    
    @classmethod
    def get_setting(cls, key, default=None):
        """Get a setting value by key."""
        try:
            setting = cls.objects.get(key=key)
            # Try to parse as JSON, fallback to string
            try:
                import json
                return json.loads(setting.value)
            except (json.JSONDecodeError, ValueError):
                # Try to parse as number
                try:
                    if '.' in setting.value:
                        return float(setting.value)
                    return int(setting.value)
                except ValueError:
                    return setting.value
        except cls.DoesNotExist:
            return default
    
    @classmethod
    def set_setting(cls, key, value, description='', user=None):
        """Set a setting value."""
        import json
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        else:
            value = str(value)
        
        setting, created = cls.objects.update_or_create(
            key=key,
            defaults={
                'value': value,
                'description': description,
                'updated_by': user
            }
        )
        return setting


class SocialLink(models.Model):
    """Footer social media link, editable in Django admin."""
    PLATFORM_CHOICES = [
        ('twitter', 'Twitter / X'),
        ('linkedin', 'LinkedIn'),
        ('facebook', 'Facebook'),
        ('youtube', 'YouTube'),
        ('instagram', 'Instagram'),
        ('github', 'GitHub'),
        ('other', 'Other'),
    ]
    name = models.CharField(max_length=100, help_text="Display name (e.g. Twitter, LinkedIn)")
    url = models.URLField(max_length=500, help_text="Full URL to your profile or page")
    platform = models.CharField(
        max_length=20,
        choices=PLATFORM_CHOICES,
        default='other',
        help_text="Platform for icon (Bootstrap Icons: bi-twitter, bi-linkedin, etc.)"
    )
    order = models.PositiveIntegerField(default=0, help_text="Display order (lower first)")
    is_active = models.BooleanField(default=True, help_text="Show this link in the footer")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = "Social Link"
        verbose_name_plural = "Social Links"

    def __str__(self):
        return f"{self.name} ({self.url})"

    def icon_class(self):
        """Bootstrap Icons class for this platform."""
        icon_map = {
            'twitter': 'bi-twitter-x',
            'linkedin': 'bi-linkedin',
            'facebook': 'bi-facebook',
            'youtube': 'bi-youtube',
            'instagram': 'bi-instagram',
            'github': 'bi-github',
            'other': 'bi-link-45deg',
        }
        return icon_map.get(self.platform, 'bi-link-45deg')


class InferenceProfile(models.Model):
    """Inference profile for Bedrock models that require provisioning."""
    
    STATUS_CHOICES = [
        ('creating', 'Creating'),
        ('active', 'Active'),
        ('updating', 'Updating'),
        ('deleting', 'Deleting'),
        ('failed', 'Failed'),
    ]
    
    model = models.ForeignKey(AIModel, on_delete=models.CASCADE, related_name='inference_profiles',
                              help_text="Model that requires this inference profile")
    profile_name = models.CharField(max_length=255, unique=True, db_index=True,
                                   help_text="Unique name for the inference profile")
    profile_arn = models.CharField(max_length=500, blank=True,
                                   help_text="AWS ARN of the inference profile")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='creating')
    regions = models.JSONField(default=list,
                               help_text="List of AWS regions where inference requests can be routed")
    configuration = models.JSONField(default=dict, blank=True,
                                    help_text="Additional inference profile configuration")
    metadata = models.JSONField(default=dict, blank=True,
                                help_text="Metadata about the inference profile")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='inference_profiles',
                                  help_text="User who created this inference profile")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_used = models.DateTimeField(null=True, blank=True,
                                     help_text="Last time this inference profile was used")
    
    class Meta:
        ordering = ['-created_at']
        unique_together = [['model', 'profile_name']]
    
    def __str__(self):
        return f"{self.profile_name} ({self.model.model_name})"
    
    def is_available(self):
        """Check if inference profile is available for use."""
        return self.status == 'active' and self.profile_arn
