"""Admin interface for connectors."""
from django.contrib import admin
from .models import Connector, ConnectorSync, SocialMediaOAuthConfig


@admin.register(Connector)
class ConnectorAdmin(admin.ModelAdmin):
    """Admin interface for Connector model."""
    list_display = ['name', 'connector_type', 'user', 'status', 'base_url', 'connected_at', 'last_sync_at']
    list_filter = ['connector_type', 'status', 'created_at']
    search_fields = ['name', 'base_url', 'user__username']
    readonly_fields = ['created_at', 'updated_at', 'connected_at', 'last_sync_at']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'connector_type', 'user', 'status')
        }),
        ('Connection Details', {
            'fields': ('base_url', 'site_url', 'client_id', 'client_secret')
        }),
        ('OAuth Tokens', {
            'fields': ('access_token', 'refresh_token', 'token_expires_at'),
            'classes': ('collapse',)
        }),
        ('Configuration', {
            'fields': ('configuration', 'metadata')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'connected_at', 'last_sync_at')
        }),
    )


@admin.register(SocialMediaOAuthConfig)
class SocialMediaOAuthConfigAdmin(admin.ModelAdmin):
    """Admin interface for Social Media OAuth Configuration."""
    list_display = ['platform', 'is_active', 'client_id', 'created_at', 'updated_at']
    list_filter = ['platform', 'is_active', 'created_at']
    search_fields = ['platform', 'notes']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Platform Configuration', {
            'fields': ('platform', 'is_active')
        }),
        ('OAuth Credentials', {
            'fields': ('client_id', 'client_secret'),
            'description': 'OAuth credentials provided by the social media platform. These are used by all users for OAuth authentication.'
        }),
        ('OAuth Settings', {
            'fields': ('redirect_uri_base', 'scopes'),
            'description': 'Redirect URI base and required OAuth scopes for this platform.'
        }),
        ('Notes', {
            'fields': ('notes',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )
    
    def has_delete_permission(self, request, obj=None):
        """Prevent deletion of active OAuth configs."""
        if obj and obj.is_active:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(ConnectorSync)
class ConnectorSyncAdmin(admin.ModelAdmin):
    """Admin interface for ConnectorSync model."""
    list_display = ['connector', 'agent', 'sync_type', 'status', 'items_synced', 'items_failed', 'started_at', 'completed_at']
    list_filter = ['status', 'sync_type', 'started_at']
    search_fields = ['connector__name', 'agent__name', 'sync_type']
    readonly_fields = ['created_at', 'started_at', 'completed_at']
    
    fieldsets = (
        ('Sync Information', {
            'fields': ('connector', 'agent', 'sync_type', 'status')
        }),
        ('Results', {
            'fields': ('items_synced', 'items_failed', 'result_data', 'error_message')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'started_at', 'completed_at')
        }),
    )
