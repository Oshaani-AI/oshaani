"""Admin configuration for agents_app."""
from django.contrib import admin
from django.utils.html import format_html
from django.urls import path
from django.shortcuts import render
from django.db.models import Q
from .models import (
    Agent,
    AgentAPIKey,
    TrainingData,
    TestResult,
    AIModel,
    UserProfile,
    UserAPIKey,
    InferenceProfile,
    AgentRequest,
    SystemSettings,
    SocialLink,
    Conversation,
    ConversationMessage,
)


class AgentAPIKeyInline(admin.TabularInline):
    """Show per-agent API keys (hashes only) in the agent change form."""

    model = AgentAPIKey
    extra = 0
    can_delete = False
    readonly_fields = ('api_key_hash', 'preview_display', 'created_at', 'last_used')
    fields = ('name', 'api_key_hash', 'preview_display', 'created_at', 'last_used', 'is_active')

    @admin.display(description='Preview')
    def preview_display(self, obj):
        if not obj.pk:
            return '—'
        return obj.preview or '—'


@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
    """Admin interface for AI Model."""
    list_display = ['model_name', 'model_id', 'provider', 'is_available', 'last_checked']
    list_filter = ['provider', 'is_available', 'last_checked']
    search_fields = ['model_name', 'model_id', 'description']
    readonly_fields = ['last_checked', 'created_at']


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    """Admin interface for Agent model."""
    inlines = [AgentAPIKeyInline]
    list_display = ['name', 'user', 'model', 'agent_type', 'status', 'has_api_key', 'api_key_preview_short', 'api_key_hash_display', 'training_data_count', 'created_at']
    list_filter = ['status', 'agent_type', 'created_at', 'model__provider']
    search_fields = ['name', 'description', 'user__username', 'user__email', 'api_key_hash']
    readonly_fields = ['api_key_hash', 'api_key_preview_display', 'created_at', 'updated_at', 'published_at']
    actions = ['regenerate_api_key_action']
    list_per_page = 50
    
    def changelist_view(self, request, extra_context=None):
        """Override changelist to add link to API keys table."""
        extra_context = extra_context or {}
        extra_context['show_api_keys_link'] = True
        return super().changelist_view(request, extra_context)
    
    def get_urls(self):
        """Add custom URL for API key table view."""
        urls = super().get_urls()
        from django.urls import path
        custom_urls = [
            path('api-keys-table/', self.admin_site.admin_view(self.api_keys_table_view), name='agents_app_agent_apikeys_table'),
        ]
        return custom_urls + urls
    
    def api_keys_table_view(self, request):
        """Custom view to display all agent API keys in a dedicated table."""
        # Get all agents with API keys
        agents_with_keys = Agent.objects.filter(
            api_key_hash__isnull=False
        ).exclude(api_key_hash='').select_related('user', 'model').order_by('-published_at', '-created_at')
        
        # Get all agents without API keys
        agents_without_keys = Agent.objects.filter(
            Q(api_key_hash__isnull=True) | Q(api_key_hash='')
        ).select_related('user', 'model').order_by('-created_at')
        
        context = {
            **self.admin_site.each_context(request),
            'title': 'Agent API Keys Table',
            'agents_with_keys': agents_with_keys,
            'agents_without_keys': agents_without_keys[:20],  # Limit to 20 for display
            'opts': self.model._meta,
            'has_view_permission': self.has_view_permission(request),
        }
        
        return render(request, 'admin/agents_app/agent/apikeys_table.html', context)
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'description', 'agent_type', 'user')
        }),
        ('Status', {
            'fields': ('status', 'configuration')
        }),
        ('Quick Suite Integration', {
            'fields': ('quick_suite_space_id', 'quick_suite_agent_id')
        }),
        ('API Key Management', {
            'fields': ('api_key_preview_display', 'api_key_hash'),
            'description': 'API keys are hashed for security. Use the action below to regenerate a key. The plaintext key is only shown once when generated.'
        }),
        ('Statistics', {
            'fields': ('training_data_count',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'published_at')
        }),
    )
    
    def has_api_key(self, obj):
        """Check if agent has an API key configured (legacy field or active AgentAPIKey rows)."""
        if obj.api_key_hash:
            return True
        return obj.agent_api_keys.filter(is_active=True).exclude(
            api_key_hash__isnull=True
        ).exclude(api_key_hash='').exists()
    has_api_key.boolean = True
    has_api_key.short_description = 'Has API Key'
    
    def api_key_preview_short(self, obj):
        """Display short API key preview in list view."""
        if obj.api_key_hash:
            preview = obj.api_key_preview
            return format_html('<code style="font-size: 0.9em;">{}</code>', preview)
        return format_html('<span style="color: #999;">—</span>')
    api_key_preview_short.short_description = 'API Key Preview'
    
    def api_key_hash_display(self, obj):
        """Display full API key hash in list view."""
        if obj.api_key_hash:
            return format_html(
                '<code style="font-size: 0.75em; word-break: break-all; max-width: 200px; display: inline-block;">{}</code>',
                obj.api_key_hash
            )
        return format_html('<span style="color: #999;">—</span>')
    api_key_hash_display.short_description = 'API Key Hash'
    
    def api_key_preview_display(self, obj):
        """Display API key preview in detail view."""
        if obj.api_key_hash:
            preview = obj.api_key_preview
            full_hash = obj.api_key_hash
            return format_html(
                '<div><code style="font-size: 1.1em; padding: 5px; background: #f5f5f5; display: inline-block;">{}</code></div>'
                '<div style="margin-top: 5px; color: #666; font-size: 0.9em;">Full hash: <code>{}</code></div>',
                preview, full_hash
            )
        return format_html('<span style="color: #999;">No API key set. Use the "Regenerate API key" action to create one.</span>')
    api_key_preview_display.short_description = 'API Key Preview'
    
    def regenerate_api_key_action(self, request, queryset):
        """Admin action to regenerate API keys for selected agents."""
        if queryset.count() > 10:
            self.message_user(request, 'Please select 10 or fewer agents at a time.', level='ERROR')
            return
        
        results = []
        errors = []
        skipped = []
        
        for agent in queryset:
            if agent.status != 'published':
                skipped.append(f'{agent.name} (not published)')
                continue
            
            try:
                # Generate new API key
                new_key = agent.generate_api_key()
                results.append(f'{agent.name}: {new_key}')
            except Exception as e:
                errors.append(f'{agent.name}: {str(e)}')
        
        # Display results
        if results:
            message = 'API Keys Generated (copy these keys - they will not be shown again):\n\n' + '\n'.join(results)
            self.message_user(request, message, level='SUCCESS')
        
        if skipped:
            self.message_user(request, f'Skipped {len(skipped)} agent(s) (not published): {", ".join(skipped)}', level='WARNING')
        
        if errors:
            self.message_user(request, f'Errors: {", ".join(errors)}', level='ERROR')
        
        if not results and not skipped and not errors:
            self.message_user(request, 'No agents selected.', level='WARNING')
    regenerate_api_key_action.short_description = 'Rotate all API keys for selected agents (published only; copies keys in success message)'


@admin.register(TrainingData)
class TrainingDataAdmin(admin.ModelAdmin):
    """Admin interface for TrainingData model."""
    list_display = ['agent', 'data_type', 'uploaded_at']
    list_filter = ['data_type', 'uploaded_at']
    search_fields = ['agent__name']


@admin.register(TestResult)
class TestResultAdmin(admin.ModelAdmin):
    """Admin interface for TestResult model."""
    list_display = ['agent', 'test_query', 'passed', 'score', 'tested_at']
    list_filter = ['passed', 'tested_at']
    search_fields = ['agent__name', 'test_query']


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Admin interface for UserProfile model."""
    list_display = ['user', 'has_api_key', 'api_key_created_at', 'api_key_last_used', 'created_at']
    list_filter = ['api_key_created_at', 'api_key_last_used', 'created_at']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['api_key_hash', 'api_key_created_at', 'api_key_last_used', 'created_at', 'updated_at']
    
    def has_api_key(self, obj):
        return obj.api_key is not None
    has_api_key.boolean = True
    has_api_key.short_description = 'Has API Key'


@admin.register(UserAPIKey)
class UserAPIKeyAdmin(admin.ModelAdmin):
    """Admin interface for UserAPIKey model."""
    list_display = ['name', 'user', 'preview', 'is_active', 'created_at', 'last_used']
    list_filter = ['is_active', 'created_at', 'last_used']
    search_fields = ['user__username', 'user__email', 'name', 'api_key']
    readonly_fields = ['api_key_hash', 'created_at', 'last_used']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('user', 'name')
        }),
        ('API Key', {
            'fields': ('api_key', 'api_key_hash', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'last_used')
        }),
    )
    
    def preview(self, obj):
        return obj.preview or 'N/A'
    preview.short_description = 'Preview'


@admin.register(AgentRequest)
class AgentRequestAdmin(admin.ModelAdmin):
    """Admin interface for AgentRequest model."""
    list_display = ['request_id', 'agent', 'status', 'created_at', 'completed_at', 'has_error']
    list_filter = ['status', 'created_at', 'completed_at']
    search_fields = ['request_id', 'agent__name', 'message', 'response']
    readonly_fields = ['request_id', 'celery_task_id', 'created_at', 'updated_at', 'completed_at']
    
    fieldsets = (
        ('Request Information', {
            'fields': ('request_id', 'agent', 'conversation', 'status', 'celery_task_id')
        }),
        ('Message', {
            'fields': ('message',)
        }),
        ('Response', {
            'fields': ('response', 'tool_calls', 'iterations')
        }),
        ('Error Information', {
            'fields': ('error_message',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'completed_at')
        }),
    )
    
    def has_error(self, obj):
        return obj.status == 'failed' and bool(obj.error_message)
    has_error.boolean = True
    has_error.short_description = 'Has Error'


@admin.register(InferenceProfile)
class InferenceProfileAdmin(admin.ModelAdmin):
    """Admin interface for InferenceProfile model."""
    list_display = ['profile_name', 'model', 'status', 'profile_arn', 'created_by', 'created_at']
    list_filter = ['status', 'created_at', 'model__provider']
    search_fields = ['profile_name', 'model__model_name', 'profile_arn']
    readonly_fields = ['profile_arn', 'created_at', 'updated_at', 'last_used']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('profile_name', 'model', 'created_by')
        }),
        ('AWS Configuration', {
            'fields': ('profile_arn', 'status', 'regions', 'configuration')
        }),
        ('Metadata', {
            'fields': ('metadata',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'last_used')
        }),
    )


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    """Admin interface for SystemSettings model."""
    list_display = ['key', 'value', 'description', 'updated_at', 'updated_by']
    list_filter = ['updated_at']
    search_fields = ['key', 'description', 'value']
    readonly_fields = ['updated_at']
    
    fieldsets = (
        ('Setting Information', {
            'fields': ('key', 'value', 'description')
        }),
        ('Metadata', {
            'fields': ('updated_at', 'updated_by')
        }),
    )


@admin.register(SocialLink)
class SocialLinkAdmin(admin.ModelAdmin):
    """Admin interface for footer social media links."""
    list_display = ['name', 'platform', 'url', 'order', 'is_active', 'updated_at']
    list_filter = ['platform', 'is_active']
    search_fields = ['name', 'url']
    list_editable = ['order', 'is_active']
    ordering = ['order', 'name']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        (None, {
            'fields': ('name', 'url', 'platform', 'order', 'is_active')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    """Admin interface for Conversation model."""
    list_display = ['conversation_id', 'agent', 'user', 'status', 'message_count', 'created_at', 'updated_at']
    list_filter = ['status', 'created_at', 'updated_at', 'agent__status']
    search_fields = ['conversation_id', 'agent__name', 'user__username', 'user__email']
    readonly_fields = ['conversation_id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Conversation Information', {
            'fields': ('conversation_id', 'agent', 'user', 'status')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )
    
    def message_count(self, obj):
        """Display number of messages in conversation."""
        return obj.messages.count()
    message_count.short_description = 'Messages'


@admin.register(ConversationMessage)
class ConversationMessageAdmin(admin.ModelAdmin):
    """Admin interface for ConversationMessage model."""
    list_display = ['id', 'conversation', 'message_type', 'content_preview', 'tool_name', 'created_at']
    list_filter = ['message_type', 'created_at', 'tool_name']
    search_fields = ['conversation__conversation_id', 'content', 'tool_name']
    readonly_fields = ['created_at']
    
    fieldsets = (
        ('Message Information', {
            'fields': ('conversation', 'message_type', 'content')
        }),
        ('Tool Information', {
            'fields': ('tool_name', 'tool_parameters', 'tool_result'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at',)
        }),
    )
    
    def content_preview(self, obj):
        """Display truncated content preview."""
        if len(obj.content) > 100:
            return obj.content[:100] + '...'
        return obj.content
    content_preview.short_description = 'Content'
