"""API views for agents_app."""
import json
from rest_framework import viewsets, status, serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.authentication import SessionAuthentication
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
import logging
from .models import (
    Agent, TrainingData, TestResult, AIModel, Conversation, ConversationMessage, 
    ToolCall, CustomTool, ConversationFile, MCPServer, Notification, UserProfile, InferenceProfile
)
from .authentication import AgentAPIKeyAuthentication
from .serializers import (
    AgentSerializer, AgentListSerializer, AgentCreateSerializer,
    TrainingDataSerializer, TestResultSerializer,
    ChatRequestSerializer, QueryRequestSerializer, InvokeRequestSerializer,
    AIModelSerializer, CreateConversationSerializer, ContinueConversationSerializer,
    AgentWebhookSerializer,
    GetAnswerSerializer, FindConversationSerializer, FileUploadSerializer,
    MCPServerSerializer, NotificationSerializer, UserProfileSerializer,
    InferenceProfileSerializer, CreateInferenceProfileSerializer
)
from .permissions import IsAgentOwner, HasAgentAPIKey, IsPublishedAgent, SessionOrAgentAPIKeyPermission
from .aws_integration import get_bedrock_client, get_quick_suite_client
from .tasks import sync_available_models
from .agent_loop import AgentLoop
from django.utils import timezone

logger = logging.getLogger(__name__)


class AIModelViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for listing available AI models."""
    
    permission_classes = [IsAuthenticated]
    serializer_class = AIModelSerializer
    
    def get_queryset(self):
        """Get queryset of available models."""
        queryset = AIModel.objects.filter(is_available=True)
        
        # Filter by provider if provided
        provider = self.request.query_params.get('provider', None)
        if provider:
            queryset = queryset.filter(provider=provider)
        
        return queryset
    
    @action(detail=False, methods=['post'])
    def sync(self, request):
        """Manually trigger model sync."""
        sync_available_models.delay()
        return Response({'status': 'sync_started'}, status=status.HTTP_202_ACCEPTED)


class AgentViewSet(viewsets.ModelViewSet):
    """ViewSet for Agent CRUD operations."""
    
    permission_classes = [IsAuthenticated, IsAgentOwner]
    
    def get_queryset(self):
        """Get queryset filtered by user, including shared agents."""
        from django.db.models import Q
        from .models import AgentShare
        
        # Base query: user's own agents
        q = Q(user=self.request.user)
        
        # Add shared agents if user has an email
        if self.request.user.email:
            q |= Q(
                shares__email=self.request.user.email,
                shares__is_accepted=True,
                shares__accepted_by=self.request.user
            )
        
        # Return combined queryset with distinct to avoid duplicates
        return Agent.objects.filter(q).distinct()
    
    def get_serializer_class(self):
        """Return appropriate serializer class."""
        if self.action == 'list':
            return AgentListSerializer
        elif self.action == 'create':
            return AgentCreateSerializer
        return AgentSerializer
    
    def perform_create(self, serializer):
        """
        Create agent and initialize with selected model.
        Model is already set by the serializer (required field).
        """
        user = self.request.user
        
        # Check agent creation limit before proceeding
        try:
            from agents_app.platform_utils import can_perform_action
            can_create, current_count, limit, remaining = can_perform_action(user, 'agent_creations')
            if not can_create:
                if limit is not None:
                    raise serializers.ValidationError({
                        'non_field_errors': [f'Agent creation limit reached. You have created {current_count} of {limit} allowed agents. Please upgrade your plan to create more agents.']
                    })
        except ImportError:
            # billing_app not available, skip restriction
            pass
        except serializers.ValidationError:
            raise
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error checking agent creation limit: {str(e)}")
        
        agent = serializer.save()
        
        # Model is required and should already be set by serializer
        if not agent.model:
            raise serializers.ValidationError({
                'model_id': 'Model selection is required. Please select an LLM model for this agent.'
            })
        
        # Check model provider restrictions after agent is created (model is set)
        try:
            from agents_app.platform_utils import can_use_bedrock, can_use_ollama
            if agent.model.provider == 'bedrock':
                can_use, reason = can_use_bedrock(user)
                if not can_use:
                    # Delete the agent since it shouldn't have been created
                    agent.delete()
                    raise serializers.ValidationError({
                        'model_id': [reason or 'Your plan does not include AWS Bedrock models. Please select an Ollama model or upgrade your plan.']
                    })
            elif agent.model.provider == 'ollama':
                can_use, reason = can_use_ollama(user)
                if not can_use:
                    # Delete the agent since it shouldn't have been created
                    agent.delete()
                    raise serializers.ValidationError({
                        'model_id': [reason or 'Your plan does not include Ollama models. Please upgrade your plan.']
                    })
        except ImportError:
            # billing_app not available, skip restriction
            pass
        except serializers.ValidationError:
            raise
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error checking model provider restrictions: {str(e)}")
        
        # Note: Agent creation usage is tracked in AgentCreateSerializer.create()
        # No need to track here to avoid double-counting
        
        # Initialize with appropriate client based on model provider
        try:
            if agent.model.provider == 'bedrock':
                client = get_bedrock_client()
            else:  # ollama
                from .ollama_integration import OllamaClient, is_ollama_available
                if not is_ollama_available():
                    raise ValueError("Ollama is not configured or not reachable. Set OLLAMA_ENABLED and OLLAMA_BASE_URL and ensure the server is running.")
                client = OllamaClient()
            
            agent_result = client.create_agent(
                agent_name=agent.name,
                agent_config=agent.configuration
            )
            agent.quick_suite_agent_id = agent_result.get('agent_id')
            agent.save()
        except Exception as e:
            # Log error but don't fail agent creation
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Agent initialization error: {str(e)}")
    
    @action(detail=True, methods=['post'])
    def upload_training_data(self, request, pk=None):
        """Upload training data for an agent."""
        agent = self.get_object()
        
        if agent.status not in ['draft', 'training']:
            return Response(
                {'error': 'Agent must be in draft or training status'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = TrainingDataSerializer(data=request.data)
        if serializer.is_valid():
            training_data = serializer.save(agent=agent)
            agent.training_data_count += 1
            agent.save(update_fields=['training_data_count'])
            
            # Upload to Bedrock
            try:
                client = get_bedrock_client()
                client.upload_training_data(
                    agent_id=agent.quick_suite_agent_id or str(agent.id),
                    training_data=[training_data.content]
                )
            except Exception as e:
                print(f"Bedrock upload error: {str(e)}")
            
            return Response(TrainingDataSerializer(training_data).data, 
                          status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def train(self, request, pk=None):
        """Start training process for an agent."""
        agent = self.get_object()
        
        if agent.status != 'draft':
            return Response(
                {'error': 'Agent must be in draft status to start training'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if agent.training_data_count == 0:
            return Response(
                {'error': 'No training data uploaded'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        agent.status = 'training'
        agent.save(update_fields=['status'])
        
        # Start training in Bedrock
        try:
            client = get_bedrock_client()
            client.train_agent(agent.quick_suite_agent_id or str(agent.id))
        except Exception as e:
            print(f"Bedrock training error: {str(e)}")
        
        return Response({'status': 'training_started'}, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['get'])
    def training_status(self, request, pk=None):
        """Get training status for an agent."""
        agent = self.get_object()
        
        if agent.status != 'training':
            return Response(
                {'error': 'Agent is not in training status'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            client = get_bedrock_client()
            status_result = client.get_training_status(agent.quick_suite_agent_id or str(agent.id))
            
            # If training is complete, move to testing
            if status_result.get('status') == 'completed':
                agent.status = 'testing'
                agent.save(update_fields=['status'])
            
            return Response(status_result, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        """Run a test query on an agent."""
        agent = self.get_object()
        
        if agent.status not in ['testing', 'published']:
            return Response(
                {'error': 'Agent must be in testing or published status'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        query = serializer.validated_data['message']
        expected = request.data.get('expected_response', '')
        
        # Test agent via Quick Suite or Ollama
        try:
            # Agent must have a model configured - this is required
            if not agent.model:
                return Response(
                    {'error': 'Agent is not configured with a model. Please configure the agent with a valid LLM model.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get the model from agent - ensure it's valid
            model = agent.model
            model_provider = model.provider
            model_id = model.model_id
            
            # Validate model_id is not empty
            if not model_id or not str(model_id).strip():
                # Try to get from configuration as fallback
                model_id = agent.configuration.get('model_id') if agent.configuration else None
                if not model_id or not str(model_id).strip():
                    return Response(
                        {'error': 'Agent model ID is missing. Please reconfigure the agent with a valid model.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Ensure model_id is a string
            model_id = str(model_id).strip()
            
            # Retrieve training data for the agent
            training_data_list = []
            training_data_objs = agent.training_data.all()
            for td in training_data_objs:
                training_data_list.append({
                    'content': td.content,
                    'data_type': td.data_type,
                })
            
            # Get appropriate client based on model provider
            if model_provider == 'ollama':
                from .ollama_integration import OllamaClient, is_ollama_available
                if not is_ollama_available():
                    return Response(
                        {'error': 'Ollama is not configured or not reachable. Set OLLAMA_ENABLED and OLLAMA_BASE_URL and ensure the server is running.'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE
                    )
                client = OllamaClient()
                result = client.test_agent(str(agent.id), query, model=model_id, training_data=training_data_list if training_data_list else None)
            else:
                # Use Bedrock client
                client = get_bedrock_client()
                result = client.test_agent(agent.quick_suite_agent_id or str(agent.id), query, model=model_id, training_data=training_data_list if training_data_list else None)
            actual_response = result.get('response', '')
            
            # Create test result
            test_result = TestResult.objects.create(
                agent=agent,
                test_query=query,
                expected_response=expected,
                actual_response=actual_response,
                passed=bool(expected and expected.lower() in actual_response.lower()),
                score=1.0 if expected and expected.lower() in actual_response.lower() else 0.0
            )
            
            return Response(TestResultSerializer(test_result).data, 
                          status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'])
    def test_results(self, request, pk=None):
        """Get test results for an agent."""
        agent = self.get_object()
        test_results = agent.test_results.all()
        serializer = TestResultSerializer(test_results, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        """Publish an agent."""
        agent = self.get_object()
        
        if agent.status != 'testing':
            return Response(
                {'error': 'Agent must be in testing status to publish'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        agent.publish()
        serializer = AgentSerializer(agent)
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'])
    def regenerate_key(self, request, pk=None):
        """Regenerate API key for a published agent."""
        agent = self.get_object()
        
        if agent.status != 'published':
            return Response(
                {'error': 'Only published agents can regenerate API keys'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        new_key = agent.generate_api_key()
        return Response({'api_key': new_key}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def add_api_key(self, request, pk=None):
        """Add another API key without revoking existing keys."""
        agent = self.get_object()
        if agent.status != 'published':
            return Response(
                {'error': 'Only published agents can add API keys'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        name = (request.data.get('name') or '').strip() or None
        new_key = agent.add_api_key(name=name)
        return Response({'api_key': new_key}, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'])
    def unpublish(self, request, pk=None):
        """Unpublish an agent."""
        agent = self.get_object()
        
        if agent.status != 'published':
            return Response(
                {'error': 'Agent is not published'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        agent.status = 'draft'
        agent.api_key = None
        agent.api_key_hash = None
        agent.published_at = None
        agent.save(update_fields=['status', 'api_key', 'api_key_hash', 'published_at'])
        agent.agent_api_keys.update(is_active=False)
        
        return Response({'status': 'unpublished'}, status=status.HTTP_200_OK)
    
    # Agent interaction endpoints (require API key)
    @action(detail=True, methods=['post'], 
            permission_classes=[HasAgentAPIKey, IsPublishedAgent])
    def chat(self, request, pk=None):
        """Chat with a published agent."""
        agent = request.auth  # Set by authentication
        
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        message = serializer.validated_data['message']
        context = serializer.validated_data.get('context', {})
        
        try:
            # Agent must have a model configured - this is required
            if not agent.model:
                return Response(
                    {'error': 'Agent is not configured with a model. Please configure the agent with a valid LLM model.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get the model from agent - ensure it's valid
            model = agent.model
            model_provider = model.provider
            model_id = model.model_id
            
            # Validate model_id is not empty
            if not model_id or not str(model_id).strip():
                # Try to get from configuration as fallback
                model_id = agent.configuration.get('model_id') if agent.configuration else None
                if not model_id or not str(model_id).strip():
                    return Response(
                        {'error': 'Agent model ID is missing. Please reconfigure the agent with a valid model.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Ensure model_id is a string
            model_id = str(model_id).strip()
            
            # Retrieve training data for the agent
            training_data_list = []
            training_data_objs = agent.training_data.all()
            for td in training_data_objs:
                training_data_list.append({
                    'content': td.content,
                    'data_type': td.data_type,
                })
            
            # Get appropriate client
            if model_provider == 'ollama':
                from .ollama_integration import OllamaClient, is_ollama_available
                if not is_ollama_available():
                    return Response(
                        {'error': 'Ollama is not configured or not reachable. Set OLLAMA_ENABLED and OLLAMA_BASE_URL and ensure the server is running.'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE
                    )
                client = OllamaClient()
                result = client.invoke_agent(
                    agent_id=str(agent.id),
                    query=message,
                    context=context,
                    model=model_id,
                    system_prompt=agent.configuration.get('system_prompt') if agent.configuration else None,
                    training_data=training_data_list if training_data_list else None
                )
            else:
                # Use Bedrock client
                client = get_bedrock_client()
                system_prompt = agent.configuration.get('system_prompt') if agent.configuration else None
                result = client.invoke_agent(
                    agent_id=agent.quick_suite_agent_id or str(agent.id),
                    query=message,
                    context=context,
                    model=model_id,
                    system_prompt=system_prompt,
                    model_provider=model_provider,
                    training_data=training_data_list if training_data_list else None
                )
            
            return Response(result, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'],
            permission_classes=[HasAgentAPIKey, IsPublishedAgent])
    def query(self, request, pk=None):
        """Query a published agent."""
        agent = request.auth
        
        serializer = QueryRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        query = serializer.validated_data['query']
        parameters = serializer.validated_data.get('parameters', {})
        
        try:
            # Agent must have a model configured - this is required
            if not agent.model:
                return Response(
                    {'error': 'Agent is not configured with a model. Please configure the agent with a valid LLM model.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get the model from agent - ensure it's valid
            model = agent.model
            model_provider = model.provider
            model_id = model.model_id
            
            # Validate model_id is not empty
            if not model_id or not str(model_id).strip():
                # Try to get from configuration as fallback
                model_id = agent.configuration.get('model_id') if agent.configuration else None
                if not model_id or not str(model_id).strip():
                    return Response(
                        {'error': 'Agent model ID is missing. Please reconfigure the agent with a valid model.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Ensure model_id is a string
            model_id = str(model_id).strip()
            
            # Retrieve training data for the agent
            training_data_list = []
            training_data_objs = agent.training_data.all()
            for td in training_data_objs:
                training_data_list.append({
                    'content': td.content,
                    'data_type': td.data_type,
                })
            
            # Get appropriate client based on model provider
            if model_provider == 'ollama':
                from .ollama_integration import OllamaClient, is_ollama_available
                if not is_ollama_available():
                    return Response(
                        {'error': 'Ollama is not configured or not reachable. Set OLLAMA_ENABLED and OLLAMA_BASE_URL and ensure the server is running.'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE
                    )
                client = OllamaClient()
                result = client.invoke_agent(
                    agent_id=str(agent.id),
                    query=query,
                    context=parameters,
                    model=model_id,
                    system_prompt=agent.configuration.get('system_prompt') if agent.configuration else None,
                    training_data=training_data_list if training_data_list else None
                )
            else:
                client = get_bedrock_client()
                system_prompt = agent.configuration.get('system_prompt') if agent.configuration else None
                result = client.invoke_agent(
                    agent_id=agent.quick_suite_agent_id or str(agent.id),
                    query=query,
                    context=parameters,
                    model=model_id,
                    system_prompt=system_prompt,
                    model_provider=model_provider,
                    training_data=training_data_list if training_data_list else None
                )
            return Response(result, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'],
            permission_classes=[HasAgentAPIKey, IsPublishedAgent])
    def invoke(self, request, pk=None):
        """Invoke an action on a published agent."""
        agent = request.auth
        
        serializer = InvokeRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        action_name = serializer.validated_data['action']
        parameters = serializer.validated_data.get('parameters', {})
        
        try:
            # Agent must have a model configured - this is required
            if not agent.model:
                return Response(
                    {'error': 'Agent is not configured with a model. Please configure the agent with a valid LLM model.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get the model from agent - ensure it's valid
            model = agent.model
            model_provider = model.provider
            model_id = model.model_id
            
            # Validate model_id is not empty
            if not model_id or not str(model_id).strip():
                # Try to get from configuration as fallback
                model_id = agent.configuration.get('model_id') if agent.configuration else None
                if not model_id or not str(model_id).strip():
                    return Response(
                        {'error': 'Agent model ID is missing. Please reconfigure the agent with a valid model.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Ensure model_id is a string
            model_id = str(model_id).strip()
            
            # Use AgentLoop for tool calling support
            query = f"Action: {action_name}"
            if parameters:
                query += f"\nParameters: {json.dumps(parameters)}"
            
            # Get or create conversation
            # For testing status agents, always start fresh - create new conversation
            conversation = None
            if agent.status != 'testing':
                conversation = Conversation.objects.filter(
                    agent=agent,
                    user=agent.user,
                    status='active'
                ).order_by('-updated_at').first()
            
            # Create new conversation if none exists (or agent is testing)
            if not conversation:
                import uuid
                conversation = Conversation.objects.create(
                    agent=agent,
                    user=agent.user,
                    conversation_id=str(uuid.uuid4()),
                    status='active'
                )
            
            # Create agent loop
            agent_loop = AgentLoop(agent, conversation)
            
            # Get system prompt
            system_prompt = agent.configuration.get('instruction') or agent.configuration.get('system_prompt', '')
            
            # Execute agent loop
            result = agent_loop.execute(query, system_prompt)
            
            return Response({
                'response': result.get('response', ''),
                'tool_calls': result.get('tool_calls', []),
                'iterations': result.get('iterations', 1),
                'conversation_id': result.get('conversation_id', '')
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'],
            permission_classes=[HasAgentAPIKey, IsPublishedAgent])
    def agent_status(self, request, pk=None):
        """Get agent status and metrics."""
        agent = request.auth
        
        try:
            # Determine which client to use based on agent's model
            if agent.model:
                model_provider = agent.model.provider
            else:
                model_provider = agent.configuration.get('provider') if agent.configuration else None
            
            # Get appropriate client
            if model_provider == 'ollama':
                from .ollama_integration import OllamaClient, is_ollama_available
                if not is_ollama_available():
                    return Response(
                        {'error': 'Ollama is not configured or not reachable. Set OLLAMA_ENABLED and OLLAMA_BASE_URL and ensure the server is running.'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE
                    )
                client = OllamaClient()
            else:
                client = get_bedrock_client()
            
            status_result = client.get_agent_status(agent.quick_suite_agent_id or str(agent.id))
            
            return Response({
                'agent_id': agent.id,
                'name': agent.name,
                'status': agent.status,
                'bedrock_status': status_result,
                'training_data_count': agent.training_data_count,
                'published_at': agent.published_at
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'],
            permission_classes=[HasAgentAPIKey, IsPublishedAgent])
    def history(self, request, pk=None):
        """Get interaction history for an agent."""
        agent = request.auth
        # In a full implementation, this would track actual interactions
        # For now, return test results as history
        test_results = agent.test_results.all()[:50]  # Last 50
        serializer = TestResultSerializer(test_results, many=True)
        return Response(serializer.data)


class MCPServerViewSet(viewsets.ModelViewSet):
    """ViewSet for MCP Server configuration."""
    serializer_class = MCPServerSerializer
    permission_classes = [IsAuthenticated]
    authentication_classes = [AgentAPIKeyAuthentication]
    
    def get_queryset(self):
        """Filter MCP servers by agent and user."""
        user = self.request.user
        agent_id = self.request.query_params.get('agent_id', None)
        
        if agent_id:
            return MCPServer.objects.filter(agent__user=user, agent_id=agent_id)
        return MCPServer.objects.filter(agent__user=user)
    
    def perform_create(self, serializer):
        """Set the agent when creating MCP server."""
        agent_id = self.request.data.get('agent')
        if agent_id:
            agent = get_object_or_404(Agent, id=agent_id, user=self.request.user)
            serializer.save(agent=agent)


class NotificationViewSet(viewsets.ModelViewSet):
    """ViewSet for notifications."""
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    authentication_classes = [AgentAPIKeyAuthentication]
    
    def get_queryset(self):
        """Filter notifications by user."""
        user = self.request.user
        queryset = Notification.objects.filter(user=user)
        
        # Filter by read status if provided
        is_read = self.request.query_params.get('is_read', None)
        if is_read is not None:
            queryset = queryset.filter(is_read=is_read.lower() == 'true')
        
        return queryset.order_by('-created_at')
    
    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        """Mark notification as read."""
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=['is_read'])
        return Response({'status': 'marked_read'})
    
    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        """Mark all notifications as read."""
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({'status': 'all_marked_read'})


class UserProfileViewSet(viewsets.ModelViewSet):
    """ViewSet for user profile.
    
    Supports both session authentication (for UI/SPA) and API key authentication (for external clients).
    """
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, AgentAPIKeyAuthentication]
    
    def get_queryset(self):
        """Return user's own profile, creating it if it doesn't exist."""
        # Ensure profile exists
        UserProfile.objects.get_or_create(user=self.request.user)
        return UserProfile.objects.filter(user=self.request.user)
    
    def list(self, request, *args, **kwargs):
        """Override list to ensure profile exists and return single profile."""
        # Ensure profile exists
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        if created:
            logger.info(f"Created UserProfile for user {request.user.username} via API")
        
        # Serialize and return
        serializer = self.get_serializer(profile)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def generate_api_key(self, request):
        """Generate a new API key for the user."""
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        api_key = profile.generate_api_key()
        
        return Response({
            'api_key': api_key,
            'message': 'API key generated successfully. Save it now - you will not be able to see it again.'
        })
    
    @action(detail=False, methods=['post'])
    def revoke_api_key(self, request):
        """Revoke the current API key."""
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.revoke_api_key()
        return Response({'status': 'api_key_revoked'})


class InferenceProfileViewSet(viewsets.ModelViewSet):
    """ViewSet for Inference Profile."""
    serializer_class = InferenceProfileSerializer
    permission_classes = [IsAuthenticated]
    authentication_classes = [AgentAPIKeyAuthentication]
    
    def get_queryset(self):
        """Filter inference profiles by user."""
        user = self.request.user
        return InferenceProfile.objects.filter(created_by=user)
    
    def get_serializer_class(self):
        """Use different serializer for creation."""
        if self.action == 'create':
            return CreateInferenceProfileSerializer
        return InferenceProfileSerializer
    
    def create(self, request, *args, **kwargs):
        """Create an inference profile."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            model = AIModel.objects.get(id=serializer.validated_data['model_id'])
            
            # Create inference profile via AWS
            client = get_bedrock_client()
            profile_data = client.create_inference_profile(
                profile_name=serializer.validated_data['profile_name'],
                model_id=model.model_id,
                regions=serializer.validated_data['regions'],
                tags=serializer.validated_data.get('tags', {})
            )
            
            # Save to database
            profile = InferenceProfile.objects.create(
                model=model,
                profile_name=serializer.validated_data['profile_name'],
                profile_arn=profile_data.get('profile_arn', ''),
                status='creating',
                regions=serializer.validated_data['regions'],
                configuration=serializer.validated_data.get('tags', {}),
                created_by=request.user
            )
            
            response_serializer = InferenceProfileSerializer(profile)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)
        except AIModel.DoesNotExist:
            return Response(
                {'error': 'Model not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.error(f"Error creating inference profile: {str(e)}")
            return Response(
                {'error': f'Failed to create inference profile: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# Simplified API endpoints
from rest_framework.views import APIView
from rest_framework.response import Response as APIResponse
from rest_framework.exceptions import PermissionDenied


class AgentChatView(APIView):
    """Simplified chat endpoint. Credits are deducted from the agent owner for each API call."""
    authentication_classes = [AgentAPIKeyAuthentication]
    permission_classes = [HasAgentAPIKey]

    def post(self, request):
        """Handle chat request."""
        agent = request.auth
        serializer = ChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return APIResponse(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        message = serializer.validated_data['message']

        # Check agent owner has sufficient credits for this API call
        try:
            from agents_app.platform_utils import has_sufficient_credits
            has_sufficient, required_credits, balance = has_sufficient_credits(agent.user, 'api_call', count=1)
            if not has_sufficient:
                return APIResponse({
                    'error': 'Insufficient credits',
                    'message': f'Agent owner needs {required_credits} credits for this API call. Balance: {balance}.',
                    'required_credits': str(required_credits),
                    'balance': str(balance),
                }, status=402)
        except ImportError:
            pass

        try:
            # Determine which client to use based on agent's model
            if agent.model:
                model_provider = agent.model.provider
                model_id = agent.model.model_id
            else:
                model_provider = agent.configuration.get('provider') if agent.configuration else None
                model_id = agent.configuration.get('model') if agent.configuration else None
            
            # Use AgentLoop for tool calling support
            # Get or create conversation
            # For testing status agents, always start fresh - create new conversation
            conversation = None
            if agent.status != 'testing':
                conversation = Conversation.objects.filter(
                    agent=agent,
                    user=agent.user,
                    status='active'
                ).order_by('-updated_at').first()
            
            # Create new conversation if none exists (or agent is testing)
            if not conversation:
                import uuid
                conversation = Conversation.objects.create(
                    agent=agent,
                    user=agent.user,
                    conversation_id=str(uuid.uuid4()),
                    status='active'
                )
            
            # Create agent loop
            agent_loop = AgentLoop(agent, conversation)
            
            # Get system prompt
            system_prompt = agent.configuration.get('instruction') or agent.configuration.get('system_prompt', '')
            
            # Execute agent loop
            result = agent_loop.execute(message, system_prompt)

            # Deduct credits for API call (agent owner is billed)
            try:
                from agents_app.platform_utils import track_usage
                track_usage(agent.user, 'api_calls', count=1, is_daily=False)
            except ImportError:
                pass

            return APIResponse({
                'response': result.get('response', ''),
                'tool_calls': result.get('tool_calls', []),
                'iterations': result.get('iterations', 1),
                'conversation_id': result.get('conversation_id', '')
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return APIResponse(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class AgentQueryView(APIView):
    """Simplified query endpoint. Credits are deducted from the agent owner for each API call."""
    authentication_classes = [AgentAPIKeyAuthentication]
    permission_classes = [HasAgentAPIKey]

    def post(self, request):
        """Handle query request."""
        agent = request.auth
        serializer = QueryRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return APIResponse(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        query = serializer.validated_data['query']

        # Check agent owner has sufficient credits for this API call
        try:
            from agents_app.platform_utils import has_sufficient_credits
            has_sufficient, required_credits, balance = has_sufficient_credits(agent.user, 'api_call', count=1)
            if not has_sufficient:
                return APIResponse({
                    'error': 'Insufficient credits',
                    'message': f'Agent owner needs {required_credits} credits for this API call. Balance: {balance}.',
                    'required_credits': str(required_credits),
                    'balance': str(balance),
                }, status=402)
        except ImportError:
            pass

        try:
            # Determine which client to use based on agent's model
            if agent.model:
                model_provider = agent.model.provider
                model_id = agent.model.model_id
            else:
                model_provider = agent.configuration.get('provider') if agent.configuration else None
                model_id = agent.configuration.get('model') if agent.configuration else None
            
            # Use AgentLoop for tool calling support
            # Get or create conversation
            # For testing status agents, always start fresh - create new conversation
            conversation = None
            if agent.status != 'testing':
                conversation = Conversation.objects.filter(
                    agent=agent,
                    user=agent.user,
                    status='active'
                ).order_by('-updated_at').first()
            
            # Create new conversation if none exists (or agent is testing)
            if not conversation:
                import uuid
                conversation = Conversation.objects.create(
                    agent=agent,
                    user=agent.user,
                    conversation_id=str(uuid.uuid4()),
                    status='active'
                )
            
            # Create agent loop
            agent_loop = AgentLoop(agent, conversation)
            
            # Get system prompt
            system_prompt = agent.configuration.get('instruction') or agent.configuration.get('system_prompt', '')
            
            # Execute agent loop
            result = agent_loop.execute(query, system_prompt)

            # Deduct credits for API call (agent owner is billed)
            try:
                from agents_app.platform_utils import track_usage
                track_usage(agent.user, 'api_calls', count=1, is_daily=False)
            except ImportError:
                pass

            return APIResponse({
                'response': result.get('response', ''),
                'tool_calls': result.get('tool_calls', []),
                'iterations': result.get('iterations', 1),
                'conversation_id': result.get('conversation_id', '')
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return APIResponse(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class AgentAgentsListView(APIView):
    """Simplified agents list endpoint."""
    authentication_classes = [AgentAPIKeyAuthentication]
    permission_classes = [HasAgentAPIKey]
    
    def get(self, request):
        """List accessible agents."""
        agent = request.auth
        serializer = AgentSerializer(agent)
        return APIResponse([serializer.data], status=status.HTTP_200_OK)


class AgentAgentDetailView(APIView):
    """Simplified agent detail endpoint."""
    authentication_classes = [AgentAPIKeyAuthentication]
    permission_classes = [HasAgentAPIKey]
    
    def get(self, request, agent_id):
        """Get agent info."""
        agent = request.auth
        if agent.id != int(agent_id):
            return APIResponse(
                {'error': 'Agent not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        serializer = AgentSerializer(agent)
        return APIResponse(serializer.data, status=status.HTTP_200_OK)


# REST API endpoints
class CreateConversationView(APIView):
    """Create a new conversation endpoint.
    
    Supports both session authentication (for UI) and agent API key authentication (for external clients).
    For session auth: agent_id must be provided in request data and user must own the agent.
    For API key auth: agent is determined from the API key.
    """
    authentication_classes = [SessionAuthentication, AgentAPIKeyAuthentication]
    permission_classes = [SessionOrAgentAPIKeyPermission]
    
    def post(self, request):
        """Create a new conversation and send initial message."""
        # Debug logging
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"CreateConversationView.post called")
        logger.info(f"request.auth: {request.auth}")
        logger.info(f"request.auth type: {type(request.auth)}")
        logger.info(f"request.user: {request.user}")
        logger.info(f"request.auth: {request.auth}")
        # Check if user is authenticated (handles both User and Agent objects)
        is_authenticated = False
        if request.user:
            # Check if it's a User object with is_authenticated attribute
            if hasattr(request.user, 'is_authenticated'):
                is_authenticated = request.user.is_authenticated
            # If it's an Agent object, authentication is via API key (check request.auth)
            elif isinstance(request.user, Agent):
                is_authenticated = False  # Agent auth uses request.auth, not request.user
        logger.info(f"is_authenticated: {is_authenticated}")
        logger.info(f"Authorization header: {request.META.get('HTTP_AUTHORIZATION', 'NOT FOUND')}")
        
        serializer = CreateConversationSerializer(data=request.data)
        
        if not serializer.is_valid():
            return APIResponse(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        message = serializer.validated_data['message']
        
        # Determine agent based on authentication method
        agent = None
        
        # If authenticated via API key, agent is in request.auth
        if hasattr(request, 'auth') and isinstance(request.auth, Agent):
            agent = request.auth
            logger.info(f"Using agent from API key authentication: {agent.id}")
        
        # If authenticated via session, get agent from agent_id in request data
        elif request.user and hasattr(request.user, 'is_authenticated') and request.user.is_authenticated:
            agent_id = serializer.validated_data.get('agent_id')
            if not agent_id:
                return APIResponse(
                    {'error': 'agent_id is required when using session authentication'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            try:
                agent = Agent.objects.get(id=agent_id, user=request.user, status='published')
                logger.info(f"Using agent from session authentication: {agent.id}")
            except Agent.DoesNotExist:
                return APIResponse(
                    {'error': 'Agent not found or access denied. Agent must be published and owned by the authenticated user.'},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            # Provide more helpful error message
            auth_header = request.META.get('HTTP_AUTHORIZATION', '')
            has_api_key = bool(auth_header.startswith('ApiKey ') or request.META.get('HTTP_X_API_KEY'))
            
            if has_api_key:
                error_msg = (
                    'Unable to continue or create conversation. The API key may be invalid or expired. '
                    'Please check your API configuration. Possible reasons: '
                    '1) API key was regenerated, 2) Agent is not published, 3) API key is incorrect.'
                )
            else:
                error_msg = 'Authentication required. Use either session authentication or agent API key.'
            
            return APIResponse(
                {'error': error_msg},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        try:
            # Generate request ID and conversation ID for async processing
            import uuid
            request_id = str(uuid.uuid4())
            conversation_id = str(uuid.uuid4())
            
            # Determine user for conversation
            # For API key auth: use agent.user
            # For session auth: use request.user
            if hasattr(request, 'auth') and isinstance(request.auth, Agent):
                conversation_user = agent.user
            else:
                conversation_user = request.user
            
            # Create conversation synchronously so we can return conversation_id immediately
            from .models import AgentRequest, Conversation
            from .tasks import process_conversation_request
            
            conversation = Conversation.objects.create(
                agent=agent,
                user=conversation_user,
                conversation_id=conversation_id,
                status='active'
            )
            
            # Create request record
            request_obj = AgentRequest.objects.create(
                request_id=request_id,
                agent=agent,
                conversation=conversation,
                status='pending',
                message=message
            )
            
            # Queue async task via Celery
            task = process_conversation_request.delay(
                request_id=request_id,
                agent_id=agent.id,
                conversation_id=conversation_id,  # Pass the conversation_id we created
                message=message
            )
            
            # Update request with celery task ID
            request_obj.celery_task_id = task.id
            request_obj.save(update_fields=['celery_task_id'])
            
            logger.info(f"Queued conversation request {request_id} (task: {task.id}) for conversation {conversation_id}")
            
            # Return request_id and conversation_id immediately (async pattern)
            return APIResponse({
                'request_id': request_id,
                'conversation_id': conversation_id,
                'status': 'pending',
                'message': 'Request queued for processing. Use /api/get_answer with request_id to get results. Use conversation_id to continue the conversation.'
            }, status=status.HTTP_202_ACCEPTED)
        except Exception as e:
            logger.error(f"Error creating conversation: {str(e)}", exc_info=True)
            return APIResponse(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class AgentWebhookView(APIView):
    """Machine-to-machine webhook: authenticate with the agent API token only.

    **Authentication (required)**  
    - ``Authorization: ApiKey <your_agent_api_key>`` or header ``X-API-Key: <key>``

    **Authorization**  
    - Token must match a **published** agent (same rules as other agent API endpoints).

    **Semantics**  
    - Every successful POST starts a **new** conversation for that agent (no continuation
      of prior webhook calls). Use :class:`ContinueConversationView` if you need follow-up
      turns using ``conversation_id``.

    **Body (JSON)**  
    - ``message`` or ``text``: optional human-readable instruction (either key).  
    - ``payload``: optional JSON object; appended to the agent message under a ``[Webhook payload]`` section so tools and RAG see structured caller data.  
    - ``metadata``: optional JSON object; **not** sent to the model—it is echoed back in the response for your correlation (ids, trace ids).

    Processing is asynchronous (Celery): poll ``GET /api/get_answer`` with ``request_id``.
    """

    authentication_classes = [AgentAPIKeyAuthentication]
    permission_classes = [HasAgentAPIKey]

    def post(self, request):
        serializer = AgentWebhookSerializer(data=request.data)
        if not serializer.is_valid():
            return APIResponse(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        agent = request.auth
        composed_message = serializer.composed_message()
        metadata = serializer.validated_data.get('metadata') or {}

        import uuid
        from .models import AgentRequest, Conversation
        from .tasks import process_conversation_request

        request_id = str(uuid.uuid4())
        conversation_id = str(uuid.uuid4())

        conversation_user = agent.user

        conversation = Conversation.objects.create(
            agent=agent,
            user=conversation_user,
            conversation_id=conversation_id,
            status='active',
        )

        request_obj = AgentRequest.objects.create(
            request_id=request_id,
            agent=agent,
            conversation=conversation,
            status='pending',
            message=composed_message,
        )

        task = process_conversation_request.delay(
            request_id=request_id,
            agent_id=agent.id,
            conversation_id=conversation_id,
            message=composed_message,
        )

        request_obj.celery_task_id = task.id
        request_obj.save(update_fields=['celery_task_id'])

        logger.info(
            'Webhook accepted for agent %s: request_id=%s conversation_id=%s',
            agent.id,
            request_id,
            conversation_id,
        )

        return APIResponse(
            {
                'request_id': request_id,
                'conversation_id': conversation_id,
                'agent_id': agent.id,
                'status': 'pending',
                'metadata': metadata,
                'message': 'Queued. Poll GET /api/get_answer with request_id for the result.',
            },
            status=status.HTTP_202_ACCEPTED,
        )


class ContinueConversationView(APIView):
    """Continue an existing conversation endpoint."""
    authentication_classes = [AgentAPIKeyAuthentication]
    permission_classes = [HasAgentAPIKey]
    
    def post(self, request):
        """Continue a conversation with a new message."""
        agent = request.auth
        serializer = ContinueConversationSerializer(data=request.data)
        
        if not serializer.is_valid():
            return APIResponse(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        message = serializer.validated_data['message']
        conversation_id = serializer.validated_data['conversation_id']
        
        try:
            # Get conversation
            conversation = Conversation.objects.get(
                conversation_id=conversation_id,
                agent=agent,
                status='active'
            )
            
            # Generate request ID for async processing
            import uuid
            request_id = str(uuid.uuid4())
            
            # Create request record
            from .models import AgentRequest
            from .tasks import process_conversation_request
            
            request_obj = AgentRequest.objects.create(
                request_id=request_id,
                agent=agent,
                conversation=conversation,
                status='pending',
                message=message
            )
            
            # Queue async task via Celery
            task = process_conversation_request.delay(
                request_id=request_id,
                agent_id=agent.id,
                conversation_id=conversation_id,
                message=message
            )
            
            # Update request with celery task ID
            request_obj.celery_task_id = task.id
            request_obj.save(update_fields=['celery_task_id'])
            
            logger.info(f"Queued continue conversation request {request_id} (task: {task.id})")
            
            # Return request_id immediately (async pattern)
            return APIResponse({
                'request_id': request_id,
                'conversation_id': conversation_id,
                'status': 'pending',
                'message': 'Request queued for processing. Use /api/get_answer with request_id to get results.'
            }, status=status.HTTP_202_ACCEPTED)
        except Conversation.DoesNotExist:
            return APIResponse(
                {'error': 'Conversation not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return APIResponse(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class GetAnswerView(APIView):
    """Get answer from a conversation (REST API endpoint)."""
    authentication_classes = [AgentAPIKeyAuthentication]
    permission_classes = [HasAgentAPIKey]
    
    def get(self, request):
        """Get answer by request_id (REST API).
        
        Supports both request_id (for async requests) and conversation_id (legacy).
        """
        try:
            # Check if authentication and permission checks passed
            if not hasattr(request, 'auth') or request.auth is None:
                return APIResponse(
                    {'error': 'Authentication required. Invalid or missing API key.'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Verify request.auth is an Agent instance
            if not isinstance(request.auth, Agent):
                return APIResponse(
                    {'error': 'Invalid authentication. Agent API key required.'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Verify agent is published (permission check)
            agent = request.auth
            if agent.status != 'published':
                return APIResponse(
                    {'error': 'Access denied. Agent is not published.'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            request_id = request.query_params.get('request_id')
            conversation_id = request.query_params.get('conversation_id')
            
            # Priority: request_id (new async API) > conversation_id (legacy)
            if request_id:
                # Check request status (async processing)
                from .models import AgentRequest
                
                try:
                    request_obj = AgentRequest.objects.get(
                        request_id=request_id,
                        agent=agent
                    )
                    
                    if request_obj.status == 'pending':
                        return APIResponse({
                            'request_id': request_id,
                            'status': 'pending',
                            'message': 'Request is still being processed. Please poll again.'
                        }, status=status.HTTP_202_ACCEPTED)
                    
                    elif request_obj.status == 'processing':
                        return APIResponse({
                            'request_id': request_id,
                            'status': 'processing',
                            'message': 'Request is currently being processed. Please poll again.'
                        }, status=status.HTTP_202_ACCEPTED)
                    
                    elif request_obj.status == 'completed':
                        return APIResponse({
                            'request_id': request_id,
                            'conversation_id': request_obj.conversation.conversation_id if request_obj.conversation else None,
                            'status': 'completed',
                            'answer': request_obj.response or '',
                            'tool_calls': request_obj.tool_calls or [],
                            'iterations': request_obj.iterations or 0,
                            'created_at': request_obj.completed_at.isoformat() if request_obj.completed_at else None
                        }, status=status.HTTP_200_OK)
                    
                    elif request_obj.status == 'failed':
                        return APIResponse({
                            'request_id': request_id,
                            'status': 'failed',
                            'error': request_obj.error_message or 'Request processing failed'
                        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                    
                    else:
                        # Unknown status
                        return APIResponse({
                            'request_id': request_id,
                            'status': request_obj.status,
                            'message': f'Request has unknown status: {request_obj.status}'
                        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                    
                except AgentRequest.DoesNotExist:
                    return APIResponse(
                        {'error': f'Request {request_id} not found'},
                        status=status.HTTP_404_NOT_FOUND
                    )
            
            # Legacy: conversation_id support (for backward compatibility)
            elif conversation_id:
                try:
                    conversation = Conversation.objects.get(
                        conversation_id=conversation_id,
                        agent=agent
                    )
                    
                    # Get latest agent message
                    latest_message = ConversationMessage.objects.filter(
                        conversation=conversation,
                        message_type='agent'
                    ).order_by('-created_at').first()
                    
                    if latest_message:
                        return APIResponse({
                            'conversation_id': conversation_id,
                            'answer': latest_message.content,
                            'created_at': latest_message.created_at.isoformat()
                        }, status=status.HTTP_200_OK)
                    else:
                        return APIResponse(
                            {'error': 'No answer found for this conversation'},
                            status=status.HTTP_404_NOT_FOUND
                        )
                except Conversation.DoesNotExist:
                    return APIResponse(
                        {'error': 'Conversation not found'},
                        status=status.HTTP_404_NOT_FOUND
                    )
            else:
                return APIResponse(
                    {'error': 'Either request_id or conversation_id parameter is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except PermissionDenied as e:
            logger.warning(f"Permission denied in GetAnswerView: {str(e)}")
            return APIResponse(
                {'error': 'Access denied. Invalid API key or insufficient permissions.'},
                status=status.HTTP_403_FORBIDDEN
            )
        except Exception as e:
            logger.error(f"Error in GetAnswerView: {str(e)}", exc_info=True)
            return APIResponse(
                {'error': f'Internal server error: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class FindConversationView(APIView):
    """Find conversation details (REST API endpoint)."""
    authentication_classes = [AgentAPIKeyAuthentication]
    permission_classes = [HasAgentAPIKey]
    
    def post(self, request):
        """Find and return conversation details."""
        agent = request.auth
        serializer = FindConversationSerializer(data=request.data)
        
        if not serializer.is_valid():
            return APIResponse(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        conversation_id = serializer.validated_data['conversation_id']
        
        try:
            conversation = Conversation.objects.get(
                conversation_id=conversation_id,
                agent=agent
            )
            
            # Get messages (excluding tool calls and tool results)
            messages = ConversationMessage.objects.filter(
                conversation=conversation
            ).exclude(
                message_type__in=['tool_call', 'tool_result']
            ).order_by('created_at')
            
            messages_data = []
            for msg in messages:
                messages_data.append({
                    'type': msg.message_type,
                    'content': msg.content,
                    'created_at': msg.created_at.isoformat(),
                    'tool_name': msg.tool_name if msg.tool_name else None
                })
            
            return APIResponse({
                'conversation_id': conversation_id,
                'status': conversation.status,
                'created_at': conversation.created_at.isoformat(),
                'updated_at': conversation.updated_at.isoformat(),
                'messages': messages_data,
                'message_count': len(messages_data)
            }, status=status.HTTP_200_OK)
        except Conversation.DoesNotExist:
            return APIResponse(
                {'error': 'Conversation not found'},
                status=status.HTTP_404_NOT_FOUND
            )


class UploadFileView(APIView):
    """Upload file for conversation (REST API endpoint)."""
    authentication_classes = [AgentAPIKeyAuthentication]
    permission_classes = [HasAgentAPIKey]
    
    def put(self, request):
        """Upload a file."""
        agent = request.auth
        
        if 'file' not in request.FILES:
            return APIResponse(
                {'error': 'No file provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        file = request.FILES['file']
        conversation_id = request.data.get('conversation_id')
        
        # Validate file size (max 100MB)
        max_size = 100 * 1024 * 1024  # 100MB
        if file.size > max_size:
            return APIResponse(
                {'error': 'File size exceeds 100MB limit'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            from django.core.files.storage import default_storage
            from django.core.files.base import ContentFile
            import uuid
            import os
            
            # Generate unique file ID
            file_id = str(uuid.uuid4())
            file_ext = os.path.splitext(file.name)[1]
            unique_filename = f"{file_id}{file_ext}"
            file_path = f"conversation_files/{agent.id}/{unique_filename}"
            
            # Save file
            saved_path = default_storage.save(file_path, ContentFile(file.read()))
            file_url = default_storage.url(saved_path)
            
            # Create file record
            conversation_file = ConversationFile.objects.create(
                agent=agent,
                file_name=file.name,
                file_path=saved_path,
                file_type=file.content_type or '',
                file_size=file.size,
                file_id=file_id,
                download_url=file_url
            )
            
            # Link to conversation if provided
            if conversation_id:
                try:
                    conversation = Conversation.objects.get(
                        conversation_id=conversation_id,
                        agent=agent
                    )
                    conversation_file.conversation = conversation
                    conversation_file.save()
                except Conversation.DoesNotExist:
                    pass
            
            return APIResponse({
                'file_id': file_id,
                'file_name': file.name,
                'file_size': file.size,
                'file_type': file.content_type,
                'download_url': file_url,
                'uploaded_at': conversation_file.uploaded_at.isoformat()
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return APIResponse(
                {'error': f'Failed to upload file: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DownloadFileView(APIView):
    """Download file (REST API endpoint)."""
    authentication_classes = [AgentAPIKeyAuthentication]
    permission_classes = [HasAgentAPIKey]
    
    def get(self, request):
        """Get file download URL."""
        agent = request.auth
        file_id = request.query_params.get('file_id')
        
        if not file_id:
            return APIResponse(
                {'error': 'file_id parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            conversation_file = ConversationFile.objects.get(
                file_id=file_id,
                agent=agent
            )
            
            from django.core.files.storage import default_storage
            download_url = default_storage.url(conversation_file.file_path)
            
            return APIResponse({
                'file_id': file_id,
                'file_name': conversation_file.file_name,
                'download_url': download_url,
                'file_size': conversation_file.file_size,
                'file_type': conversation_file.file_type
            }, status=status.HTTP_200_OK)
        except ConversationFile.DoesNotExist:
            return APIResponse(
                {'error': 'File not found'},
                status=status.HTTP_404_NOT_FOUND
            )


@login_required
def serve_media_file(request, file_path):
    """Serve media files with authentication."""
    from django.core.files.storage import default_storage
    import os
    logger = logging.getLogger(__name__)
    
    from django.http import Http404
    
    # Security: Only allow files from conversation_files directory
    if not file_path.startswith('conversation_files/'):
        raise Http404("File not found")
    
    # Check if file exists
    if not default_storage.exists(file_path):
        raise Http404("File not found")
    
    # Check if user has access to the file (via conversation or agent)
    try:
        # Extract agent ID from path: conversation_files/{agent_id}/...
        path_parts = file_path.split('/')
        if len(path_parts) >= 3:
            agent_id = path_parts[1]
            from .models import Agent, ConversationFile
            agent = Agent.objects.get(id=agent_id)
            
            # Check if user owns the agent or has access via share
            from .views_chat import user_has_agent_access
            if not user_has_agent_access(request.user, agent):
                # Check if file is linked to a conversation the user has access to
                conversation_file = ConversationFile.objects.filter(
                    file_path__endswith=os.path.basename(file_path),
                    agent=agent
                ).first()
                
                if conversation_file and conversation_file.conversation:
                    if conversation_file.conversation.user != request.user:
                        raise Http404("File not found")
                else:
                    raise Http404("File not found")
        else:
            raise Http404("File not found")
    except (Agent.DoesNotExist, ValueError):
        raise Http404("File not found")
    
    # Serve the file
    try:
        file_obj = default_storage.open(file_path, 'rb')
        from django.http import FileResponse
        
        # Determine content type and whether to download or display
        file_name = os.path.basename(file_path)
        file_ext = os.path.splitext(file_name)[1].lower()
        
        # Content type mapping
        content_types = {
            '.pdf': 'application/pdf',
            '.txt': 'text/plain',
            '.csv': 'text/csv',
            '.json': 'application/json',
            '.xml': 'application/xml',
            '.zip': 'application/zip',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xls': 'application/vnd.ms-excel',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }
        
        content_type = content_types.get(file_ext, 'application/octet-stream')
        
        # For PDFs and other documents, force download; for images, display inline
        is_image = file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg']
        disposition = 'inline' if is_image else 'attachment'
        
        response = FileResponse(file_obj, content_type=content_type)
        response['Content-Disposition'] = f'{disposition}; filename="{file_name}"'
        return response
    except Exception as e:
        logger.error(f"Error serving media file {file_path}: {str(e)}", exc_info=True)
        raise Http404("File not found")


class CreateUploadURLView(APIView):
    """Create a private user file upload URL (REST API endpoint)."""
    authentication_classes = [AgentAPIKeyAuthentication]
    permission_classes = [HasAgentAPIKey]
    
    def post(self, request):
        """Create a temporary upload URL."""
        agent = request.auth
        serializer = FileUploadSerializer(data=request.data)
        
        if not serializer.is_valid():
            return APIResponse(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        file_name = serializer.validated_data.get('file_name', 'file')
        file_type = serializer.validated_data.get('file_type', '')
        
        try:
            import uuid
            file_id = str(uuid.uuid4())
            
            # Create file record with upload URL
            conversation_file = ConversationFile.objects.create(
                agent=agent,
                file_name=file_name,
                file_type=file_type,
                file_id=file_id,
                upload_url=f"/api/upload_file?file_id={file_id}"
            )
            
            return APIResponse({
                'file_id': file_id,
                'upload_url': conversation_file.upload_url,
                'expires_at': None  # URLs don't expire in this implementation
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return APIResponse(
                {'error': f'Failed to create upload URL: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class GetDownloadURLView(APIView):
    """Get download file URL (REST API endpoint)."""
    authentication_classes = [AgentAPIKeyAuthentication]
    permission_classes = [HasAgentAPIKey]
    
    def post(self, request):
        """Get download URL for a file."""
        agent = request.auth
        file_id = request.data.get('file_id')
        
        if not file_id:
            return APIResponse(
                {'error': 'file_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            conversation_file = ConversationFile.objects.get(
                file_id=file_id,
                agent=agent
            )
            
            from django.core.files.storage import default_storage
            if conversation_file.file_path:
                download_url = default_storage.url(conversation_file.file_path)
            else:
                download_url = conversation_file.download_url
            
            return APIResponse({
                'file_id': file_id,
                'file_name': conversation_file.file_name,
                'download_url': download_url,
                'expires_at': None  # URLs don't expire in this implementation
            }, status=status.HTTP_200_OK)
        except ConversationFile.DoesNotExist:
            return APIResponse(
                {'error': 'File not found'},
                status=status.HTTP_404_NOT_FOUND
            )


def handler404(request, exception):
    """Custom 404 handler that redirects to home page."""
    from django.shortcuts import redirect
    path = request.path
    if path.startswith('/static/') or path.startswith('/media/'):
        raise Http404()
    return redirect('home')
