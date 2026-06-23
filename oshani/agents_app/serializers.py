"""Serializers for agents_app."""
from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Agent, TrainingData, TestResult, AIModel, AgentFeedback, CustomTool, Conversation, ConversationMessage, ToolCall, MCPServer, Notification, UserProfile, InferenceProfile


class UserSerializer(serializers.ModelSerializer):
    """Serializer for User model."""
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email']


class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for UserProfile model."""
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    has_api_key = serializers.SerializerMethodField()
    api_key_preview = serializers.SerializerMethodField()
    
    class Meta:
        model = UserProfile
        fields = ['id', 'username', 'email', 'has_api_key', 'api_key_preview', 
                  'api_key_created_at', 'api_key_last_used', 'created_at', 'updated_at']
        read_only_fields = ['api_key_created_at', 'api_key_last_used', 'created_at', 'updated_at']
    
    def get_has_api_key(self, obj):
        """Check if user has an API key."""
        return obj.api_key is not None
    
    def get_api_key_preview(self, obj):
        """Return a preview of the API key (first 8 and last 4 characters)."""
        if obj.api_key:
            return f"{obj.api_key[:8]}...{obj.api_key[-4:]}"
        return None


class GenerateAPIKeySerializer(serializers.Serializer):
    """Serializer for generating API key."""
    regenerate = serializers.BooleanField(default=False, help_text="Regenerate even if key exists")


class TrainingDataSerializer(serializers.ModelSerializer):
    """Serializer for TrainingData model."""
    
    class Meta:
        model = TrainingData
        fields = ['id', 'agent', 'data_type', 'content', 'file_path', 'uploaded_at']
        read_only_fields = ['uploaded_at']


class TestResultSerializer(serializers.ModelSerializer):
    """Serializer for TestResult model."""
    
    class Meta:
        model = TestResult
        fields = ['id', 'agent', 'test_query', 'expected_response', 'actual_response', 
                  'score', 'passed', 'tested_at']
        read_only_fields = ['tested_at']


class AgentFeedbackSerializer(serializers.ModelSerializer):
    """Serializer for AgentFeedback model."""
    
    class Meta:
        model = AgentFeedback
        fields = ['id', 'agent', 'user', 'query', 'response', 'feedback_type', 'feedback_text', 'created_at']
        read_only_fields = ['created_at']


class AIModelSerializer(serializers.ModelSerializer):
    """Serializer for AI Model."""
    
    class Meta:
        model = AIModel
        fields = ['id', 'model_id', 'model_name', 'provider', 'description', 
                  'input_modalities', 'output_modalities', 'use_cases', 
                  'is_available', 'metadata', 'last_checked']


class CreateConversationSerializer(serializers.Serializer):
    """Serializer for creating a conversation."""
    message = serializers.CharField(required=True)
    agent_id = serializers.IntegerField(required=False)  # Optional if using API key


class ContinueConversationSerializer(serializers.Serializer):
    """Serializer for continuing a conversation."""
    message = serializers.CharField(required=True)
    conversation_id = serializers.CharField(required=True)


class AgentWebhookSerializer(serializers.Serializer):
    """Inbound webhook body: user-visible text plus optional structured payload for the agent."""

    message = serializers.CharField(required=False, allow_blank=True, default='')
    text = serializers.CharField(required=False, allow_blank=True, default='')
    payload = serializers.JSONField(required=False, default=None)
    metadata = serializers.JSONField(required=False, default=dict)

    def validate(self, data):
        msg = (data.get('message') or '').strip()
        txt = (data.get('text') or '').strip()
        payload = data.get('payload')
        has_text = bool(msg or txt)
        has_payload = payload is not None
        if not has_text and not has_payload:
            raise serializers.ValidationError(
                'Provide at least one of: message, text, or payload.'
            )
        return data

    def composed_message(self) -> str:
        """Build the single user message passed to the agent (after validation)."""
        import json

        parts = []
        primary = (self.validated_data.get('message') or '').strip()
        if not primary:
            primary = (self.validated_data.get('text') or '').strip()
        if primary:
            parts.append(primary)
        payload = self.validated_data.get('payload')
        if payload is not None:
            parts.append(
                '\n\n[Webhook payload]\n'
                + json.dumps(payload, indent=2, ensure_ascii=False, default=str)
            )
        return '\n'.join(parts).strip()


class GetAnswerSerializer(serializers.Serializer):
    """Serializer for getting answer."""
    conversation_id = serializers.CharField(required=True)


class FindConversationSerializer(serializers.Serializer):
    """Serializer for finding conversation."""
    conversation_id = serializers.CharField(required=True)


class FileUploadSerializer(serializers.Serializer):
    """Serializer for file upload."""
    file_name = serializers.CharField(required=False)
    file_type = serializers.CharField(required=False)


class CustomToolSerializer(serializers.ModelSerializer):
    """Serializer for Custom Tool."""
    
    class Meta:
        model = CustomTool
        fields = ['id', 'display_name', 'function_name', 'description', 'instructions',
                  'url', 'method', 'headers', 'parameters', 'is_active', 'created_at']


class AgentSerializer(serializers.ModelSerializer):
    """Serializer for Agent model."""
    
    user = UserSerializer(read_only=True)
    model = AIModelSerializer(read_only=True)
    training_data_count = serializers.IntegerField(read_only=True)
    api_key = serializers.CharField(read_only=True)  # Only show on creation/regeneration
    
    class Meta:
        model = Agent
        fields = ['id', 'name', 'description', 'agent_type', 'status', 'configuration',
                  'api_key', 'user', 'model', 'quick_suite_space_id', 'quick_suite_agent_id',
                  'training_data_count', 'created_at', 'updated_at', 'published_at']
        read_only_fields = ['created_at', 'updated_at', 'published_at', 
                           'quick_suite_space_id', 'quick_suite_agent_id']
    
    def create(self, validated_data):
        """Create a new agent."""
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)


class AgentListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for agent lists."""
    
    class Meta:
        model = Agent
        fields = ['id', 'name', 'description', 'agent_type', 'status', 
                  'training_data_count', 'created_at', 'updated_at']


class AgentCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating agents.
    
    Model selection is REQUIRED - each agent must be configured with a specific LLM model.
    """
    
    model_id = serializers.IntegerField(write_only=True, required=True, help_text="ID of the AI model to use for this agent")
    
    class Meta:
        model = Agent
        fields = ['name', 'description', 'agent_type', 'configuration', 'model_id']
    
    def validate_model_id(self, value):
        """Validate that the model exists and is available."""
        try:
            model = AIModel.objects.get(id=value, is_available=True)
            return value
        except AIModel.DoesNotExist:
            raise serializers.ValidationError(f"Model with ID {value} not found or not available. Please select a valid model.")
    
    def create(self, validated_data):
        """Create a new agent with the selected model."""
        model_id = validated_data.pop('model_id')
        user = self.context['request'].user
        validated_data['user'] = user
        validated_data['status'] = 'draft'
        agent = super().create(validated_data)
        
        # Set model - already validated in validate_model_id
        model = AIModel.objects.get(id=model_id, is_available=True)
        agent.model = model
        
        # Store model information in configuration for reference
        if not agent.configuration:
            agent.configuration = {}
        agent.configuration['model_id'] = model.model_id
        agent.configuration['model_name'] = model.model_name
        agent.configuration['model_provider'] = model.provider
        agent.save()
        
        # Track agent creation usage
        try:
            from agents_app.platform_utils import track_usage
            track_usage(user, 'agent_creations', count=1, metadata={'agent_id': agent.id, 'agent_name': agent.name})
        except ImportError:
            # billing_app not available, skip tracking
            pass
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error tracking agent creation: {str(e)}")
        
        return agent


class ChatRequestSerializer(serializers.Serializer):
    """Serializer for chat requests."""
    message = serializers.CharField()
    context = serializers.JSONField(required=False, default=dict)


class QueryRequestSerializer(serializers.Serializer):
    """Serializer for query requests."""
    query = serializers.CharField()
    parameters = serializers.JSONField(required=False, default=dict)


class InvokeRequestSerializer(serializers.Serializer):
    """Serializer for invoke requests."""
    action = serializers.CharField()
    parameters = serializers.JSONField(required=False, default=dict)


class MCPServerSerializer(serializers.ModelSerializer):
    """Serializer for MCP Server configuration."""
    
    class Meta:
        model = MCPServer
        fields = ['id', 'agent', 'name', 'description', 'transport_type', 'command', 'args',
                  'url', 'headers', 'is_active', 'auto_connect', 'configuration',
                  'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']
    
    def validate(self, data):
        """Validate MCP server configuration based on transport type."""
        transport_type = data.get('transport_type', 'stdio')
        
        if transport_type == 'stdio':
            if not data.get('command'):
                raise serializers.ValidationError({
                    'command': 'Command is required for stdio transport'
                })
        elif transport_type in ['http', 'sse']:
            if not data.get('url'):
                raise serializers.ValidationError({
                    'url': 'URL is required for HTTP/SSE transport'
                })
        
        return data


class NotificationSerializer(serializers.ModelSerializer):
    """Serializer for notifications."""
    
    agent_name = serializers.CharField(source='agent.name', read_only=True)
    
    class Meta:
        model = Notification
        fields = ['id', 'user', 'agent', 'agent_name', 'notification_type', 'title', 'message',
                  'data', 'is_read', 'created_at']
        read_only_fields = ['created_at']


class InferenceProfileSerializer(serializers.ModelSerializer):
    """Serializer for Inference Profile."""
    
    model_name = serializers.CharField(source='model.model_name', read_only=True)
    model_id = serializers.CharField(source='model.model_id', read_only=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    is_available = serializers.SerializerMethodField()
    
    class Meta:
        model = InferenceProfile
        fields = ['id', 'model', 'model_name', 'model_id', 'profile_name', 'profile_arn', 
                  'status', 'regions', 'configuration', 'metadata', 'created_by', 
                  'created_by_username', 'created_at', 'updated_at', 'last_used', 'is_available']
        read_only_fields = ['profile_arn', 'status', 'created_at', 'updated_at', 'last_used']
    
    def get_is_available(self, obj):
        """Check if inference profile is available."""
        return obj.is_available()


class CreateInferenceProfileSerializer(serializers.Serializer):
    """Serializer for creating inference profile."""
    profile_name = serializers.CharField(required=True, help_text="Unique name for the inference profile")
    model_id = serializers.IntegerField(required=True, help_text="ID of the model requiring inference profile")
    regions = serializers.ListField(
        child=serializers.CharField(),
        required=True,
        help_text="List of AWS regions (e.g., ['us-east-1', 'us-west-2'])"
    )
    tags = serializers.DictField(required=False, help_text="Optional tags for the inference profile")

