"""Connector models for external service integrations."""
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class ConnectorType(models.TextChoices):
    """Supported connector types."""
    JIRA = 'jira', 'JIRA'
    CONFLUENCE = 'confluence', 'Confluence'
    GITLAB = 'gitlab', 'GitLab'
    SLACK = 'slack', 'Slack'
    GITHUB = 'github', 'GitHub'
    GOOGLE = 'google', 'Google'
    MICROSOFT = 'microsoft', 'Microsoft'
    LINKEDIN = 'linkedin', 'LinkedIn'
    FACEBOOK = 'facebook', 'Facebook'
    INSTAGRAM = 'instagram', 'Instagram'
    TWITTER = 'twitter', 'X'


class Connector(models.Model):
    """Model for external service connectors."""
    
    STATUS_CHOICES = [
        ('disconnected', 'Disconnected'),
        ('connecting', 'Connecting'),
        ('connected', 'Connected'),
        ('error', 'Error'),
    ]
    
    name = models.CharField(max_length=200, help_text="Friendly name for this connector")
    connector_type = models.CharField(max_length=50, choices=ConnectorType.choices)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='connectors')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='disconnected')
    
    # OAuth credentials (encrypted in production)
    client_id = models.CharField(max_length=255, blank=True, help_text="OAuth Client ID")
    client_secret = models.CharField(max_length=255, blank=True, help_text="OAuth Client Secret")
    access_token = models.TextField(blank=True, help_text="OAuth Access Token")
    refresh_token = models.TextField(blank=True, help_text="OAuth Refresh Token")
    token_expires_at = models.DateTimeField(null=True, blank=True, help_text="Token expiration time")
    
    # Service-specific configuration
    base_url = models.URLField(help_text="Base URL of the service (e.g., https://your-domain.atlassian.net)")
    site_url = models.URLField(blank=True, help_text="Site URL for OAuth callback")
    
    # Metadata
    metadata = models.JSONField(default=dict, blank=True, help_text="Additional connector metadata")
    configuration = models.JSONField(default=dict, blank=True, help_text="Connector-specific configuration")
    
    # Timestamps
    connected_at = models.DateTimeField(null=True, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = [['user', 'connector_type', 'base_url']]
    
    def __str__(self):
        return f"{self.name} ({self.get_connector_type_display()}) - {self.get_status_display()}"
    
    def is_token_valid(self):
        """Check if the access token is still valid."""
        if not self.access_token:
            return False
        if self.token_expires_at and self.token_expires_at <= timezone.now():
            return False
        return True
    
    def update_token(self, access_token, refresh_token=None, expires_in=None):
        """Update OAuth tokens."""
        self.access_token = access_token
        if refresh_token:
            self.refresh_token = refresh_token
        if expires_in:
            from datetime import timedelta
            self.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
        self.save(update_fields=['access_token', 'refresh_token', 'token_expires_at'])


class SocialMediaOAuthConfig(models.Model):
    """Admin-managed OAuth credentials for social media platforms."""
    
    PLATFORM_CHOICES = [
        ('linkedin', 'LinkedIn'),
        ('facebook', 'Facebook'),
        ('twitter', 'X'),
        ('instagram', 'Instagram'),
        ('google', 'Google'),
    ]
    
    platform = models.CharField(max_length=50, choices=PLATFORM_CHOICES, unique=True, help_text="Social media platform")
    client_id = models.CharField(max_length=255, help_text="OAuth Client ID (managed by admin)")
    client_secret = models.CharField(max_length=255, help_text="OAuth Client Secret (managed by admin)")
    is_active = models.BooleanField(default=True, help_text="Whether this OAuth config is active")
    
    # Additional configuration
    redirect_uri_base = models.URLField(blank=True, help_text="Base redirect URI (will be appended with platform-specific path)")
    scopes = models.TextField(blank=True, help_text="Comma-separated list of OAuth scopes")
    
    # Metadata
    notes = models.TextField(blank=True, help_text="Admin notes about this OAuth configuration")
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['platform']
        verbose_name = 'Social Media OAuth Configuration'
        verbose_name_plural = 'Social Media OAuth Configurations'
    
    def __str__(self):
        return f"{self.get_platform_display()} OAuth Config"
    
    @classmethod
    def get_config(cls, platform):
        """Get active OAuth config for a platform."""
        try:
            return cls.objects.get(platform=platform, is_active=True)
        except cls.DoesNotExist:
            return None


class ConnectorSync(models.Model):
    """Track synchronization of data from connectors."""
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    connector = models.ForeignKey(Connector, on_delete=models.CASCADE, related_name='syncs')
    agent = models.ForeignKey('agents_app.Agent', on_delete=models.CASCADE, related_name='connector_syncs', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Sync details
    sync_type = models.CharField(max_length=50, help_text="Type of sync (e.g., 'jira_issues', 'confluence_pages')")
    items_synced = models.IntegerField(default=0)
    items_failed = models.IntegerField(default=0)
    
    # Results
    result_data = models.JSONField(default=dict, blank=True, help_text="Sync result details")
    error_message = models.TextField(blank=True)
    
    # Timestamps
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.connector.name} - {self.sync_type} - {self.get_status_display()}"
