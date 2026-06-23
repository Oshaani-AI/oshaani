"""Dashboard views for agents_app."""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from .models import (
    Agent,
    AgentAPIKey,
    TrainingData,
    TestResult,
    AIModel,
    AgentFeedback,
    UserProfile,
    AgentShare,
    AgentPublicShare,
    Conversation,
    ConversationMessage,
    AgentLike,
    AgentRequest,
)
# Store exception class at module level to avoid scoping issues
TrainingDataDoesNotExist = TrainingData.DoesNotExist
from django.utils import timezone
import os
import uuid
import logging
logger = logging.getLogger(__name__)
from .feedback_optimizer import FeedbackOptimizer
from .serializers import AgentSerializer, TrainingDataSerializer, TestResultSerializer, AgentWebhookSerializer
from .aws_integration import get_bedrock_client
from .tasks import sync_available_models
import json
from django.conf import settings
from django.urls import reverse


def _write_debug_log(data):
    """Safely write debug log to the logs directory."""
    try:
        from pathlib import Path
        logs_dir = Path(settings.BASE_DIR) / 'logs'
        logs_dir.mkdir(exist_ok=True)
        debug_log_path = logs_dir / 'debug.log'
        with open(debug_log_path, 'a') as f:
            f.write(json.dumps(data) + '\n')
    except Exception:
        # Silently fail if logging fails - don't break the application
        pass


def _redirect_after_mcp(agent, request):
    """Return to agent test page or agent detail after MCP CRUD."""
    target = (request.POST.get('next_page') or request.GET.get('next_page') or '').strip().lower()
    if target == 'test':
        return redirect('agent_test', slug=agent.slug)
    return redirect('agent_detail', slug=agent.slug)


def get_mcp_servers_info_for_agent(agent):
    """Load MCP servers with connection status and tools list (dashboard UI)."""
    mcp_servers_info = []
    try:
        from .models import MCPServer
        from .mcp_tools import MCPToolManager

        mcp_servers = MCPServer.objects.filter(agent=agent).order_by('name')

        if not mcp_servers.exists():
            return mcp_servers_info

        mcp_manager = MCPToolManager(agent)
        mcp_manager.connect_all()

        for mcp_server in mcp_servers:
            server_info = {
                'id': mcp_server.id,
                'name': mcp_server.name,
                'description': mcp_server.description,
                'transport_type': mcp_server.transport_type,
                'transport_type_display': mcp_server.get_transport_type_display(),
                'command': mcp_server.command,
                'args': mcp_server.args,
                'url': mcp_server.url,
                'headers': mcp_server.headers,
                'is_active': mcp_server.is_active,
                'auto_connect': mcp_server.auto_connect,
                'is_connected': False,
                'tools': [],
            }

            connection_error = None
            if mcp_server.is_active:
                client = mcp_manager.client_manager.get_client(mcp_server.name)
                if client and client.connected:
                    server_info['is_connected'] = True
                    try:
                        tools = client.list_tools()
                        server_info['tools'] = [
                            {
                                'name': tool.get('name', ''),
                                'description': tool.get('description', ''),
                                'input_schema': tool.get('inputSchema', {}),
                            }
                            for tool in tools
                        ]
                    except Exception as e:
                        logger = logging.getLogger(__name__)
                        logger.warning(
                            'Error listing tools from MCP server %s: %s',
                            mcp_server.name,
                            str(e),
                        )
                        connection_error = f'Error listing tools: {str(e)}'
                else:
                    if mcp_server.auto_connect:
                        try:
                            from .mcp_client import MCPClient

                            new_client = MCPClient(
                                server_name=mcp_server.name,
                                transport_type=mcp_server.transport_type,
                                command=mcp_server.command if mcp_server.command else None,
                                args=mcp_server.args if mcp_server.args else [],
                                url=mcp_server.url if mcp_server.url else None,
                                headers=mcp_server.headers if mcp_server.headers else {},
                            )
                            if new_client.connect():
                                server_info['is_connected'] = True
                                mcp_manager.client_manager.add_client(mcp_server.name, new_client)
                                try:
                                    tools = new_client.list_tools()
                                    server_info['tools'] = [
                                        {
                                            'name': tool.get('name', ''),
                                            'description': tool.get('description', ''),
                                            'input_schema': tool.get('inputSchema', {}),
                                        }
                                        for tool in tools
                                    ]
                                except Exception as e:
                                    logger = logging.getLogger(__name__)
                                    logger.warning(
                                        'Error listing tools from MCP server %s: %s',
                                        mcp_server.name,
                                        str(e),
                                    )
                                    connection_error = f'Error listing tools: {str(e)}'
                            else:
                                connection_error = (
                                    'Failed to connect to MCP server. Check command and configuration.'
                                )
                        except Exception as e:
                            logger = logging.getLogger(__name__)
                            logger.warning(
                                'Error connecting to MCP server %s: %s',
                                mcp_server.name,
                                str(e),
                            )
                            connection_error = f'Connection error: {str(e)}'
                    else:
                        connection_error = (
                            'Auto-connect is disabled. Enable it to connect automatically.'
                        )

            server_info['connection_error'] = connection_error
            mcp_servers_info.append(server_info)

        mcp_manager.disconnect_all()
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f'Error loading MCP servers: {str(e)}', exc_info=True)

    return mcp_servers_info


@login_required
def agents_list(request):
    """Dashboard home - list all user's agents."""
    # Check if there's a pending agent share token in session (from login redirect)
    share_token = request.session.get('agent_share_token')
    if share_token:
        # Clear the token from session
        del request.session['agent_share_token']
        request.session.save()
        # Redirect to accept the share
        from django.urls import reverse
        return redirect(reverse('accept_agent_share', kwargs={'token': share_token}))
    
    # Get user's own agents with prefetched model information
    agents = Agent.objects.filter(user=request.user).select_related('model')
    
    # Get agents shared with user (accepted shares) with prefetched model information
    shared_agents = Agent.objects.filter(
        shares__email=request.user.email,
        shares__is_accepted=True,
        shares__accepted_by=request.user
    ).exclude(id__in=agents.values_list('id', flat=True)).distinct().select_related('model')
    
    # Get filter parameters
    status_filter = request.GET.get('status')
    search_query = request.GET.get('search', '').strip()
    sort_by = request.GET.get('sort', '-created_at')  # Default: newest first
    
    # Apply search filter
    if search_query:
        from django.db.models import Q
        search_q = Q(name__icontains=search_query) | Q(description__icontains=search_query)
        agents = agents.filter(search_q)
        shared_agents = shared_agents.filter(search_q)
    
    # Apply status filter
    if status_filter:
        agents = agents.filter(status=status_filter)
        shared_agents = shared_agents.filter(status=status_filter)
    
    # Apply sorting
    valid_sort_fields = ['name', '-name', 'created_at', '-created_at', 'updated_at', '-updated_at', 'status', '-status']
    if sort_by in valid_sort_fields:
        agents = agents.order_by(sort_by)
        shared_agents = shared_agents.order_by(sort_by)
    else:
        agents = agents.order_by('-created_at')
        shared_agents = shared_agents.order_by('-created_at')
    
    # Get shared agent usage statistics for user's own agents
    from .models import SharedAgentUsage
    from django.db.models import Sum, Count, Max
    from datetime import timedelta
    
    now = timezone.now()
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_30_days = now - timedelta(days=30)
    
    # Get usage stats for user's agents (as owner)
    user_agent_ids = list(agents.values_list('id', flat=True))
    owner_usage_stats = {}
    
    if user_agent_ids:
        # Per-agent usage stats - attach to agent objects
        for agent in agents:
            # Get shared usage (usage by other users)
            agent_usage = SharedAgentUsage.objects.filter(
                agent=agent,
                shared_by=request.user,
                period_start__gte=last_30_days
            )
            
            shared_messages = agent_usage.aggregate(Sum('message_count'))['message_count__sum'] or 0
            shared_conversations = agent_usage.aggregate(Sum('conversation_count'))['conversation_count__sum'] or 0
            unique_users = agent_usage.values('used_by').distinct().count()
            
            # Get private usage (owner's own usage)
            owner_conversations = Conversation.objects.filter(
                agent=agent,
                user=request.user,
                created_at__gte=last_30_days
            )
            
            owner_messages = ConversationMessage.objects.filter(
                conversation__agent=agent,
                conversation__user=request.user,
                created_at__gte=last_30_days
            ).exclude(
                message_type__in=['tool_call', 'tool_result']
            ).count()
            
            owner_conversation_count = owner_conversations.count()
            
            agent.usage_stats = {
                'shared_messages': shared_messages,
                'shared_conversations': shared_conversations,
                'private_messages': owner_messages,
                'private_conversations': owner_conversation_count,
                'total_messages': shared_messages + owner_messages,
                'total_conversations': shared_conversations + owner_conversation_count,
                'unique_users': unique_users,
            }
            owner_usage_stats[agent.id] = agent.usage_stats
            
            # Determine visibility status: private, shared, or public
            has_public_share = AgentPublicShare.objects.filter(
                agent=agent,
                is_active=True
            ).exists()
            
            has_email_shares = AgentShare.objects.filter(
                agent=agent,
                shared_by=request.user
            ).exists()
            
            if has_public_share:
                agent.visibility_status = 'public'
            elif has_email_shares:
                agent.visibility_status = 'shared'
            else:
                agent.visibility_status = 'private'
        
        # Per-user statistics across all agents
        per_user_stats = SharedAgentUsage.objects.filter(
            agent_id__in=user_agent_ids,
            shared_by=request.user,
            period_start__gte=last_30_days
        ).values(
            'used_by__id',
            'used_by__username',
            'used_by__email'
        ).annotate(
            total_messages=Sum('message_count'),
            total_conversations=Sum('conversation_count'),
            agents_used=Count('agent', distinct=True),
            last_used=Max('last_used_at')
        ).order_by('-total_messages')
    else:
        per_user_stats = []
    
    # Get usage stats for shared agents (as user) - attach to agent objects
    shared_agent_ids = list(shared_agents.values_list('id', flat=True))
    my_shared_usage_stats = {}
    
    if shared_agent_ids:
        for agent in shared_agents:
            agent_usage = SharedAgentUsage.objects.filter(
                agent=agent,
                used_by=request.user,
                period_start__gte=last_30_days
            )
            
            agent.my_usage_stats = {
                'total_messages': agent_usage.aggregate(Sum('message_count'))['message_count__sum'] or 0,
                'total_conversations': agent_usage.aggregate(Sum('conversation_count'))['conversation_count__sum'] or 0,
            }
            my_shared_usage_stats[agent.id] = agent.my_usage_stats
            
            # Determine visibility status for shared agents (from owner's perspective)
            has_public_share = AgentPublicShare.objects.filter(
                agent=agent,
                is_active=True
            ).exists()
            
            has_email_shares = AgentShare.objects.filter(
                agent=agent
            ).exists()
            
            if has_public_share:
                agent.visibility_status = 'public'
            elif has_email_shares:
                agent.visibility_status = 'shared'
            else:
                agent.visibility_status = 'private'
    
    context = {
        'agents': agents,
        'shared_agents': shared_agents,
        'status_filter': status_filter,
        'search_query': search_query,
        'sort_by': sort_by,
        'owner_usage_stats': owner_usage_stats,
        'my_shared_usage_stats': my_shared_usage_stats,
        'per_user_stats': list(per_user_stats),
        'has_no_agents': agents.count() == 0 and shared_agents.count() == 0,
    }
    return render(request, 'dashboard/agents_list.html', context)


@login_required
def create_first_agent(request):
    """Create a default agent similar to Oshaani Agent Creator with MCP server configured."""
    # Check if user already has agents
    if Agent.objects.filter(user=request.user).exists():
        messages.warning(request, 'You already have agents. Use "Create Agent" to add more.')
        return redirect('agents_list')
    
    try:
        # Get or create user profile
        user_profile, created = UserProfile.objects.get_or_create(user=request.user)
        
        # Generate user API key if they don't have one
        # Note: Plaintext keys are not stored, so we generate a new one for MCP server configuration
        from .models import UserAPIKey
        user_api_key_obj = UserAPIKey.objects.filter(
            user=request.user,
            is_active=True
        ).first()
        
        if not user_api_key_obj:
            # Create a new UserAPIKey for MCP server
            user_api_key_obj = UserAPIKey.objects.create(user=request.user, name="Default")
        
        # Generate API key (returns plaintext, but only stored as hash)
        user_api_key = user_api_key_obj.generate_api_key()
        messages.info(request, 'A user API key has been generated for MCP server authentication.')
        
        # Get the default model (GPT OSS Safeguard 20B or first available model)
        default_model = AIModel.objects.filter(
            model_name__icontains='GPT OSS Safeguard 20B',
            is_available=True
        ).first()
        
        if not default_model:
            # Fallback to first available model
            default_model = AIModel.objects.filter(is_available=True).first()
        
        if not default_model:
            messages.error(request, 'No available AI models found. Please contact support.')
            return redirect('agents_list')
        
        # Create the agent similar to Oshaani Agent Creator
        agent = Agent.objects.create(
            name="My First Agent",
            description="An AI agent created to help you get started with Oshaani. This agent can help you create more agents and explore the platform.",
            agent_type="chat_agent",
            user=request.user,
            model=default_model,
            status="draft",
            configuration={
                'instruction': 'Help user in creating bots and exploring the Oshaani platform. Be helpful, friendly, and guide users through the platform features. Avoid creating bots for malicious activity and crimes. Take care of security as well when creating bots.',
                'system_prompt': 'Help user in creating bots and exploring the Oshaani platform. Be helpful, friendly, and guide users through the platform features. Avoid creating bots for malicious activity and crimes. Take care of security as well when creating bots.',
                'model_id': default_model.model_id,
                'model_name': default_model.model_name,
                'model_provider': default_model.provider,
                'send_introduction': True,
                'use_rag': True,
            }
        )
        
        # Create MCP server configuration
        from .models import MCPServer
        from django.conf import settings
        
        # Get MCP server URL from settings or use default
        # Note: The MCP server endpoint is /mcp, but the URL should be the base URL (without /mcp)
        # The MCP client will append /mcp automatically
        mcp_server_url = getattr(settings, 'MCP_SERVER_URL', 'http://localhost:8080')
        
        # Create MCP server with user API key
        mcp_server = MCPServer.objects.create(
            agent=agent,
            name="Oshaani AI",
            description="Oshaani AI MCP server for agent creation and management",
            transport_type="http",
            url=mcp_server_url,
            headers={
                'Authorization': f'ApiKey {user_api_key}'
            },
            is_active=True,
            auto_connect=True,
        )
        
        # Add training data from sop.html
        try:
            import re
            
            # Read sop.html file
            sop_template_path = 'agents_app/templates/system_sop.html'
            sop_file_path = os.path.join(settings.BASE_DIR, 'agents_app', 'templates', 'system_sop.html')
            
            if os.path.exists(sop_file_path):
                with open(sop_file_path, 'r', encoding='utf-8') as f:
                    sop_html = f.read()
                
                # Extract text content from HTML (simple regex-based extraction)
                # Remove script and style tags
                sop_html = re.sub(r'<script[^>]*>.*?</script>', '', sop_html, flags=re.DOTALL | re.IGNORECASE)
                sop_html = re.sub(r'<style[^>]*>.*?</style>', '', sop_html, flags=re.DOTALL | re.IGNORECASE)
                
                # Extract text from HTML tags
                sop_text = re.sub(r'<[^>]+>', '\n', sop_html)
                # Clean up whitespace
                sop_text = re.sub(r'\n\s*\n+', '\n\n', sop_text)
                sop_text = sop_text.strip()
                
                # Limit text length to reasonable size (e.g., 500KB)
                MAX_TRAINING_TEXT_SIZE = 500 * 1024  # 500KB
                if len(sop_text) > MAX_TRAINING_TEXT_SIZE:
                    sop_text = sop_text[:MAX_TRAINING_TEXT_SIZE] + "\n\n[Content truncated...]"
                
                # Create training data entry
                training_data = TrainingData.objects.create(
                    agent=agent,
                    data_type='text',
                    content={
                        'text': sop_text,
                        'source': 'system_sop.html',
                        'title': 'Oshaani Platform Standard Operating Procedures'
                    }
                )
                
                # Update training data count
                agent.training_data_count += 1
                agent.save(update_fields=['training_data_count'])
                
                # Index training data for RAG (async via Celery)
                try:
                    from .tasks import index_training_data_for_rag
                    task_result = index_training_data_for_rag.delay(agent.id, training_data.id)
                    logger.info(f"Triggered RAG indexing for training data {training_data.id} of agent {agent.id} (Celery task ID: {task_result.id})")
                except Exception as e:
                    logger.warning(f"Failed to trigger RAG indexing: {str(e)}")
                    # Don't fail if indexing fails
                
                logger.info(f"Added SOP training data to agent {agent.id}")
        except Exception as e:
            logger.error(f"Error adding SOP training data: {str(e)}", exc_info=True)
            # Don't fail the agent creation if training data addition fails
            messages.warning(request, f'Agent created but failed to add training data: {str(e)}')
        
        # Change status to testing (required before publishing)
        agent.status = 'testing'
        agent.save(update_fields=['status'])
        
        # Publish the agent
        try:
            agent.publish()
            logger.info(f"Published agent {agent.id} ({agent.name})")
            messages.success(request, f'Your first agent "{agent.name}" has been created, trained with SOP data, and published successfully!')
        except Exception as e:
            logger.error(f"Error publishing agent: {str(e)}", exc_info=True)
            messages.warning(request, f'Agent created but failed to publish: {str(e)}. You can publish it manually from the agent detail page.')
        
        logger.info(f"Created first agent {agent.id} ({agent.name}) for user {request.user.username} with MCP server {mcp_server.id}")
        
        # Redirect to chat page with the new agent
        return redirect('agent_chat', slug=agent.slug)
        
    except Exception as e:
        logger.error(f"Error creating first agent: {str(e)}", exc_info=True)
        messages.error(request, f'An error occurred while creating your first agent: {str(e)}')
        return redirect('agents_list')


@login_required
def agent_create(request):
    """Create a new agent."""
    # Get available and compatible models with use cases serialized for template
    from django.db.models import Q
    available_models = AIModel.objects.filter(
        is_available=True
    ).filter(
        Q(metadata__is_compatible=True) | 
        Q(metadata__is_compatible__isnull=True) |
        ~Q(metadata__is_compatible=False)
    ).order_by('provider', 'model_name')
    
    # Serialize use_cases as JSON strings for template
    import json
    for model in available_models:
        if model.use_cases:
            model.use_cases_json = json.dumps(model.use_cases)
        else:
            model.use_cases_json = '[]'
    
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        instruction = request.POST.get('instruction', '').strip()
        send_introduction = request.POST.get('send_introduction') == 'on'  # Checkbox returns 'on' when checked
        memory_type = request.POST.get('memory_type', '').strip()
        memory_max_tokens = request.POST.get('memory_max_tokens', '').strip()
        use_rag = request.POST.get('use_rag') == 'on'
        
        # Validate required fields
        if not name:
            messages.error(request, 'Agent name is required.')
            context = {
                'available_models': available_models,
            }
            return render(request, 'dashboard/agent_create.html', context)
        
        if not instruction:
            messages.error(request, 'Instruction is required. Please provide instructions for the agent.')
            context = {
                'available_models': available_models,
            }
            return render(request, 'dashboard/agent_create.html', context)
        
        # Check agent creation limit before proceeding
        try:
            from agents_app.platform_utils import can_perform_action, get_usage_limit, get_usage_count
            can_create, current_count, limit, remaining = can_perform_action(request.user, 'agent_creations')
            if not can_create:
                if limit is None:
                    # Should not happen, but handle gracefully
                    pass
                else:
                    messages.error(request, f'Agent creation limit reached. You have created {current_count} of {limit} allowed agents. Please upgrade your plan to create more agents.')
                    context = {
                        'available_models': available_models,
                    }
                    return render(request, 'dashboard/agent_create.html', context)
        except ImportError:
            # billing_app not available, skip restriction
            pass
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error checking agent creation limit: {str(e)}")
        
        # Model is REQUIRED - agent must be configured with a specific LLM model
        model_id = request.POST.get('model_id')
        if not model_id:
            messages.error(request, 'Model selection is required. Please select an LLM model for this agent.')
            context = {
                'available_models': available_models,
            }
            return render(request, 'dashboard/agent_create.html', context)
        
        try:
            model = AIModel.objects.get(id=model_id, is_available=True)
        except AIModel.DoesNotExist:
            messages.error(request, f'Selected model not found or not available. Please select a valid model.')
            context = {
                'available_models': available_models,
            }
            return render(request, 'dashboard/agent_create.html', context)
        
        # Check model provider restrictions (Bedrock/Ollama)
        try:
            from agents_app.platform_utils import can_use_bedrock, can_use_ollama
            if model.provider == 'bedrock':
                can_use, reason = can_use_bedrock(request.user)
                if not can_use:
                    messages.error(request, f'{reason} Please select an Ollama model or upgrade your plan.')
                    context = {
                        'available_models': available_models,
                    }
                    return render(request, 'dashboard/agent_create.html', context)
            elif model.provider == 'ollama':
                can_use, reason = can_use_ollama(request.user)
                if not can_use:
                    messages.error(request, f'{reason} Please upgrade your plan to use Ollama models.')
                    context = {
                        'available_models': available_models,
                    }
                    return render(request, 'dashboard/agent_create.html', context)
        except ImportError:
            # billing_app not available, skip restriction
            pass
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error checking model provider restrictions: {str(e)}")
        
        # Check if model requires inference profile
        requires_inference_profile = model.metadata.get('requires_inference_profile', False)
        inference_profile_id = request.POST.get('inference_profile_id')
        
        if requires_inference_profile:
            if not inference_profile_id:
                messages.error(request, 'This model requires an inference profile. Please create and select an inference profile.')
                context = {
                    'available_models': available_models,
                }
                return render(request, 'dashboard/agent_create.html', context)
            
            try:
                from .models import InferenceProfile
                inference_profile = InferenceProfile.objects.get(
                    id=inference_profile_id,
                    model=model,
                    status='active'
                )
            except InferenceProfile.DoesNotExist:
                messages.error(request, 'Selected inference profile not found or not active. Please select a valid inference profile.')
                context = {
                    'available_models': available_models,
                }
                return render(request, 'dashboard/agent_create.html', context)
        
        # Build configuration with instruction and model information
        configuration = {
            'instruction': instruction,
            'system_prompt': instruction,  # Store instruction as system_prompt for use with LLM
            'model_id': model.model_id,
            'model_name': model.model_name,
            'model_provider': model.provider,
            'send_introduction': send_introduction,
            'use_rag': use_rag,
        }
        
        # Add memory settings if provided
        if memory_type and memory_type != 'buffer':
            configuration['memory_type'] = memory_type
        if memory_max_tokens:
            try:
                configuration['memory_max_tokens'] = int(memory_max_tokens)
            except ValueError:
                pass
        
        agent = Agent.objects.create(
            name=name,
            description=description,
            agent_type='chat_agent',  # Default value, not shown in form
            configuration=configuration,
            user=request.user,
            model=model,
            inference_profile=inference_profile if requires_inference_profile else None,
            status='draft'
        )
        
        # Track agent creation usage
        try:
            from agents_app.platform_utils import track_usage
            track_usage(request.user, 'agent_creations', count=1, metadata={'agent_id': agent.id, 'agent_name': agent.name})
        except ImportError:
            # billing_app not available, skip tracking
            pass
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Error tracking agent creation: {str(e)}")
        
        # Initialize with appropriate client based on model
        try:
            if model and model.provider == 'ollama':
                from .ollama_integration import OllamaClient, is_ollama_available
                if not is_ollama_available():
                    messages.error(request, 'Ollama is not configured or not reachable. Set OLLAMA_ENABLED and OLLAMA_BASE_URL and ensure the server is running.')
                    return redirect('agent_create')
                client = OllamaClient()
            else:
                client = get_bedrock_client()
            
            agent_result = client.create_agent(
                agent_name=agent.name,
                agent_config=agent.configuration
            )
            agent.quick_suite_agent_id = agent_result.get('agent_id')
            agent.save()
        except Exception as e:
            messages.warning(request, f'Agent initialization warning: {str(e)}')
        
        messages.success(request, 'Agent created successfully!')
        return redirect('agent_detail', slug=agent.slug)
    
    context = {
        'available_models': available_models,
    }
    return render(request, 'dashboard/agent_create.html', context)


@login_required
def agent_edit(request, slug):
    """Edit agent details. Only the owner can edit - shared agents cannot be modified."""
    agent = get_object_or_404(Agent, slug=slug)
    
    # Only allow owner to edit - prevent shared agent editing
    if agent.user != request.user:
        messages.error(request, 'You can only edit agents that you own. Shared agents cannot be modified.')
        return redirect('agent_detail', slug=agent.slug)
    
    if request.method == 'POST':
        # Update agent fields
        agent.name = request.POST.get('name', agent.name)
        agent.description = request.POST.get('description', agent.description)
        
        # Update agent_type with validation
        new_agent_type = request.POST.get('agent_type', agent.agent_type)
        if new_agent_type:
            # Validate agent_type is a valid choice
            valid_agent_types = dict(Agent.AGENT_TYPE_CHOICES).keys()
            if new_agent_type in valid_agent_types:
                agent.agent_type = new_agent_type
            else:
                messages.warning(request, f'Invalid agent type: {new_agent_type}. Keeping current type: {agent.get_agent_type_display()}')
        
        # Initialize configuration if needed
        if not agent.configuration:
            agent.configuration = {}
        
        # Update settings from form fields (preferred method)
        send_introduction = request.POST.get('send_introduction') == 'on'
        instruction = request.POST.get('instruction', '').strip()
        memory_type = request.POST.get('memory_type', '').strip()
        memory_max_tokens = request.POST.get('memory_max_tokens', '').strip()
        use_rag = request.POST.get('use_rag') == 'on'
        
        # Update configuration from form fields
        agent.configuration['send_introduction'] = send_introduction
        if instruction:
            agent.configuration['instruction'] = instruction
            agent.configuration['system_prompt'] = instruction  # Keep both for compatibility
        if memory_type:
            agent.configuration['memory_type'] = memory_type
        elif 'memory_type' in agent.configuration:
            # Remove if set to default (buffer)
            if agent.configuration['memory_type'] == 'buffer':
                del agent.configuration['memory_type']
        if memory_max_tokens:
            try:
                agent.configuration['memory_max_tokens'] = int(memory_max_tokens)
            except ValueError:
                pass
        elif 'memory_max_tokens' in agent.configuration:
            del agent.configuration['memory_max_tokens']
        agent.configuration['use_rag'] = use_rag
        
        # Update configuration from JSON if provided (JSON takes precedence for advanced settings)
        config_json = request.POST.get('configuration', '').strip()
        if config_json:
            try:
                import json
                # Parse JSON configuration
                config_obj = json.loads(config_json)
                # Merge JSON config (preserves form field values but allows JSON to override)
                agent.configuration.update(config_obj)
                # Ensure form field values are preserved (they're the primary interface)
                agent.configuration['send_introduction'] = send_introduction
                if instruction:
                    agent.configuration['instruction'] = instruction
                    agent.configuration['system_prompt'] = instruction
                agent.configuration['use_rag'] = use_rag
            except json.JSONDecodeError as e:
                messages.error(request, f'Invalid JSON in configuration field: {str(e)}')
                # Re-render form with error
                from django.db.models import Q
                available_models = AIModel.objects.filter(
                    is_available=True
                ).filter(
                    Q(metadata__is_compatible=True) | 
                    Q(metadata__is_compatible__isnull=True) |
                    ~Q(metadata__is_compatible=False)
                ).order_by('provider', 'model_name')
                import json as json_module
                config_json_display = json_module.dumps(agent.configuration, indent=2) if agent.configuration else '{}'
                context = {
                    'agent': agent,
                    'available_models': available_models,
                    'config_json': config_json_display,
                }
                return render(request, 'dashboard/agent_edit.html', context)
        
        # Update model if provided (only if agent is not published)
        model_id = request.POST.get('model_id')
        if model_id:
            if agent.status == 'published':
                messages.warning(request, 'Cannot change the LLM model for published agents. Please unpublish the agent first.')
            else:
                try:
                    new_model = AIModel.objects.get(id=model_id, is_available=True)
                    old_model = agent.model
                    agent.model = new_model
                    # Update configuration with model info
                    if not agent.configuration:
                        agent.configuration = {}
                    agent.configuration['model_id'] = new_model.model_id
                    agent.configuration['model_name'] = new_model.model_name
                    agent.configuration['model_provider'] = new_model.provider
                    if old_model and old_model.id != new_model.id:
                        messages.info(request, f'Model changed from {old_model.model_name} to {new_model.model_name}')
                except AIModel.DoesNotExist:
                    messages.error(request, 'Selected model not found or not available.')
        
        try:
            agent.save()
            messages.success(request, 'Agent updated successfully!')
            return redirect('agent_detail', slug=agent.slug)
        except Exception as e:
            messages.error(request, f'Error updating agent: {str(e)}')
    
    # Get available models for dropdown
    from django.db.models import Q
    available_models = AIModel.objects.filter(
        is_available=True
    ).filter(
        Q(metadata__is_compatible=True) | 
        Q(metadata__is_compatible__isnull=True) |
        ~Q(metadata__is_compatible=False)
    ).order_by('provider', 'model_name')
    
    # Format configuration as JSON string for display
    import json
    config_json = json.dumps(agent.configuration, indent=2) if agent.configuration else '{}'
    
    context = {
        'agent': agent,
        'available_models': available_models,
        'config_json': config_json,
    }
    return render(request, 'dashboard/agent_edit.html', context)


@login_required
def agent_detail(request, slug):
    """View agent details."""
    # #region agent log
    _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H2", "location": "views_dashboard.py:289", "message": "agent_detail entry", "data": {"slug": slug, "user": request.user.username, "user_id": request.user.id}, "timestamp": int(__import__('time').time() * 1000)})
    # #endregion
    # Check if user owns the agent or has accepted a share
    try:
        agent = Agent.objects.get(slug=slug)
        # #region agent log
        _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H2", "location": "views_dashboard.py:295", "message": "agent found", "data": {"agent_id": agent.id, "agent_name": agent.name, "agent_owner_id": agent.user.id, "agent_owner_username": agent.user.username, "request_user_id": request.user.id, "is_owner": agent.user == request.user}, "timestamp": int(__import__('time').time() * 1000)})
        # #endregion
        # Check ownership or accepted share
        has_access = False
        is_owner = False
        if agent.user == request.user:
            has_access = True
            is_owner = True
            # #region agent log
            _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H2", "location": "views_dashboard.py:299", "message": "user is owner", "data": {"is_owner": True}, "timestamp": int(__import__('time').time() * 1000)})
            # #endregion
        else:
            # Check if user has accepted a share for this agent
            accepted_share = AgentShare.objects.filter(
                agent=agent,
                email=request.user.email,
                is_accepted=True,
                accepted_by=request.user
            ).first()
            if accepted_share and not accepted_share.is_expired():
                has_access = True
                # #region agent log
                _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H2", "location": "views_dashboard.py:308", "message": "user has shared access", "data": {"is_owner": False, "share_id": accepted_share.id if accepted_share else None}, "timestamp": int(__import__('time').time() * 1000)})
                # #endregion
        
        if not has_access:
            # #region agent log
            _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H2", "location": "views_dashboard.py:312", "message": "access denied", "data": {"has_access": False}, "timestamp": int(__import__('time').time() * 1000)})
            # #endregion
            from django.http import Http404
            raise Http404("Agent not found or access denied")
    except Agent.DoesNotExist:
        # #region agent log
        _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H2", "location": "views_dashboard.py:315", "message": "agent not found", "data": {"slug": slug}, "timestamp": int(__import__('time').time() * 1000)})
        # #endregion
        from django.http import Http404
        raise Http404("Agent not found")
    
    # Handle model update via POST - only owner can update
    if request.method == 'POST' and 'update_model' in request.POST:
        # Only owner can update model - prevent shared agent modification
        if not is_owner:
            messages.error(request, 'Only the agent owner can modify the LLM model. Shared agents cannot be modified.')
            return redirect('agent_detail', slug=agent.slug)
        
        # Prevent model changes for published agents
        if agent.status == 'published':
            messages.warning(request, 'Cannot change the LLM model for published agents. Please unpublish the agent first.')
            return redirect('agent_detail', slug=agent.slug)
        
        # Try to get model_id from multiple possible sources
        model_id = request.POST.get('model_id') or request.POST.get('model_id_hidden')
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Model update request: model_id={model_id}, POST data: {list(request.POST.keys())}")
        
        if model_id:
            try:
                new_model = AIModel.objects.get(id=model_id, is_available=True)
                old_model = agent.model
                old_name = old_model.model_name if old_model else "None"
                
                agent.model = new_model
                
                # Update configuration with new model info
                if not agent.configuration:
                    agent.configuration = {}
                
                agent.configuration['model_id'] = new_model.model_id
                agent.configuration['model_name'] = new_model.model_name
                agent.configuration['model_provider'] = new_model.provider
                
                agent.save()
                
                messages.success(
                    request, 
                    f'Model updated from {old_name} to {new_model.model_name}'
                )
                logger.info(f"Model updated successfully: {old_name} -> {new_model.model_name}")
            except AIModel.DoesNotExist:
                messages.error(request, 'Selected model not found or not available.')
                logger.error(f"Model not found: {model_id}")
            except Exception as e:
                messages.error(request, f'Error updating model: {str(e)}')
                logger.error(f"Error updating model: {str(e)}", exc_info=True)
        else:
            messages.error(request, 'Please select a model.')
            logger.warning("No model_id provided in POST request")
        
        return redirect('agent_detail', slug=agent.slug)
    
    # Get feedback analytics
    optimizer = FeedbackOptimizer(agent)
    feedback_analysis = optimizer.analyze_feedback()
    
    # Get recent feedbacks for display
    recent_feedbacks = agent.feedbacks.all()[:10]
    
    # Check if optimization was applied
    optimization_applied = agent.configuration.get('last_optimized') if agent.configuration else None
    
    # Get available models for dropdown (exclude incompatible models)
    from django.db.models import Q
    available_models = AIModel.objects.filter(
        is_available=True
    ).filter(
        Q(metadata__is_compatible=True) | 
        Q(metadata__is_compatible__isnull=True) |
        ~Q(metadata__is_compatible=False)
    ).order_by('provider', 'model_name')
    
    # Get agent shares
    agent_shares = agent.shares.all().order_by('-created_at')
    
    # Get public share (if exists)
    public_share = AgentPublicShare.objects.filter(agent=agent, is_active=True).first()
    
    # Get MCP servers with tools
    mcp_servers_info = get_mcp_servers_info_for_agent(agent)
    
    # Get new API key from session if it exists (will be cleared after display)
    new_agent_api_key = request.session.pop(f'new_agent_api_key_{agent.id}', None)
    if new_agent_api_key:
        request.session.save()  # Save session changes
    
    # Get shared agent usage statistics (only for owner)
    shared_usage_stats = None
    if is_owner:
        from .models import SharedAgentUsage
        from django.db.models import Sum, Count, Max
        from django.utils import timezone
        from datetime import timedelta
        
        # Get current period usage
        now = timezone.now()
        current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Aggregate usage by user for current month
        usage_by_user = SharedAgentUsage.objects.filter(
            agent=agent,
            shared_by=request.user,
            period_start__gte=current_month_start,
            is_daily=False
        ).values('used_by__username', 'used_by__email', 'used_by__id').annotate(
            total_messages=Sum('message_count'),
            total_conversations=Sum('conversation_count'),
            last_used=Max('last_used_at'),
            first_used=Max('first_used_at')
        ).order_by('-last_used')
        
        # Get total stats
        total_usage = SharedAgentUsage.objects.filter(
            agent=agent,
            shared_by=request.user,
            period_start__gte=current_month_start,
            is_daily=False
        ).aggregate(
            total_messages=Sum('message_count'),
            total_conversations=Sum('conversation_count'),
            unique_users=Count('used_by', distinct=True)
        )
        
        shared_usage_stats = {
            'by_user': list(usage_by_user),
            'total': total_usage,
        }
    
    # Check for unpublished changes (for published agents)
    has_unpublished_changes = False
    change_summary = []
    if agent.status == 'published' and is_owner:
        has_unpublished_changes = agent.has_unpublished_changes()
        change_summary = agent.get_change_summary() if has_unpublished_changes else []
    
    agent_api_keys = agent.agent_api_keys.filter(is_active=True).order_by('-created_at')

    context = {
        'agent': agent,
        'training_data': agent.training_data.all()[:10],
        'feedback_analysis': feedback_analysis,
        'recent_feedbacks': recent_feedbacks,
        'shared_usage_stats': shared_usage_stats,
        'optimization_applied': optimization_applied,
        'mcp_servers': mcp_servers_info,
        'available_models': available_models,
        'new_agent_api_key': new_agent_api_key,  # Only set if just generated
        'agent_api_keys': agent_api_keys,
        'is_owner': is_owner,  # Flag to check if user owns the agent
        'has_unpublished_changes': has_unpublished_changes,
        'change_summary': change_summary,
    }
    # Add shares to context
    context['agent_shares'] = agent_shares
    context['public_share'] = public_share
    
    return render(request, 'dashboard/agent_detail.html', context)


@login_required
def agent_train(request, slug):
    """Train agent page."""
    logger = logging.getLogger(__name__)
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    
    # Initialize training_status
    training_status = None
    
    if request.method == 'POST':
        # Handle tool enable/disable - save to database (AgentTool model)
        if 'toggle_tool' in request.POST:
            tool_name = request.POST.get('tool_name')
            enabled = request.POST.get('enabled') == 'true'
            
            from .models import AgentTool
            
            # Get or create AgentTool record
            agent_tool, created = AgentTool.objects.get_or_create(
                agent=agent,
                tool_name=tool_name,
                defaults={'is_enabled': enabled}
            )
            
            # Update if it already exists
            if not created:
                agent_tool.is_enabled = enabled
                agent_tool.save(update_fields=['is_enabled', 'updated_at'])
            
            messages.success(request, f'Tool {tool_name} {"enabled" if enabled else "disabled"}')
            return redirect('agent_train', slug=agent.slug)
        
        # Handle bulk tool configuration save (from form)
        if 'save_tool_config' in request.POST:
            from .models import AgentTool
            
            # Get all tool names and their enabled state from POST data
            tool_configs = {}
            for key, value in request.POST.items():
                if key.startswith('tool_enabled_'):
                    tool_name = key.replace('tool_enabled_', '')
                    tool_configs[tool_name] = value == 'true'
            
            # Update or create AgentTool records
            for tool_name, is_enabled in tool_configs.items():
                AgentTool.objects.update_or_create(
                    agent=agent,
                    tool_name=tool_name,
                    defaults={'is_enabled': is_enabled}
                )
            
            messages.success(request, 'Tool configuration saved successfully')
            return redirect('agent_train', slug=agent.slug)
        
        # Handle training data upload (supports multiple files)
        if 'file' in request.FILES:
            files = request.FILES.getlist('file')
            
            # Check document count limit (150 per agent)
            MAX_DOCUMENTS_PER_AGENT = 150
            MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB in bytes
            current_count = agent.training_data.count()
            
            # Check if adding all files would exceed the limit
            remaining_slots = MAX_DOCUMENTS_PER_AGENT - current_count
            if len(files) > remaining_slots:
                messages.error(request, f'Cannot upload {len(files)} file(s). Only {remaining_slots} slot(s) remaining. Maximum {MAX_DOCUMENTS_PER_AGENT} documents per agent.')
                return redirect('agent_train', slug=agent.slug)
            
            # Allowed MIME types for text documents
            import mimetypes
            ALLOWED_MIME_TYPES = [
                'text/plain', 'text/html', 'text/css', 'text/javascript', 'text/xml',
                'application/json', 'application/xml', 'application/javascript',
                'application/pdf',  # PDF can contain text
                'application/msword',  # .doc
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  # .docx
                'application/vnd.ms-excel',  # .xls
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # .xlsx
                'application/vnd.ms-powerpoint',  # .ppt
                'application/vnd.openxmlformats-officedocument.presentationml.presentation',  # .pptx
                'text/csv', 'text/tsv',
                'application/rtf',  # Rich Text Format
            ]
            
            # Allowed file extensions
            ALLOWED_EXTENSIONS = [
                '.txt', '.text', '.md', '.markdown', '.rst',
                '.html', '.htm', '.xml', '.json', '.csv', '.tsv',
                '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                '.rtf', '.log', '.conf', '.config', '.ini', '.yaml', '.yml',
                '.js', '.jsx', '.ts', '.tsx', '.css', '.scss', '.sass',
                '.py', '.java', '.cpp', '.c', '.h', '.hpp', '.cs', '.go',
                '.rb', '.php', '.swift', '.kt', '.scala', '.sh', '.bash',
                '.sql', '.r', '.m', '.pl', '.pm', '.lua', '.vim', '.vimrc',
            ]
            
            uploaded_count = 0
            skipped_files = []
            
            for file in files:
                # Check file size limit (10MB)
                if file.size > MAX_FILE_SIZE:
                    skipped_files.append(f'{file.name} (exceeds 10MB limit)')
                    continue
                
                # Validate file is a text document
                file_mime_type, _ = mimetypes.guess_type(file.name)
                file_ext = '.' + file.name.split('.')[-1].lower() if '.' in file.name else ''
                is_text_document = False
                
                # Check MIME type
                if file_mime_type and any(allowed in file_mime_type.lower() for allowed in ['text/', 'application/']):
                    if file_mime_type in ALLOWED_MIME_TYPES:
                        is_text_document = True
                    elif file_mime_type.startswith('text/'):
                        is_text_document = True
                
                # Check extension if MIME type check didn't pass
                if not is_text_document and file_ext in ALLOWED_EXTENSIONS:
                    is_text_document = True
                
                if not is_text_document:
                    skipped_files.append(f'{file.name} (unsupported file type)')
                    continue
                
                # Create training data record
                training_data = TrainingData.objects.create(
                    agent=agent,
                    data_type='file',
                    file_path=file,
                    content={'filename': file.name, 'size': file.size}
                )
                
                # Upload to Bedrock
                try:
                    client = get_bedrock_client()
                    client.upload_training_data(
                        agent_id=agent.quick_suite_agent_id or str(agent.id),
                        training_data=[{'filename': file.name}]
                    )
                except Exception as e:
                    logger.warning(f'Bedrock upload warning for {file.name}: {str(e)}')
                
                # Index training data for RAG
                try:
                    from .tasks import index_training_data_for_rag
                    task_result = index_training_data_for_rag.delay(agent.id, training_data.id)
                    logger.info(f"Triggered RAG indexing for training data {training_data.id} of agent {agent.id}")
                except Exception as e:
                    logger.warning(f"Failed to trigger RAG indexing for {file.name}: {str(e)}")
                
                uploaded_count += 1
            
            # Update training data count
            if uploaded_count > 0:
                agent.training_data_count = agent.training_data.count()
                agent.save(update_fields=['training_data_count'])
                new_count = agent.training_data_count
                messages.success(request, f'{uploaded_count} file(s) uploaded successfully! ({new_count}/{MAX_DOCUMENTS_PER_AGENT} documents)')
            
            # Report skipped files
            if skipped_files:
                messages.warning(request, f'Skipped {len(skipped_files)} file(s): {", ".join(skipped_files)}')
        
        # Handle delete training data
        elif 'delete_training_data' in request.POST:
            training_data_id = request.POST.get('training_data_id')
            if training_data_id:
                try:
                    # TrainingData is imported at module level, so we can use it directly
                    training_data = TrainingData.objects.get(id=training_data_id, agent=agent)
                    
                    # Delete the file if it exists
                    if training_data.file_path:
                        try:
                            training_data.file_path.delete(save=False)
                        except Exception as e:
                            logger.warning(f"Error deleting file for training data {training_data_id}: {str(e)}")
                    
                    # Clean up RAG index for this training data
                    try:
                        from .models import RAGIndexStatus
                        # Delete RAG index status records for this training data
                        RAGIndexStatus.objects.filter(agent=agent, training_data=training_data).delete()
                        logger.info(f"Deleted RAG index status for training data {training_data_id}")
                    except Exception as e:
                        logger.warning(f"Error deleting RAG index status for training data {training_data_id}: {str(e)}")
                    
                    # Note: RAG vectors will be cleaned up during next re-indexing
                    # For immediate cleanup, we would need to query and delete vectors by metadata
                    # This is handled by the signal in signals.py when training data is deleted
                    
                    # Delete the training data
                    training_data.delete()
                    
                    # Update training data count
                    agent.training_data_count = max(0, agent.training_data_count - 1)
                    agent.save(update_fields=['training_data_count'])
                    
                    messages.success(request, 'Training data deleted successfully.')
                        
                except TrainingDataDoesNotExist:
                    messages.error(request, 'Training data not found.')
                except Exception as e:
                    logger.error(f"Error deleting training data {training_data_id}: {str(e)}", exc_info=True)
                    messages.error(request, f'Error deleting training data: {str(e)}')
            else:
                messages.error(request, 'Training data ID is required.')
            
            return redirect('agent_train', slug=agent.slug)
        
        # Handle start training
        elif 'start_training' in request.POST:
            # Maximum training data per agent (must match MAX_DOCUMENTS_PER_AGENT)
            MAX_TRAINING_DATA_PER_AGENT = 150
            
            # Get actual count of training data (not cached, use actual database count)
            total_training_data_count = agent.training_data.count()
            
            if total_training_data_count == 0:
                messages.error(request, 'No training data uploaded. Please upload at least one training document.')
            elif total_training_data_count > MAX_TRAINING_DATA_PER_AGENT:
                excess_count = total_training_data_count - MAX_TRAINING_DATA_PER_AGENT
                messages.error(
                    request, 
                    f'Training data limit exceeded. You have {total_training_data_count} training data items, '
                    f'but the maximum allowed is {MAX_TRAINING_DATA_PER_AGENT}. '
                    f'Please delete {excess_count} training data item(s) before starting training.'
                )
            elif agent.status == 'training':
                messages.info(request, 'Training is already in progress.')
            else:
                # Allow training at any status (draft, testing, published, etc.)
                previous_status = agent.status
                agent.status = 'training'
                agent.save(update_fields=['status'])
                
                # Trigger RAG indexing for all training data before starting training
                # This ensures training data is indexed for RAG retrieval during agent execution
                try:
                    from .tasks import index_training_data_for_rag
                    # Index all training data for this agent (async via Celery)
                    task_result = index_training_data_for_rag.delay(agent.id, training_data_id=None)
                    logger.info(f"Triggered RAG indexing for all training data of agent {agent.id} before training (Celery task ID: {task_result.id})")
                except Exception as e:
                    logger.warning(f"Failed to trigger RAG indexing before training: {str(e)}")
                    # Continue with training even if RAG indexing fails
                
                try:
                    client = get_bedrock_client()
                    client.train_agent(agent.quick_suite_agent_id or str(agent.id))
                except Exception as e:
                    messages.warning(request, f'Training error: {str(e)}')
                    # Revert status if training failed to start
                    agent.status = previous_status
                    agent.save(update_fields=['status'])
                
                messages.success(request, 'Training started! RAG indexing has been triggered in the background.')
        
        # Handle connector OAuth initiation
        if 'initiate_connector_oauth' in request.POST:
            connector_id = request.POST.get('connector_id')
            try:
                from connectors.models import Connector
                from connectors.jira_oauth import JIRAOAuthClient, ConfluenceOAuthClient
                from connectors.gitlab_oauth import GitLabOAuthClient
                from connectors.models import ConnectorType
                
                connector = Connector.objects.get(id=connector_id, user=request.user)
                # Include redirect parameter to return to training page after OAuth
                from urllib.parse import urlencode
                redirect_url = request.build_absolute_uri(f'/dashboard/agents/{agent.id}/train/')
                callback_base = request.build_absolute_uri(f'/api/connectors/{connector_id}/oauth/callback/')
                callback_url = f"{callback_base}?{urlencode({'redirect': redirect_url})}"
                
                if connector.connector_type == ConnectorType.JIRA:
                    client = JIRAOAuthClient(connector)
                    auth_data = client.get_authorization_url(callback_url)
                    return redirect(auth_data['authorization_url'])
                elif connector.connector_type == ConnectorType.CONFLUENCE:
                    client = ConfluenceOAuthClient(connector)
                    auth_data = client.get_authorization_url(callback_url)
                    return redirect(auth_data['authorization_url'])
                elif connector.connector_type == ConnectorType.GITLAB:
                    client = GitLabOAuthClient(connector)
                    auth_data = client.get_authorization_url(callback_url)
                    return redirect(auth_data['authorization_url'])
                else:
                    messages.error(request, f'Unsupported connector type: {connector.connector_type}')
            except Exception as e:
                logger.error(f"Error initiating connector OAuth: {str(e)}", exc_info=True)
                messages.error(request, f'Failed to initiate OAuth: {str(e)}')
            return redirect('agent_train', slug=agent.slug)
        
        # Handle connector status validation (POST for form submission - kept for backward compatibility)
        if 'validate_connector' in request.POST:
            connector_id = request.POST.get('connector_id')
            try:
                from connectors.models import Connector
                from connectors.validator import ConnectorValidator
                
                connector = Connector.objects.get(id=connector_id, user=request.user)
                result = ConnectorValidator.validate_and_update_status(connector)
                
                if result['valid']:
                    messages.success(request, f'{connector.name}: {result["message"]}')
                else:
                    messages.warning(request, f'{connector.name}: {result["message"]}')
            except Exception as e:
                logger.error(f"Error validating connector: {str(e)}", exc_info=True)
                messages.error(request, f'Failed to validate connector: {str(e)}')
            return redirect('agent_train', slug=agent.slug)
        
        # Handle connector data sync
        if 'sync_connector_data' in request.POST:
            connector_id = request.POST.get('connector_id')
            try:
                from connectors.models import Connector, ConnectorSync
                from connectors.tasks import sync_connector_data
                
                connector = Connector.objects.get(id=connector_id, user=request.user)
                
                if connector.status != 'connected':
                    messages.error(request, 'Connector is not connected. Please complete OAuth flow first.')
                    return redirect('agent_train', slug=agent.slug)
                
                # Create sync record
                sync = ConnectorSync.objects.create(
                    connector=connector,
                    agent=agent,
                    status='pending',
                    sync_type=f'{connector.connector_type}_sync',
                )
                
                # Start sync task
                sync_connector_data.delay(sync.id)
                
                messages.success(request, f'Data sync started for {connector.name}. Check back in a few moments.')
            except Exception as e:
                logger.error(f"Error starting connector sync: {str(e)}", exc_info=True)
                messages.error(request, f'Failed to start sync: {str(e)}')
            return redirect('agent_train', slug=agent.slug)
        
        # Get training status if training (for POST requests)
        if agent.status == 'training':
            try:
                client = get_bedrock_client()
                training_status = client.get_training_status(agent.quick_suite_agent_id or str(agent.id))
                if training_status and training_status.get('status') == 'completed':
                    agent.status = 'testing'
                    agent.save(update_fields=['status'])
                    messages.success(request, 'Training completed!')
            except Exception as e:
                pass
        
        return redirect('agent_train', slug=agent.slug)
    
    # Get training status if training (for GET requests)
    if agent.status == 'training':
        try:
            client = get_bedrock_client()
            training_status = client.get_training_status(agent.quick_suite_agent_id or str(agent.id))
            if training_status and training_status.get('status') == 'completed':
                agent.status = 'testing'
                agent.save(update_fields=['status'])
                messages.success(request, 'Training completed!')
        except Exception as e:
            pass
    
    # Get all available tools (including disabled ones for display)
    from .tool_executor import ToolExecutor
    tool_executor = ToolExecutor(agent)
    # Use get_all_tools_schema to get ALL tools including disabled ones
    all_tools = tool_executor.get_all_tools_schema()
    
    # Get enabled/disabled tools from database (AgentTool model)
    # Fallback to JSON configuration for backward compatibility
    from .models import AgentTool
    agent_tools = AgentTool.objects.filter(agent=agent)
    
    if agent_tools.exists():
        # Use database configuration
        enabled_tools_set = set(agent_tools.filter(is_enabled=True).values_list('tool_name', flat=True))
        disabled_tools_set = set(agent_tools.filter(is_enabled=False).values_list('tool_name', flat=True))
    else:
        # Fallback to JSON configuration for backward compatibility
        enabled_tools_list = agent.configuration.get('enabled_tools', []) if agent.configuration else []
        disabled_tools_list = agent.configuration.get('disabled_tools', []) if agent.configuration else []
        enabled_tools_set = set(enabled_tools_list)
        disabled_tools_set = set(disabled_tools_list)
    
    # Categorize tools
    default_tools = []
    custom_tools = []
    mcp_tools = []
    
    for tool in all_tools:
        tool_name = tool.get('name', '')
        
        # Determine if tool is enabled:
        # - If tool is in disabled_tools_set, it's disabled
        # - If enabled_tools_set is not empty and tool is not in it, it's disabled
        # - Otherwise, it's enabled (default state)
        if tool_name in disabled_tools_set:
            is_enabled = False
        elif enabled_tools_set and tool_name not in enabled_tools_set:
            is_enabled = False
        else:
            is_enabled = True
        
        tool_info = {
            'name': tool_name,
            'description': tool.get('description', ''),
            'instructions': tool.get('instructions', ''),
            'parameters': tool.get('parameters', []),
            'enabled': is_enabled
        }
        
        if tool_name.startswith('mcp_'):
            mcp_tools.append(tool_info)
        elif tool_name in [t.name for t in tool_executor.tool_manager.tools.values() if hasattr(t, 'tool_config')]:
            custom_tools.append(tool_info)
        else:
            default_tools.append(tool_info)
    
    # Get user's connectors and validate their status
    from connectors.models import Connector
    from connectors.validator import ConnectorValidator
    user_connectors = Connector.objects.filter(user=request.user).order_by('-created_at')
    
    # Auto-validate connectors that are marked as connected or error (but not recently validated)
    from datetime import timedelta
    validation_threshold = timezone.now() - timedelta(hours=1)  # Validate if not checked in last hour
    
    for connector in user_connectors:
        if connector.status in ['connected', 'error']:
            # Check if validation is needed
            last_validation = connector.metadata.get('last_validation', {})
            last_validation_time = last_validation.get('timestamp')
            
            needs_validation = False
            if not last_validation_time:
                needs_validation = True
            else:
                try:
                    # Parse ISO format timestamp
                    from datetime import datetime
                    validation_dt = datetime.fromisoformat(last_validation_time.replace('Z', '+00:00'))
                    if timezone.make_aware(validation_dt) if timezone.is_naive(validation_dt) else validation_dt < validation_threshold:
                        needs_validation = True
                except:
                    needs_validation = True
            
            if needs_validation:
                try:
                    ConnectorValidator.validate_and_update_status(connector)
                except Exception as e:
                    logger.debug(f"Error auto-validating connector {connector.id}: {str(e)}")
    
    # Calculate counts by data type (for all training data, not filtered)
    from django.db.models import Count, Q
    from django.core.paginator import Paginator
    # TrainingData is already imported at module level, no need to import again
    import os
    
    training_data_by_type = agent.training_data.values('data_type').annotate(count=Count('id')).order_by('data_type')
    training_data_type_counts = {}
    for item in training_data_by_type:
        data_type = item['data_type']
        count = item['count']
        # Get display name from choices
        display_name = dict(TrainingData.DATA_TYPE_CHOICES).get(data_type, data_type.title())
        training_data_type_counts[display_name] = count
    
    # Calculate file type counts for files only (data_type='file')
    file_type_counts = {}
    file_training_data = agent.training_data.filter(data_type='file')
    file_count = file_training_data.count()
    
    if file_count > 0:
        for training_data in file_training_data:
            if training_data.file_path:
                # Get file extension from file_path
                file_name = training_data.file_path.name if hasattr(training_data.file_path, 'name') else str(training_data.file_path)
                file_ext = os.path.splitext(file_name)[1].lower()
                
                # Normalize extension (remove dot, uppercase)
                if file_ext:
                    file_ext = file_ext[1:].upper()  # Remove dot and uppercase
                else:
                    file_ext = 'UNKNOWN'
                
                # Count by extension
                file_type_counts[file_ext] = file_type_counts.get(file_ext, 0) + 1
    
    # Get search query from request
    search_query = request.GET.get('search', '').strip()
    
    # Filter training data based on search query
    training_data_queryset = agent.training_data.all()
    if search_query:
        # Search in file_path name and data_type
        # Note: JSONField content search is complex and database-dependent, so we focus on searchable fields
        training_data_queryset = training_data_queryset.filter(
            Q(file_path__icontains=search_query) |
            Q(data_type__icontains=search_query)
        )
    
    # Paginate training data - 8 items per page
    paginator = Paginator(training_data_queryset, 7)
    page_number = request.GET.get('page', 1)
    try:
        training_data_page = paginator.page(page_number)
    except:
        training_data_page = paginator.page(1)
    
    # Maximum documents per agent
    MAX_DOCUMENTS_PER_AGENT = 150
    
    # Sync training_data_count to ensure accuracy before displaying
    agent.sync_training_data_count()
    
    context = {
        'agent': agent,
        'training_data': training_data_page,
        'training_status': training_status,
        'default_tools': default_tools,
        'custom_tools': custom_tools,
        'mcp_tools': mcp_tools,
        'enabled_tools': list(enabled_tools_set),
        'disabled_tools': list(disabled_tools_set),
        'connectors': user_connectors,
        'training_data_type_counts': training_data_type_counts,
        'file_type_counts': file_type_counts,
        'file_count': file_count,
        'search_query': search_query,
        'total_training_data_count': agent.training_data.count(),
        'max_documents_per_agent': MAX_DOCUMENTS_PER_AGENT,
    }
    return render(request, 'dashboard/agent_train.html', context)


@login_required
def training_data_detail(request, slug, training_data_id):
    """Training data detail view with CRUD operations."""
    logger = logging.getLogger(__name__)
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    training_data = get_object_or_404(TrainingData, id=training_data_id, agent=agent)
    
    if request.method == 'POST':
        # Handle update
        if 'update_training_data' in request.POST:
            try:
                # Update data type if provided
                new_data_type = request.POST.get('data_type')
                if new_data_type and new_data_type in dict(TrainingData.DATA_TYPE_CHOICES):
                    training_data.data_type = new_data_type
                
                # Update content if provided (for text-based training data)
                if training_data.data_type != 'file':
                    content_text = request.POST.get('content_text', '').strip()
                    if content_text:
                        # Update content field
                        if not training_data.content:
                            training_data.content = {}
                        training_data.content['text'] = content_text
                        training_data.content['updated_at'] = timezone.now().isoformat()
                        training_data.content['updated_by'] = request.user.username
                
                # Handle file replacement if new file is uploaded
                if 'file' in request.FILES and training_data.data_type == 'file':
                    # Delete old file
                    if training_data.file_path:
                        try:
                            training_data.file_path.delete(save=False)
                        except Exception as e:
                            logger.warning(f"Error deleting old file: {str(e)}")
                    
                    # Save new file
                    new_file = request.FILES['file']
                    training_data.file_path = new_file
                    training_data.content = {
                        'filename': new_file.name,
                        'size': new_file.size,
                        'updated_at': timezone.now().isoformat(),
                        'updated_by': request.user.username
                    }
                
                training_data.save()
                messages.success(request, 'Training data updated successfully.')
                
                # Trigger RAG re-indexing
                try:
                    from .tasks import index_training_data_for_rag
                    index_training_data_for_rag.delay(agent.id, training_data.id)
                    logger.info(f"Triggered RAG re-indexing for training data {training_data.id}")
                except Exception as e:
                    logger.warning(f"Failed to trigger RAG re-indexing: {str(e)}")
                
                return redirect('training_data_detail', slug=agent.slug, training_data_id=training_data.id)
                
            except Exception as e:
                logger.error(f"Error updating training data {training_data_id}: {str(e)}", exc_info=True)
                messages.error(request, f'Error updating training data: {str(e)}')
        
        # Handle delete
        elif 'delete_training_data' in request.POST:
            try:
                # Delete the file if it exists
                if training_data.file_path:
                    try:
                        training_data.file_path.delete(save=False)
                    except Exception as e:
                        logger.warning(f"Error deleting file: {str(e)}")
                
                # Clean up RAG index
                try:
                    from .models import RAGIndexStatus
                    RAGIndexStatus.objects.filter(agent=agent, training_data=training_data).delete()
                except Exception as e:
                    logger.warning(f"Error deleting RAG index status: {str(e)}")
                
                # Delete the training data
                training_data_id_for_redirect = training_data.id
                training_data.delete()
                
                # Update training data count
                agent.training_data_count = max(0, agent.training_data_count - 1)
                agent.save(update_fields=['training_data_count'])
                
                messages.success(request, 'Training data deleted successfully.')
                return redirect('agent_train', slug=agent.slug)
                
            except Exception as e:
                logger.error(f"Error deleting training data {training_data_id}: {str(e)}", exc_info=True)
                messages.error(request, f'Error deleting training data: {str(e)}')
    
    # Prepare content for display
    content_display = {}
    if training_data.content:
        if isinstance(training_data.content, dict):
            content_display = training_data.content
        else:
            content_display = {'raw': str(training_data.content)}
    
    # Check if indexed for RAG
    is_indexed = training_data.is_indexed()
    
    # Prepare JSON content for template (safe JSON string)
    import json
    content_json = json.dumps(content_display) if content_display else '{}'
    
    context = {
        'agent': agent,
        'training_data': training_data,
        'content_display': content_display,
        'content_json': content_json,
        'is_indexed': is_indexed,
        'data_type_choices': TrainingData.DATA_TYPE_CHOICES,
    }
    
    return render(request, 'dashboard/training_data_detail.html', context)


@login_required
def agent_test(request, slug):
    """Test agent page - WebSocket-based chat interface."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    
    mcp_servers_info = get_mcp_servers_info_for_agent(agent)

    # Test page always starts a fresh conversation. Mark any active prior
    # conversations for this agent+user as completed so a new one is created
    # on first message and the UI is empty on load.
    from .models import Conversation
    conversation = None
    conversation_messages = []
    conversation_messages_json = []

    try:
        Conversation.objects.filter(
            agent=agent,
            user=request.user,
            status='active'
        ).update(status='completed')
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error closing prior conversations on test load: {str(e)}", exc_info=True)
    
    # Check for unpublished changes
    has_unpublished_changes = agent.has_unpublished_changes()
    change_summary = agent.get_change_summary() if has_unpublished_changes else []
    
    def _safe_abs_url(path_name, url_kwargs=None):
        """Build absolute URL; fallback to SITE_URL if Host is missing or invalid."""
        path = reverse(path_name, kwargs=url_kwargs) if url_kwargs else reverse(path_name)
        try:
            return request.build_absolute_uri(path)
        except Exception:
            base = getattr(settings, 'SITE_URL', 'https://localhost').rstrip('/')
            if not path.startswith('/'):
                path = '/' + path
            return base + path

    context = {
        'agent': agent,
        'mcp_servers': mcp_servers_info,
        'mcp_return_page': 'test',
        'conversation': conversation,
        'conversation_messages': conversation_messages,
        'conversation_messages_json': conversation_messages_json,
        'has_unpublished_changes': has_unpublished_changes,
        'change_summary': change_summary,
        'webhook_api_url': _safe_abs_url('agent-webhook'),
        'webhook_get_answer_url': _safe_abs_url('get-answer'),
        'webhook_test_endpoint_url': _safe_abs_url('agent_webhook_test', {'slug': agent.slug}),
    }
    return render(request, 'dashboard/agent_test.html', context)


@login_required
@require_http_methods(['GET', 'POST'])
def agent_webhook_test(request, slug):
    """Dashboard-only webhook test: same payload as POST /api/webhook/agent/, session auth.

    POST queues a new conversation + Celery job. GET ?request_id= polls status without an API key.
    Allowed agent statuses: testing, published (matches Celery processor).
    """
    agent = get_object_or_404(Agent, slug=slug, user=request.user)

    if request.method == 'GET':
        rid = request.GET.get('request_id')
        if not rid:
            return JsonResponse({'error': 'request_id query parameter required'}, status=400)
        try:
            req_obj = AgentRequest.objects.select_related('conversation').get(request_id=rid, agent=agent)
        except AgentRequest.DoesNotExist:
            return JsonResponse({'error': 'Request not found'}, status=404)

        if req_obj.status == 'pending':
            return JsonResponse(
                {
                    'request_id': rid,
                    'status': 'pending',
                    'message': 'Request is still queued or starting.',
                },
                status=202,
            )
        if req_obj.status == 'processing':
            return JsonResponse(
                {
                    'request_id': rid,
                    'status': 'processing',
                    'message': 'Request is being processed.',
                },
                status=202,
            )
        if req_obj.status == 'completed':
            conv_id = req_obj.conversation.conversation_id if req_obj.conversation else None
            return JsonResponse(
                {
                    'request_id': rid,
                    'conversation_id': conv_id,
                    'status': 'completed',
                    'answer': req_obj.response or '',
                    'tool_calls': req_obj.tool_calls or [],
                    'iterations': req_obj.iterations or 0,
                    'created_at': req_obj.completed_at.isoformat() if req_obj.completed_at else None,
                }
            )
        if req_obj.status == 'failed':
            return JsonResponse(
                {
                    'request_id': rid,
                    'status': 'failed',
                    'error': req_obj.error_message or 'Request processing failed',
                },
                status=500,
            )

        return JsonResponse({'request_id': rid, 'status': req_obj.status}, status=500)

    # POST — enqueue webhook-style request
    if agent.status not in ('published', 'testing'):
        return JsonResponse(
            {'error': 'Put the agent in Testing or Published to run webhook tests.'},
            status=400,
        )

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    serializer = AgentWebhookSerializer(data=data)
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)

    composed_message = serializer.composed_message()
    metadata = serializer.validated_data.get('metadata') or {}

    request_id_str = str(uuid.uuid4())
    conversation_id_str = str(uuid.uuid4())

    conversation = Conversation.objects.create(
        agent=agent,
        user=agent.user,
        conversation_id=conversation_id_str,
        status='active',
    )

    from .tasks import process_conversation_request

    request_obj = AgentRequest.objects.create(
        request_id=request_id_str,
        agent=agent,
        conversation=conversation,
        status='pending',
        message=composed_message,
    )

    task = process_conversation_request.delay(
        request_id=request_id_str,
        agent_id=agent.id,
        conversation_id=conversation_id_str,
        message=composed_message,
    )

    request_obj.celery_task_id = task.id
    request_obj.save(update_fields=['celery_task_id'])

    logger.info('Dashboard webhook test queued for agent %s: request_id=%s', agent.id, request_id_str)

    return JsonResponse(
        {
            'request_id': request_id_str,
            'conversation_id': conversation_id_str,
            'agent_id': agent.id,
            'status': 'pending',
            'metadata': metadata,
            'message': 'Queued. Poll this URL with GET ?request_id= for status (same session).',
        },
        status=202,
    )


@login_required
def agent_publish(request, slug):
    """Publish agent page."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    
    if request.method == 'POST':
        # Allow publishing from any status, but automatically move to testing first if needed
        original_status = agent.status
        
        # If not in testing or published status, move to testing first
        if agent.status not in ['testing', 'published']:
            agent.status = 'testing'
            agent.save(update_fields=['status'])
            messages.info(request, f'Agent status changed from {original_status} to testing before publishing.')
        
        # Check for unpublished changes before publishing
        has_changes = agent.has_unpublished_changes()
        
        # If already published, check for unpublished changes
        if agent.status == 'published' and not has_changes:
            messages.info(request, 'No changes to publish. Agent is already up to date.')
        else:
            # Set status to testing if needed (for publish method validation)
            if agent.status == 'published':
                agent.status = 'testing'
                agent.save(update_fields=['status'])
            
            agent.publish()
            if has_changes:
                messages.success(request, 'Agent published successfully with new configuration!')
            else:
                messages.success(request, 'Agent published successfully!')
            # Redirect to chat page with the published agent
            from django.urls import reverse
            return redirect(reverse('agent_chat', kwargs={'slug': agent.slug}))
    
    # Check for unpublished changes
    has_unpublished_changes = agent.has_unpublished_changes()
    change_summary = agent.get_change_summary() if has_unpublished_changes else []
    
    # Get feedback information
    from .models import AgentFeedback
    feedbacks = agent.feedbacks.all()
    feedback_summary = {
        'total': feedbacks.count(),
        'positive': feedbacks.filter(feedback_type='positive').count(),
        'negative': feedbacks.filter(feedback_type='negative').count(),
        'neutral': feedbacks.filter(feedback_type='neutral').count(),
    }
    
    context = {
        'agent': agent,
        'feedbacks': feedbacks[:10],  # Recent 10 feedbacks
        'feedback_summary': feedback_summary,
        'has_unpublished_changes': has_unpublished_changes,
        'change_summary': change_summary,
    }
    return render(request, 'dashboard/agent_publish.html', context)


@login_required
@require_http_methods(['POST'])
def regenerate_api_key(request, slug):
    """Rotate all agent API keys to a single new key."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    
    if agent.status != 'published':
        messages.error(request, 'Only published agents can regenerate API keys')
        return redirect('agent_detail', slug=agent.slug)
    
    # Generate new key
    new_key = agent.generate_api_key()
    
    # Store in session to show once (will be cleared after display)
    request.session[f'new_agent_api_key_{agent.id}'] = new_key
    request.session.save()
    
    messages.success(
        request,
        'All previous API keys were revoked. Save the new key now — you will not be able to see it again.',
    )

    return redirect('agent_detail', slug=agent.slug)


@login_required
@require_http_methods(['POST'])
def add_agent_api_key(request, slug):
    """Create an additional API key without revoking existing keys."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    if agent.status != 'published':
        messages.error(request, 'Only published agents can have API keys.')
        return redirect('agent_detail', slug=agent.slug)
    name = (request.POST.get('name') or '').strip() or None
    new_key = agent.add_api_key(name=name)
    request.session[f'new_agent_api_key_{agent.id}'] = new_key
    request.session.save()
    messages.success(request, 'New API key created. Save it now — you will not see it again.')
    return redirect('agent_detail', slug=agent.slug)


@login_required
@require_http_methods(['POST'])
def revoke_agent_api_key(request, slug, key_id):
    """Revoke one agent API key."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    key_row = get_object_or_404(AgentAPIKey, pk=key_id, agent=agent)
    key_row.revoke()
    if not agent.agent_api_keys.filter(is_active=True).exclude(
        api_key_hash__isnull=True
    ).exclude(api_key_hash='').exists():
        agent.api_key = None
        agent.api_key_hash = None
        agent.save(update_fields=['api_key', 'api_key_hash'])
    messages.success(request, 'That API key was revoked. Clients using it will stop working immediately.')
    return redirect('agent_detail', slug=agent.slug)


@login_required
def sync_models(request):
    """Manually trigger model sync."""
    try:
        sync_available_models.delay()
        messages.success(request, 'Model sync started in background. Models will be updated shortly.')
    except Exception as e:
        messages.error(request, f'Failed to start model sync: {str(e)}')
    
    return redirect('agent_create')


@login_required
def optimize_agent(request, slug):
    """Manually trigger agent optimization based on feedback."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    
    optimizer = FeedbackOptimizer(agent)
    result = optimizer.optimize_agent_instructions()
    
    if result.get('optimized'):
        messages.success(request, 'Agent instructions have been optimized based on feedback analysis!')
    else:
        messages.info(request, f"Optimization not applied: {result.get('reason', 'Unknown reason')}")
    
    return redirect('agent_detail', slug=agent.slug)


@login_required
def update_agent_model(request, slug):
    """Update the LLM model for an agent."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    
    # Prevent model changes for published agents
    if agent.status == 'published':
        messages.warning(request, 'Cannot change the LLM model for published agents. Please unpublish the agent first.')
        return redirect('agent_detail', slug=agent.slug)
    
    # Get available models with use cases serialized for template
    # Get available and compatible models (exclude incompatible ones)
    from django.db.models import Q
    available_models = AIModel.objects.filter(
        is_available=True
    ).exclude(
        metadata__is_compatible=False
    ).order_by('provider', 'model_name')
    
    # Serialize use_cases as JSON strings for template
    for model in available_models:
        if model.use_cases:
            model.use_cases_json = json.dumps(model.use_cases)
        else:
            model.use_cases_json = '[]'
    
    if request.method == 'POST':
        model_id = request.POST.get('model_id')
        if not model_id:
            messages.error(request, 'Please select a model.')
            context = {
                'agent': agent,
                'available_models': available_models,
            }
            return render(request, 'dashboard/update_model.html', context)
        
        try:
            new_model = AIModel.objects.get(id=model_id, is_available=True)
        except AIModel.DoesNotExist:
            messages.error(request, 'Selected model not found or not available.')
            context = {
                'agent': agent,
                'available_models': available_models,
            }
            return render(request, 'dashboard/update_model.html', context)
        
        # Update agent model
        old_model = agent.model
        agent.model = new_model
        
        # Update configuration with new model info
        if not agent.configuration:
            agent.configuration = {}
        
        agent.configuration['model_id'] = new_model.model_id
        agent.configuration['model_name'] = new_model.model_name
        agent.configuration['model_provider'] = new_model.provider
        
        agent.save()
        
        messages.success(
            request, 
            f'Model updated from {old_model.model_name if old_model else "None"} to {new_model.model_name}'
        )
        return redirect('agent_detail', slug=agent.slug)
    
    # GET request - show form
    context = {
        'agent': agent,
        'available_models': available_models,
    }
    return render(request, 'dashboard/update_model.html', context)


@login_required
# Removed @csrf_exempt for security - CSRF protection is important for file uploads
def upload_chat_file(request, slug):
    """Upload a file for chat conversation."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST method allowed'}, status=405)
    
    # Handle multiple files - check both 'file' and 'file_0', 'file_1', etc. patterns
    files = []
    
    # First, check for file_0, file_1, etc. pattern (used by frontend)
    for key in request.FILES.keys():
        if key.startswith('file_'):
            files.extend(request.FILES.getlist(key))
    
    # Also check for 'file' key (fallback for direct API calls)
    if not files and 'file' in request.FILES:
        files = request.FILES.getlist('file')
    
    if not files:
        return JsonResponse({'error': 'No file provided'}, status=400)
    
    # Validate file sizes (max 10MB each)
    max_size = 10 * 1024 * 1024  # 10MB
    uploaded_files = []
    
    try:
        for file in files:
            if file.size > max_size:
                return JsonResponse({'error': f'File "{file.name}" exceeds 10MB limit'}, status=400)
            
            # Generate unique filename
            file_ext = os.path.splitext(file.name)[1]
            unique_filename = f"{uuid.uuid4()}{file_ext}"
            file_path = f"chat_files/{agent.id}/{unique_filename}"
            
            # Save file
            saved_path = default_storage.save(file_path, ContentFile(file.read()))
            file_url = default_storage.url(saved_path)
            
            # Get file info
            file_info = {
                'file_name': file.name,
                'file_path': saved_path,
                'file_url': file_url,
                'file_size': file.size,
                'file_type': file.content_type or 'application/octet-stream',
                'file_id': str(uuid.uuid4())
            }
            uploaded_files.append(file_info)
        
        return JsonResponse({
            'success': True,
            'files': uploaded_files
        })
    except Exception as e:
        return JsonResponse({'error': f'Failed to upload file: {str(e)}'}, status=500)


@login_required
def mcp_server_create(request, slug):
    """Create a new MCP server for an agent."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    
    if request.method == 'POST':
        from .models import MCPServer
        import json
        
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        transport_type = request.POST.get('transport_type', 'stdio')
        command = request.POST.get('command', '').strip()
        args_text = request.POST.get('args', '').strip()
        url = request.POST.get('url', '').strip()
        headers_text = request.POST.get('headers', '{}').strip()
        auto_connect = request.POST.get('auto_connect') == 'on'
        
        # Validate required fields
        if not name:
            messages.error(request, 'MCP server name is required.')
            return _redirect_after_mcp(agent, request)
        
        # Validate transport-specific fields
        if transport_type == 'stdio' and not command:
            messages.error(request, 'Command is required for STDIO transport.')
            return _redirect_after_mcp(agent, request)
        elif transport_type in ['http', 'sse'] and not url:
            messages.error(request, 'URL is required for HTTP/SSE transport.')
            return _redirect_after_mcp(agent, request)
        
        # Parse JSON fields
        try:
            args = json.loads(args_text) if args_text else []
            if not isinstance(args, list):
                args = []
        except:
            args = []
        
        try:
            headers = json.loads(headers_text) if headers_text else {}
            if not isinstance(headers, dict):
                headers = {}
        except:
            headers = {}
        
        # For HTTP transport, use user API key for MCP (not agent key)
        # MCP operations should use user keys, not agent keys
        if transport_type in ['http', 'sse']:
            if 'Authorization' not in headers and 'X-API-Key' not in headers:
                # Get user's API key (prefer UserAPIKey, fallback to UserProfile)
                user_api_key = None
                try:
                    from .models import UserAPIKey
                    user_api_key_obj = UserAPIKey.objects.filter(
                        user=request.user, 
                        is_active=True
                    ).first()
                    # Note: Plaintext keys are not stored in database for security
                    # User must manually enter their API key in MCP server configuration
                    pass
                except:
                    pass
                
                # Fallback to UserProfile API key
                if not user_api_key:
                    try:
                        user_profile = request.user.profile
                        # Note: Plaintext keys are not stored in database for security
                        # User must manually enter their API key in MCP server configuration
                        pass
                    except:
                        pass
                
                if user_api_key:
                    headers['X-API-Key'] = user_api_key
                    messages.info(request, 'User API key automatically added to MCP server headers for authentication.')
                else:
                    messages.warning(request, 'No user API key found. Please generate a user API key for MCP authentication.')
        
        # Validate MCP server connection before saving
        try:
            from .mcp_client import MCPClient
            
            # Create a temporary client to test the connection
            test_client = MCPClient(
                server_name=name,
                transport_type=transport_type,
                command=command if transport_type == 'stdio' else None,
                args=args if transport_type == 'stdio' else [],
                url=url if transport_type in ['http', 'sse'] else None,
                headers=headers if transport_type in ['http', 'sse'] else {}
            )
            
            # Test connection
            if test_client.connect():
                # Connection successful - list tools to verify it's working
                tools = test_client.list_tools()
                test_client.disconnect()
                
                # Create MCP server
                mcp_server = MCPServer.objects.create(
                    agent=agent,
                    name=name,
                    description=description,
                    transport_type=transport_type,
                    command=command if transport_type == 'stdio' else '',
                    args=args,
                    url=url if transport_type in ['http', 'sse'] else '',
                    headers=headers,
                    auto_connect=auto_connect,
                    is_active=True
                )
                
                tool_count = len(tools) if tools else 0
                if tool_count > 0:
                    messages.success(
                        request, 
                        f'MCP server "{name}" created and validated successfully! Found {tool_count} tool(s).'
                    )
                else:
                    messages.success(
                        request, 
                        f'MCP server "{name}" created and validated successfully! (No tools available)'
                    )
            else:
                # Connection failed
                error_msg = f'Failed to connect to MCP server "{name}". Please check the configuration and ensure the server is running.'
                if transport_type == 'stdio':
                    error_msg += ' Verify the command and arguments are correct.'
                elif transport_type in ['http', 'sse']:
                    error_msg += ' Verify the URL is correct and the server is accessible. Check authentication headers if required.'
                messages.error(request, error_msg)
                return _redirect_after_mcp(agent, request)
                
        except ImportError:
            # MCP client not available - skip validation but warn user
            messages.warning(request, 'MCP client validation skipped (client not available). Creating server without validation.')
            try:
                mcp_server = MCPServer.objects.create(
                    agent=agent,
                    name=name,
                    description=description,
                    transport_type=transport_type,
                    command=command if transport_type == 'stdio' else '',
                    args=args,
                    url=url if transport_type in ['http', 'sse'] else '',
                    headers=headers,
                    auto_connect=auto_connect,
                    is_active=True
                )
                messages.success(request, f'MCP server "{name}" created (validation skipped).')
            except Exception as e:
                messages.error(request, f'Error creating MCP server: {str(e)}')
        except Exception as e:
            # Other errors during validation
            error_msg = f'Error validating MCP server connection: {str(e)}'
            messages.error(request, error_msg)
            logger = logging.getLogger(__name__)
            logger.error(f"Error validating MCP server: {str(e)}", exc_info=True)
            return _redirect_after_mcp(agent, request)
        
        return _redirect_after_mcp(agent, request)
    
    return _redirect_after_mcp(agent, request)


@login_required
def mcp_server_update(request, slug, mcp_server_id):
    """Update an MCP server configuration."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    from .models import MCPServer
    import json
    
    mcp_server = get_object_or_404(MCPServer, id=mcp_server_id, agent=agent)
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        transport_type = request.POST.get('transport_type', 'stdio')
        command = request.POST.get('command', '').strip()
        args_text = request.POST.get('args', '').strip()
        url = request.POST.get('url', '').strip()
        headers_text = request.POST.get('headers', '{}').strip()
        auto_connect = request.POST.get('auto_connect') == 'on'
        
        # Validate required fields
        if not name:
            messages.error(request, 'MCP server name is required.')
            return _redirect_after_mcp(agent, request)
        
        # Validate transport-specific fields
        if transport_type == 'stdio' and not command:
            messages.error(request, 'Command is required for STDIO transport.')
            return _redirect_after_mcp(agent, request)
        elif transport_type in ['http', 'sse'] and not url:
            messages.error(request, 'URL is required for HTTP/SSE transport.')
            return _redirect_after_mcp(agent, request)
        
        # Parse JSON fields
        try:
            args = json.loads(args_text) if args_text else []
            if not isinstance(args, list):
                args = []
        except:
            args = []
        
        try:
            headers = json.loads(headers_text) if headers_text else {}
            if not isinstance(headers, dict):
                headers = {}
        except:
            headers = {}
        
        # For HTTP transport, use user API key for MCP (not agent key)
        # MCP operations should use user keys, not agent keys
        if transport_type in ['http', 'sse']:
            if 'Authorization' not in headers and 'X-API-Key' not in headers:
                # Get user's API key (prefer UserAPIKey, fallback to UserProfile)
                user_api_key = None
                try:
                    from .models import UserAPIKey
                    user_api_key_obj = UserAPIKey.objects.filter(
                        user=request.user, 
                        is_active=True
                    ).first()
                    # Note: Plaintext keys are not stored in database for security
                    # User must manually enter their API key in MCP server configuration
                    pass
                except:
                    pass
                
                # Fallback to UserProfile API key
                if not user_api_key:
                    try:
                        user_profile = request.user.profile
                        # Note: Plaintext keys are not stored in database for security
                        # User must manually enter their API key in MCP server configuration
                        pass
                    except:
                        pass
                
                if user_api_key:
                    headers['X-API-Key'] = user_api_key
        
        # Update MCP server
        try:
            mcp_server.name = name
            mcp_server.description = description
            mcp_server.transport_type = transport_type
            mcp_server.command = command if transport_type == 'stdio' else ''
            mcp_server.args = args
            mcp_server.url = url if transport_type in ['http', 'sse'] else ''
            mcp_server.headers = headers
            mcp_server.auto_connect = auto_connect
            mcp_server.save()
            
            messages.success(request, f'MCP server "{name}" updated successfully!')
        except Exception as e:
            messages.error(request, f'Error updating MCP server: {str(e)}')
        
        return _redirect_after_mcp(agent, request)
    
    return _redirect_after_mcp(agent, request)


@login_required
def mcp_server_toggle(request, slug, mcp_server_id):
    """Toggle MCP server active status."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    from .models import MCPServer
    
    mcp_server = get_object_or_404(MCPServer, id=mcp_server_id, agent=agent)
    mcp_server.is_active = not mcp_server.is_active
    mcp_server.save()
    
    status = 'enabled' if mcp_server.is_active else 'disabled'
    messages.success(request, f'MCP server "{mcp_server.name}" {status}.')
    
    return _redirect_after_mcp(agent, request)


@login_required
def mcp_server_delete(request, slug, mcp_server_id):
    """Delete an MCP server."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    from .models import MCPServer
    
    mcp_server = get_object_or_404(MCPServer, id=mcp_server_id, agent=agent)
    server_name = mcp_server.name
    mcp_server.delete()
    
    messages.success(request, f'MCP server "{server_name}" deleted successfully.')
    
    return _redirect_after_mcp(agent, request)


@login_required
def agent_delete(request, slug):
    """Delete an agent with confirmation."""
    # #region agent log
    _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H4", "location": "views_dashboard.py:1788", "message": "agent_delete entry", "data": {"slug": slug, "user": request.user.username, "user_id": request.user.id}, "timestamp": int(__import__('time').time() * 1000)})
    # #endregion
    try:
        agent = Agent.objects.get(slug=slug, user=request.user)
        # #region agent log
        _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H4", "location": "views_dashboard.py:1794", "message": "agent found for deletion", "data": {"agent_id": agent.id, "agent_name": agent.name, "is_owner": True}, "timestamp": int(__import__('time').time() * 1000)})
        # #endregion
    except Agent.DoesNotExist:
        # Check if agent exists but user doesn't own it
        try:
            agent_check = Agent.objects.get(slug=slug)
            # #region agent log
            _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H4", "location": "views_dashboard.py:1802", "message": "agent exists but user not owner", "data": {"slug": slug, "agent_owner_id": agent_check.user.id, "request_user_id": request.user.id, "is_owner": False}, "timestamp": int(__import__('time').time() * 1000)})
            # #endregion
        except Agent.DoesNotExist:
            # #region agent log
            _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H4", "location": "views_dashboard.py:1807", "message": "agent not found", "data": {"slug": slug}, "timestamp": int(__import__('time').time() * 1000)})
            # #endregion
        from django.http import Http404
        raise Http404("Agent not found")
    
    if request.method == 'POST':
        # Verify confirmation
        confirm = request.POST.get('confirm', '').strip().lower()
        agent_name_posted = request.POST.get('agent_name', '').strip()
        
        # #region agent log
        _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H4", "location": "views_dashboard.py:agent_delete:POST", "message": "deletion POST received", "data": {"slug": slug, "confirm": confirm, "agent_name_posted": agent_name_posted, "agent_name_actual": agent.name, "match": agent_name_posted == agent.name}, "timestamp": int(__import__('time').time() * 1000)})
        # #endregion
        
        # Double verification: user must type agent name and confirm
        if confirm != 'delete' or agent_name_posted != agent.name:
            # #region agent log
            _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H4", "location": "views_dashboard.py:agent_delete:POST", "message": "deletion validation failed", "data": {"confirm_match": confirm == 'delete', "name_match": agent_name_posted == agent.name}, "timestamp": int(__import__('time').time() * 1000)})
            # #endregion
            messages.error(request, 'Deletion not confirmed. Please type "delete" and the agent name correctly.')
            return redirect('agent_delete', slug=agent.slug)
        
        # Store agent name for success message
        agent_name_for_msg = agent.name
        
        # #region agent log
        _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H4", "location": "views_dashboard.py:agent_delete:POST", "message": "deletion validation passed, proceeding with delete", "data": {"agent_id": agent.id, "agent_name": agent_name_for_msg}, "timestamp": int(__import__('time').time() * 1000)})
        # #endregion
        
        # Clean up related data
        try:
            # Clear RAG vectors if indexed
            from .rag_service import get_rag_service
            from django.conf import settings
            
            try:
                embedding_provider = 'bedrock'
                if agent.model and agent.model.provider == 'ollama':
                    embedding_provider = 'ollama'
                
                vector_store_backend = getattr(settings, 'RAG_VECTOR_STORE_BACKEND', 'qdrant')
                if agent.configuration and 'rag_vector_store_backend' in agent.configuration:
                    vector_store_backend = agent.configuration['rag_vector_store_backend']
                
                rag_service = get_rag_service(
                    embedding_provider=embedding_provider,
                    vector_store_backend=vector_store_backend
                )
                rag_service.vector_store.clear_agent_vectors(agent.id)
            except Exception as e:
                # Log but don't fail deletion
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Error clearing RAG vectors during deletion: {str(e)}")
            
            # Delete the agent (cascade will handle related objects)
            agent.delete()
            
            # #region agent log
            _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H4", "location": "views_dashboard.py:agent_delete:POST", "message": "agent deleted successfully", "data": {"agent_name": agent_name_for_msg}, "timestamp": int(__import__('time').time() * 1000)})
            # #endregion
            
            messages.success(request, f'Agent "{agent_name_for_msg}" has been deleted successfully.')
            return redirect('agents_list')
        except Exception as e:
            # #region agent log
            _write_debug_log({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1,H4", "location": "views_dashboard.py:agent_delete:POST", "message": "error during deletion", "data": {"error": str(e)}, "timestamp": int(__import__('time').time() * 1000)})
            # #endregion
            logger.error(f"Error deleting agent {slug}: {str(e)}", exc_info=True)
            messages.error(request, f'Error deleting agent: {str(e)}')
            return redirect('agent_delete', slug=slug)
    
    # GET request - show confirmation page
    context = {
        'agent': agent,
    }
    return render(request, 'dashboard/agent_delete.html', context)


@login_required
def user_profile(request):
    """User profile page with API key management."""
    from .models import Agent
    
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    
    # Clear the new_api_key from session after displaying it once
    # Use pop() to get and remove in one operation - ensures it's only shown once
    new_api_key = request.session.pop('new_api_key', None)
    new_api_key_id = request.session.pop('new_api_key_id', None)
    
    # Save session changes (removing the key)
    if new_api_key:
        request.session.save()
    
    # Get all active API keys for the user
    api_keys = request.user.api_keys.filter(is_active=True).order_by('-created_at')
    
    # Get all published agents with their API keys (own + shared)
    from django.db.models import Q
    from .models import AgentShare
    
    # Build query using Q objects to avoid queryset combination issues
    q = Q(
        user=request.user,
        status='published',
        api_key_hash__isnull=False
    )
    
    # Add shared published agents if user has an email
    if request.user.email:
        q |= Q(
            shares__email=request.user.email,
            shares__is_accepted=True,
            shares__accepted_by=request.user,
            status='published',
            api_key_hash__isnull=False
        )
    
    # Get combined queryset
    published_agents = Agent.objects.filter(q).distinct().order_by('-published_at')
    
    # Get social media connectors
    from connectors.models import Connector, ConnectorType
    social_connectors = {}
    for platform in ['linkedin', 'facebook', 'twitter', 'instagram']:
        connector_type = getattr(ConnectorType, platform.upper(), None)
        if connector_type:
            connector = Connector.objects.filter(
                user=request.user,
                connector_type=connector_type
            ).first()
            social_connectors[platform] = connector
    
    context = {
        'profile': profile,
        'api_keys': api_keys,
        'has_api_key': api_keys.exists() or (profile.api_key_hash is not None),
        'api_key_preview': f"{profile.api_key_hash[:8]}...{profile.api_key_hash[-4:]}" if profile.api_key_hash else None,
        'published_agents': published_agents,
        'social_connectors': social_connectors,
    }
    
    # Add new_api_key to context if it exists (already popped from session, so it won't persist)
    if new_api_key:
        context['new_api_key'] = new_api_key
        context['new_api_key_id'] = new_api_key_id
        # DO NOT put it back in session - we want it to disappear after this view
    
    return render(request, 'dashboard/user_profile.html', context)


@login_required
def generate_api_key(request):
    """Generate a new API key for the user."""
    from .models import UserAPIKey
    
    if request.method == 'POST':
        key_name = request.POST.get('key_name', '').strip()
        
        # Create new API key
        user_api_key = UserAPIKey.objects.create(
            user=request.user,
            name=key_name or None
        )
        api_key = user_api_key.generate_api_key()
        
        # Store in session to show once (will be cleared after display)
        request.session['new_api_key'] = api_key
        request.session['new_api_key_id'] = user_api_key.id
        
        messages.success(request, 'API key generated successfully! Save it now - you won\'t be able to see it again.')
        return redirect('user_profile')
    
    return redirect('user_profile')


@login_required
def revoke_api_key(request):
    """Revoke a specific API key."""
    from .models import UserAPIKey
    
    if request.method == 'POST':
        key_id = request.POST.get('key_id')
        
        if not key_id:
            messages.error(request, 'No API key ID provided.')
            return redirect('user_profile')
        
        try:
            user_api_key = UserAPIKey.objects.get(id=key_id, user=request.user, is_active=True)
            user_api_key.revoke()
            messages.success(request, f'API key "{user_api_key.name or f"Key {user_api_key.id}"}" revoked successfully.')
        except UserAPIKey.DoesNotExist:
            messages.error(request, 'API key not found or already revoked.')
        
        return redirect('user_profile')
    
    return redirect('user_profile')


@login_required
def validate_connector_ajax(request, connector_id):
    """AJAX endpoint to validate connector status without page refresh."""
    logger = logging.getLogger(__name__)
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST method allowed'}, status=405)
    
    try:
        from connectors.models import Connector
        from connectors.validator import ConnectorValidator
        
        connector = Connector.objects.get(id=connector_id, user=request.user)
        result = ConnectorValidator.validate_and_update_status(connector)
        
        # Reload connector to get updated metadata
        connector.refresh_from_db()
        
        # Format validation details for response
        validation_info = None
        if connector.metadata.get('last_validation'):
            validation = connector.metadata['last_validation']
            validation_info = {
                'timestamp': validation.get('timestamp', ''),
                'valid': validation.get('valid', False),
                'message': validation.get('message', ''),
                'details': validation.get('details', {}),
            }
        
        return JsonResponse({
            'success': True,
            'valid': result['valid'],
            'status': result['status'],
            'message': result['message'],
            'connector_status': connector.status,
            'validation': validation_info,
        })
    except Connector.DoesNotExist:
        return JsonResponse({'error': 'Connector not found'}, status=404)
    except Exception as e:
        logger.error(f"Error validating connector: {str(e)}", exc_info=True)
        return JsonResponse({'error': f'Validation failed: {str(e)}'}, status=500)


@login_required
@require_http_methods(["POST"])
def toggle_tool_ajax(request, slug):
    """AJAX endpoint to toggle tool enable/disable without page refresh - saves to database."""
    logger = logging.getLogger(__name__)
    
    try:
        agent = get_object_or_404(Agent, slug=slug, user=request.user)
        
        # Get parameters from JSON body or form data
        if request.content_type == 'application/json':
            import json
            data = json.loads(request.body)
            tool_name = data.get('tool_name')
            enabled = data.get('enabled', False)
        else:
            tool_name = request.POST.get('tool_name')
            enabled = request.POST.get('enabled') == 'true' or request.POST.get('enabled') == 'True'
        
        if not tool_name:
            return JsonResponse({'error': 'tool_name parameter is required'}, status=400)
        
        # Save to database (AgentTool model)
        from .models import AgentTool
        
        # Get or create AgentTool record
        agent_tool, created = AgentTool.objects.get_or_create(
            agent=agent,
            tool_name=tool_name,
            defaults={'is_enabled': enabled}
        )
        
        # Update if it already exists
        if not created:
            agent_tool.is_enabled = enabled
            agent_tool.save(update_fields=['is_enabled', 'updated_at'])
        
        # Get current enabled/disabled tools for response
        agent_tools = AgentTool.objects.filter(agent=agent)
        enabled_tools = list(agent_tools.filter(is_enabled=True).values_list('tool_name', flat=True))
        disabled_tools = list(agent_tools.filter(is_enabled=False).values_list('tool_name', flat=True))
        
        return JsonResponse({
            'success': True,
            'tool_name': tool_name,
            'enabled': enabled,
            'message': f'Tool {tool_name} {"enabled" if enabled else "disabled"} successfully',
            'enabled_tools': enabled_tools,
            'disabled_tools': disabled_tools
        })
        
    except Agent.DoesNotExist:
        return JsonResponse({'error': 'Agent not found'}, status=404)
    except Exception as e:
        logger.error(f"Error toggling tool: {str(e)}", exc_info=True)
        return JsonResponse({'error': f'Failed to toggle tool: {str(e)}'}, status=500)


@login_required
def share_agent(request, slug):
    """Share agent with another user via email."""
    logger = logging.getLogger(__name__)
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    
    # Only published agents can be shared
    if agent.status != 'published':
        messages.error(request, 'Only published agents can be shared. Please publish your agent first.')
        return redirect('agent_detail', slug=agent.slug)
    
    if request.method == 'POST':
        action = request.POST.get('action', '')
        
        email = request.POST.get('email', '').strip()
        message = request.POST.get('message', '').strip()
        expires_days = request.POST.get('expires_days', '')
        
        if not email:
            messages.error(request, 'Email address is required.')
            return redirect('agent_detail', slug=agent.slug)
        
        # Validate email format
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError
        try:
            validate_email(email)
        except ValidationError:
            messages.error(request, 'Invalid email address.')
            return redirect('agent_detail', slug=agent.slug)
        
        # Check if already shared with this email
        from .models import AgentShare
        existing_share = AgentShare.objects.filter(agent=agent, email=email).first()
        
        if existing_share and existing_share.is_valid():
            messages.info(request, f'Agent is already shared with {email}.')
            return redirect('agent_detail', slug=agent.slug)
        
        # Create share token
        import secrets
        token = secrets.token_urlsafe(32)
        
        # Calculate expiration date if provided
        expires_at = None
        if expires_days:
            try:
                days = int(expires_days)
                if days > 0:
                    from datetime import timedelta
                    expires_at = timezone.now() + timedelta(days=days)
            except ValueError:
                pass
        
        # Create or update share
        if existing_share:
            existing_share.token = token
            existing_share.message = message
            existing_share.expires_at = expires_at
            existing_share.is_accepted = False
            existing_share.accepted_at = None
            existing_share.accepted_by = None
            existing_share.shared_by = request.user
            existing_share.save()
            share = existing_share
        else:
            share = AgentShare.objects.create(
                agent=agent,
                shared_by=request.user,
                email=email,
                token=token,
                message=message,
                expires_at=expires_at
            )
        
        # Send email via Celery task (async)
        try:
            from .tasks import send_agent_share_email
            
            # Debug: Check broker connection and task registration
            try:
                from celery import current_app
                from kombu import Connection
                from django.conf import settings
                
                # Check broker connection
                broker_url = getattr(settings, 'CELERY_BROKER_URL', 'redis://localhost:6379/0')
                logger.info(f"[Agent Share] Broker URL: {broker_url}")
                
                try:
                    conn = Connection(broker_url)
                    conn.connect()
                    conn.release()
                    logger.info(f"[Agent Share] Broker connection successful")
                except Exception as broker_error:
                    logger.error(f"[Agent Share] Broker connection failed: {str(broker_error)}")
                
                # Check task registration
                task_name = send_agent_share_email.name
                registered_tasks = list(current_app.tasks.keys())
                is_registered = task_name in registered_tasks
                
                logger.info(f"[Agent Share] Task name: {task_name}")
                logger.info(f"[Agent Share] Task registered: {is_registered}")
                logger.info(f"[Agent Share] Total registered tasks: {len(registered_tasks)}")
                if not is_registered:
                    logger.warning(f"[Agent Share] Task '{task_name}' not found in registered tasks!")
                    logger.warning(f"[Agent Share] Sample registered tasks: {list(registered_tasks)[:10]}")
            except Exception as debug_error:
                logger.warning(f"[Agent Share] Debug check failed (non-critical): {str(debug_error)}")
            
            # Queue email sending task
            logger.info(f"[Agent Share] Attempting to queue task for share ID {share.id}")
            task = send_agent_share_email.delay(share.id)
            logger.info(f"[Agent Share] Queued agent share email task (ID: {task.id}) for share ID {share.id}")
            logger.info(f"[Agent Share] Task state: {task.state}")
            
            messages.success(request, f'Agent shared successfully! An email will be sent to {email} shortly.')
        except Exception as e:
            logger.error(f"[Agent Share] Error queueing share email task: {str(e)}", exc_info=True)
            import traceback
            logger.error(f"[Agent Share] Traceback: {traceback.format_exc()}")
            messages.warning(request, f'Agent share created, but failed to queue email: {str(e)}')
        
        return redirect('agent_detail', slug=agent.slug)
    
    # GET request - show share form
    # Get public share (if exists)
    from .models import AgentPublicShare, MCPServer
    public_share = AgentPublicShare.objects.filter(agent=agent, is_active=True).first()
    
    # Check if agent has MCP servers enabled
    has_mcp_servers = MCPServer.objects.filter(agent=agent, is_active=True).exists()
    
    context = {
        'agent': agent,
        'public_share': public_share,
        'has_mcp_servers': has_mcp_servers,
    }
    return render(request, 'dashboard/share_agent.html', context)


@login_required
@require_http_methods(["POST"])
def resend_agent_share_email(request, share_id):
    """Resend email for an agent share (AJAX endpoint)."""
    logger = logging.getLogger(__name__)
    from .models import AgentShare
    from .tasks import send_agent_share_email
    from django.http import JsonResponse
    
    try:
        share = get_object_or_404(AgentShare, id=share_id)
        
        # Check if user owns the agent
        if share.agent.user != request.user:
            return JsonResponse({
                'success': False,
                'error': 'You do not have permission to resend this share email.'
            }, status=403)
        
        # Check if share is still valid
        if share.is_accepted:
            return JsonResponse({
                'success': False,
                'error': 'This share has already been accepted.'
            }, status=400)
        
        if share.is_expired():
            return JsonResponse({
                'success': False,
                'error': 'This share has expired. Please create a new share.'
            }, status=400)
        
        # Queue email sending task
        try:
            task = send_agent_share_email.delay(share.id)
            logger.info(f"Resent agent share email for share ID {share.id}, task ID: {task.id}")
            return JsonResponse({
                'success': True,
                'message': f'Share email resent successfully to {share.email}.',
                'share_id': share_id
            })
        except Exception as e:
            logger.error(f"Error resending share email: {str(e)}", exc_info=True)
            return JsonResponse({
                'success': False,
                'error': f'Failed to resend email: {str(e)}'
            }, status=500)
        
    except AgentShare.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Share not found.'
        }, status=404)


@login_required
@require_http_methods(["POST"])
def withdraw_agent_share(request, share_id):
    """Withdraw/delete an agent share (AJAX endpoint)."""
    logger = logging.getLogger(__name__)
    from .models import AgentShare
    from django.http import JsonResponse
    
    try:
        share = get_object_or_404(AgentShare, id=share_id)
        agent_id = share.agent.id
        email = share.email
        
        # Check if user owns the agent
        if share.agent.user != request.user:
            return JsonResponse({
                'success': False,
                'error': 'You do not have permission to withdraw this share.'
            }, status=403)
        
        # Revoke access by setting is_accepted=False and clearing accepted_by
        # This allows the owner to revoke access even after acceptance
        if share.is_accepted:
            share.is_accepted = False
            share.accepted_by = None
            share.accepted_at = None
            share.save()
            logger.info(f"Revoked access for agent share ID {share_id} (was accepted by {email})")
            message = f'Access revoked for {email}. They no longer have access to the agent "{share.agent.name}".'
        else:
            # If not accepted yet, delete the share invitation
            share.delete()
            logger.info(f"Deleted pending agent share ID {share_id} for email {email}")
            message = f'Share invitation to {email} has been withdrawn.'
        
        return JsonResponse({
            'success': True,
            'message': message,
            'share_id': share_id
        })
        
    except AgentShare.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Share not found.'
        }, status=404)
    except Exception as e:
        logger.error(f"Error withdrawing agent share: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to withdraw share: {str(e)}'
        }, status=500)


@login_required
def generate_public_share_url(request, slug):
    """Generate a public share URL for a published agent."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    
    # Only published agents can have public share URLs
    if agent.status != 'published':
        messages.error(request, 'Only published agents can have public share URLs. Please publish your agent first.')
        return redirect('agent_detail', slug=agent.slug)
    
    # Check if agent has MCP servers enabled - block public sharing for security
    from .models import MCPServer
    has_mcp_servers = MCPServer.objects.filter(agent=agent, is_active=True).exists()
    if has_mcp_servers:
        messages.error(request, 'Agents with MCP servers enabled cannot be shared publicly due to private data security concerns. MCP servers may have access to sensitive tools and data.')
        return redirect('share_agent', slug=agent.slug)
    
    if request.method == 'POST':
        expires_days = request.POST.get('expires_days', '').strip()
        
        # Calculate expiration date if provided
        expires_at = None
        if expires_days:
            try:
                days = int(expires_days)
                if days > 0:
                    from datetime import timedelta
                    expires_at = timezone.now() + timedelta(days=days)
            except ValueError:
                pass
        
        # Check if there's already an active public share
        existing_share = AgentPublicShare.objects.filter(
            agent=agent,
            is_active=True
        ).first()
        
        if existing_share and existing_share.is_valid():
            # Update expiration if provided
            if expires_at:
                existing_share.expires_at = expires_at
                existing_share.save(update_fields=['expires_at'])
            messages.info(request, 'Public share URL already exists. Use the existing URL or deactivate it first.')
        else:
            # Create new public share
            import secrets
            token = secrets.token_urlsafe(32)
            
            # Deactivate any existing shares
            AgentPublicShare.objects.filter(agent=agent, is_active=True).update(is_active=False)
            
            public_share = AgentPublicShare.objects.create(
                agent=agent,
                shared_by=request.user,
                token=token,
                expires_at=expires_at,
                is_active=True
            )
            messages.success(request, 'Public share URL generated successfully!')
        
        return redirect('agent_detail', slug=agent.slug)
    
    # GET request - show form
    existing_share = AgentPublicShare.objects.filter(
        agent=agent,
        is_active=True
    ).first()
    
    context = {
        'agent': agent,
        'existing_share': existing_share,
    }
    return render(request, 'dashboard/generate_public_share.html', context)


@login_required
@require_http_methods(["POST"])
def deactivate_public_share(request, slug):
    """Deactivate a public share URL."""
    agent = get_object_or_404(Agent, slug=slug, user=request.user)
    
    # Check if user owns the agent
    if agent.user != request.user:
        return JsonResponse({
            'success': False,
            'error': 'You do not have permission to deactivate this share.'
        }, status=403)
    
    try:
        public_share = AgentPublicShare.objects.filter(
            agent=agent,
            is_active=True
        ).first()
        
        if public_share:
            public_share.is_active = False
            public_share.save(update_fields=['is_active'])
            return JsonResponse({
                'success': True,
                'message': 'Public share URL deactivated successfully.'
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'No active public share found.'
            })
    except Exception as e:
        logger.error(f"Error deactivating public share: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to deactivate public share: {str(e)}'
        }, status=500)


def access_public_share(request, token):
    """Access an agent via public share URL."""
    try:
        public_share = AgentPublicShare.objects.get(token=token)
        
        # Check if share is valid
        if not public_share.is_valid():
            if public_share.is_expired():
                messages.error(request, 'This share link has expired.')
            elif not public_share.is_active:
                messages.error(request, 'This share link has been deactivated.')
            return redirect('home')
        
        # Check if agent is still published
        if public_share.agent.status != 'published':
            messages.error(request, 'This agent is no longer published.')
            return redirect('home')
        
        # Increment access count
        public_share.increment_access()
        
        # If user is authenticated, redirect to chat with agent
        if request.user.is_authenticated:
            from django.urls import reverse
            return redirect(reverse('agent_chat', kwargs={'slug': public_share.agent.slug}))
        
        # If not authenticated, store agent_id in session and redirect to login
        request.session['public_share_agent_id'] = public_share.agent.id
        request.session['public_share_token'] = token
        messages.info(request, f'Please log in to access the agent: {public_share.agent.name}')
        from django.urls import reverse
        return redirect(reverse('login'))
        
    except AgentPublicShare.DoesNotExist:
        messages.error(request, 'Invalid share link.')
        return redirect('home')


def public_agents_list(request):
    """List all publicly shared agents."""
    from django.core.paginator import Paginator
    
    # Get search query
    search_query = request.GET.get('search', '').strip()
    
    # Get all agents with active public shares that are published
    public_shares = AgentPublicShare.objects.filter(
        is_active=True
    ).select_related('agent', 'agent__model', 'shared_by').prefetch_related('agent__public_shares')
    
    # Filter to only valid shares (not expired) and published agents
    valid_public_agents = []
    for share in public_shares:
        if share.is_valid() and share.agent.status == 'published':
            # Avoid duplicates - if agent already in list, skip
            if not any(agent['id'] == share.agent.id for agent in valid_public_agents):
                # Apply search filter if provided
                if search_query:
                    search_lower = search_query.lower()
                    name_match = share.agent.name.lower() if share.agent.name else ''
                    desc_match = share.agent.description.lower() if share.agent.description else ''
                    owner_match = share.shared_by.username.lower() if share.shared_by.username else ''
                    email_match = share.shared_by.email.lower() if share.shared_by.email else ''
                    
                    # Check if search query matches name, description, owner username, or email
                    if (search_lower not in name_match and 
                        search_lower not in desc_match and 
                        search_lower not in owner_match and
                        search_lower not in email_match):
                        continue  # Skip this agent if it doesn't match search
                
                # Get like/dislike counts
                like_count = AgentLike.objects.filter(agent=share.agent, feedback_type='like').count()
                dislike_count = AgentLike.objects.filter(agent=share.agent, feedback_type='dislike').count()
                
                # Check if current user has liked/disliked this agent
                user_feedback = None
                if request.user.is_authenticated:
                    user_feedback_obj = AgentLike.objects.filter(agent=share.agent, user=request.user).first()
                    if user_feedback_obj:
                        user_feedback = user_feedback_obj.feedback_type
                
                # Check if user has connected social media accounts
                from connectors.models import Connector, ConnectorType
                social_connections = {
                    'linkedin': False,
                    'facebook': False,
                    'twitter': False,
                    'instagram': False,
                }
                if request.user.is_authenticated:
                    for platform in ['linkedin', 'facebook', 'twitter', 'instagram']:
                        connector_type = getattr(ConnectorType, platform.upper(), None)
                        if connector_type:
                            connector = Connector.objects.filter(
                                user=request.user,
                                connector_type=connector_type,
                                status='connected'
                            ).first()
                            social_connections[platform] = connector is not None and connector.is_token_valid()
                
                valid_public_agents.append({
                    'id': share.agent.id,
                    'slug': share.agent.slug,  # URL-friendly identifier
                    'name': share.agent.name,
                    'description': share.agent.description,
                    'agent_type': share.agent.get_agent_type_display(),
                    'model': share.agent.model,
                    'created_at': share.agent.created_at,
                    'shared_by': share.shared_by,
                    'shared_by_username': share.shared_by.username,
                    'access_count': share.access_count,
                    'last_accessed_at': share.last_accessed_at,
                    'like_count': like_count,
                    'dislike_count': dislike_count,
                    'user_feedback': user_feedback,
                    'social_connections': social_connections if request.user.is_authenticated else {},
                    'agent': share.agent,  # Full agent object for template
                })
    
    # Sort by access count (most popular first), then by creation date
    valid_public_agents.sort(key=lambda x: (x['access_count'], x['created_at']), reverse=True)
    
    # Pagination
    paginator = Paginator(valid_public_agents, 50)  # 50 agents per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'public_agents': page_obj,
        'total_count': len(valid_public_agents),
        'search_query': search_query,
    }
    
    return render(request, 'dashboard/public_agents_list.html', context)


@login_required
@require_http_methods(["POST"])
def submit_agent_like(request, slug):
    """Submit like/dislike feedback for a public agent."""
    try:
        import json
        data = json.loads(request.body)
        feedback_type = data.get('feedback_type')  # 'like' or 'dislike'
        
        if feedback_type not in ['like', 'dislike']:
            return JsonResponse({
                'success': False,
                'error': 'feedback_type must be "like" or "dislike"'
            }, status=400)
        
        # Get agent - must be published
        agent = get_object_or_404(Agent, slug=slug, status='published')
        
        # Check if agent has active public share
        public_share = AgentPublicShare.objects.filter(
            agent=agent,
            is_active=True
        ).first()
        
        if not public_share or not public_share.is_valid():
            return JsonResponse({
                'success': False,
                'error': 'Agent is not publicly available'
            }, status=404)
        
        # Get or create user's feedback
        agent_like, created = AgentLike.objects.get_or_create(
            agent=agent,
            user=request.user,
            defaults={'feedback_type': feedback_type}
        )
        
        # Update if already exists
        if not created:
            if agent_like.feedback_type == feedback_type:
                # User clicked same button - remove feedback
                agent_like.delete()
                like_count = AgentLike.objects.filter(agent=agent, feedback_type='like').count()
                dislike_count = AgentLike.objects.filter(agent=agent, feedback_type='dislike').count()
                return JsonResponse({
                    'success': True,
                    'message': 'Feedback removed',
                    'like_count': like_count,
                    'dislike_count': dislike_count,
                    'user_feedback': None,
                    'removed': True
                })
            else:
                # User changed feedback type
                agent_like.feedback_type = feedback_type
                agent_like.save(update_fields=['feedback_type', 'updated_at'])
        
        # Get updated counts
        like_count = AgentLike.objects.filter(agent=agent, feedback_type='like').count()
        dislike_count = AgentLike.objects.filter(agent=agent, feedback_type='dislike').count()
        
        return JsonResponse({
            'success': True,
            'message': f'Thank you for your {feedback_type}!',
            'like_count': like_count,
            'dislike_count': dislike_count,
            'user_feedback': feedback_type
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        logger.error(f"Error submitting agent like: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to submit feedback: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["GET"])
def social_media_oauth(request, slug, platform):
    """Initiate OAuth flow for social media publishing."""
    from connectors.models import Connector, ConnectorType
    from connectors.social_media_oauth import (
        LinkedInPublishOAuth, FacebookPublishOAuth, 
        TwitterPublishOAuth, InstagramPublishOAuth
    )
    from django.urls import reverse
    from django.core.cache import cache
    
    agent = get_object_or_404(Agent, slug=slug, status='published')
    
    # Check if agent has public share
    public_share = AgentPublicShare.objects.filter(agent=agent, is_active=True).first()
    if not public_share or not public_share.is_valid():
        messages.error(request, 'Agent must be publicly shared to publish on social media.')
        return redirect('public_agents_list')
    
    # Get or create connector for this platform
    connector_type_map = {
        'linkedin': ConnectorType.LINKEDIN,
        'facebook': ConnectorType.FACEBOOK,
        'twitter': ConnectorType.TWITTER,
        'instagram': ConnectorType.INSTAGRAM,
    }
    
    if platform not in connector_type_map:
        messages.error(request, 'Invalid social media platform.')
        return redirect('public_agents_list')
    
    connector_type = connector_type_map[platform]
    
    # Get or create connector for storing user's access tokens
    # OAuth credentials (client_id/client_secret) are managed by admin
    connector, created = Connector.objects.get_or_create(
        user=request.user,
        connector_type=connector_type,
        defaults={
            'name': f'{platform.capitalize()} Account',
            'base_url': f'https://{platform}.com',
            'status': 'disconnected'
        }
    )
    
    # Build redirect URI - use the new URL format WITHOUT agent_id
    # The agent_id is now passed via the OAuth state parameter
    redirect_uri = request.build_absolute_uri(
        reverse('social_media_oauth_callback', kwargs={'platform': platform})
    )
    
    # Get authorization URL based on platform
    # OAuth credentials are fetched from admin-managed config
    oauth_classes = {
        'linkedin': LinkedInPublishOAuth,
        'facebook': FacebookPublishOAuth,
        'twitter': TwitterPublishOAuth,
        'instagram': InstagramPublishOAuth,
    }
    
    oauth_class = oauth_classes[platform]
    try:
        auth_url, state = oauth_class.get_authorization_url(request, redirect_uri)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('public_agents_list')
    
    # Store agent_id and connector_id in cache for callback
    cache.set(f'social_publish_{state}', {
        'agent_id': agent.id,  # Store numeric ID for callback lookup
        'connector_id': connector.id,
        'user_id': request.user.id,
    }, timeout=600)
    
    return redirect(auth_url)


@login_required
@require_http_methods(["GET"])
def social_media_oauth_callback(request, platform):
    """Handle OAuth callback and publish post.
    
    Note: agent_id is NO LONGER in the URL. It's retrieved from cache using state.
    This allows OAuth providers to use a single fixed callback URL.
    """
    from connectors.models import Connector, ConnectorType
    from connectors.social_media_oauth import (
        LinkedInPublishOAuth, FacebookPublishOAuth, 
        TwitterPublishOAuth, InstagramPublishOAuth
    )
    from django.core.cache import cache
    from django.urls import reverse
    from datetime import timedelta
    
    code = request.GET.get('code')
    state = request.GET.get('state')
    error = request.GET.get('error')
    
    if error:
        messages.error(request, f'OAuth error: {error}')
        return redirect('public_agents_list')
    
    if not code or not state:
        messages.error(request, 'Missing OAuth parameters.')
        return redirect('public_agents_list')
    
    # Get stored data - agent_id is stored here, NOT in the URL
    stored_data = cache.get(f'social_publish_{state}')
    if not stored_data or stored_data['user_id'] != request.user.id:
        messages.error(request, 'Invalid OAuth state.')
        return redirect('public_agents_list')
    
    agent = get_object_or_404(Agent, id=stored_data['agent_id'])
    connector = get_object_or_404(Connector, id=stored_data['connector_id'], user=request.user)
    
    # Build redirect URI - must match what was used in the authorization request
    redirect_uri = request.build_absolute_uri(
        reverse('social_media_oauth_callback', kwargs={'platform': platform})
    )
    
    # Exchange code for token
    oauth_classes = {
        'linkedin': LinkedInPublishOAuth,
        'facebook': FacebookPublishOAuth,
        'twitter': TwitterPublishOAuth,
        'instagram': InstagramPublishOAuth,
    }
    
    oauth_class = oauth_classes[platform]
    
    try:
        # Get code_verifier for Twitter
        code_verifier = None
        if platform == 'twitter':
            state_data = cache.get(f'twitter_publish_state_{state}')
            if state_data:
                code_verifier = state_data.get('code_verifier')
        
        # Exchange code for token - credentials are fetched from admin-managed config
        if platform == 'twitter':
            access_token, refresh_token, expires_in = oauth_class.exchange_code_for_token(
                code, redirect_uri, code_verifier
            )
        else:
            access_token, refresh_token, expires_in = oauth_class.exchange_code_for_token(
                code, redirect_uri
            )
        
        # Update connector with tokens
        connector.access_token = access_token
        if refresh_token:
            connector.refresh_token = refresh_token
        if expires_in:
            connector.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
        connector.status = 'connected'
        connector.connected_at = timezone.now()
        connector.save()
        
        # Build share content
        agent_url = f"{request.scheme}://{request.get_host()}{reverse('home')}?agent_id={agent.id}"
        share_text = f"🤖 Check out this amazing AI agent: {agent.name}\n\n"
        if agent.description:
            share_text += f"{agent.description[:200]}\n\n"
        share_text += f"Try it now: {agent_url}\n\n"
        share_text += "#AI #Agent #Automation #Tech"
        
        # Publish post
        try:
            if platform == 'linkedin':
                result = LinkedInPublishOAuth.publish_post(access_token, share_text, agent_url)
            elif platform == 'facebook':
                result = FacebookPublishOAuth.publish_post(access_token, share_text, agent_url)
            elif platform == 'twitter':
                # Twitter doesn't support URLs in text well, include in text
                twitter_text = f"🤖 Check out this AI agent: {agent.name}\n\n{agent.description[:200] if agent.description else ''}\n\n{agent_url}\n\n#AI #Agent #Automation"
                result = TwitterPublishOAuth.publish_post(access_token, twitter_text)
            elif platform == 'instagram':
                # Instagram requires image, so we'll just show success message
                messages.success(request, f'Successfully connected to Instagram! Use the share link feature to copy content for Instagram.')
                return redirect('public_agents_list')
            
            messages.success(request, f'Successfully published to {platform.capitalize()}! 🎉')
        except Exception as e:
            logger.error(f"Failed to publish to {platform}: {e}", exc_info=True)
            messages.warning(request, f'Connected to {platform.capitalize()} but failed to publish. Please try again.')
        
        return redirect('public_agents_list')
        
    except Exception as e:
        logger.error(f"OAuth callback error for {platform}: {e}", exc_info=True)
        messages.error(request, f'Failed to connect to {platform.capitalize()}. Please try again.')
        return redirect('public_agents_list')


def accept_agent_share(request, token):
    """Accept an agent share via token."""
    from .models import AgentShare
    logger = logging.getLogger(__name__)
    
    try:
        share = AgentShare.objects.get(token=token)
    except AgentShare.DoesNotExist:
        messages.error(request, 'Invalid or expired share link.')
        return redirect('agents_list')
    
    # Check if share is valid
    if not share.is_valid():
        if share.is_expired():
            messages.error(request, 'This share link has expired.')
        elif share.is_accepted:
            messages.info(request, 'This share has already been accepted.')
        return redirect('agents_list')
    
    # Check if user is logged in
    if not request.user.is_authenticated:
        # Store token in session and redirect to login
        request.session['agent_share_token'] = token
        messages.info(request, f'Please log in to accept the share. The agent will be shared with {share.email}.')
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    
    # Verify email matches - reject if mismatch
    if request.user.email.lower() != share.email.lower():
        messages.error(
            request, 
            f'Email mismatch: This share was sent to {share.email}, but you are logged in as {request.user.email}. '
            'Please log in with the correct email address to accept this share.'
        )
        return redirect('agents_list')
    
    # Accept the share (only if email matches)
    share.is_accepted = True
    share.accepted_at = timezone.now()
    share.accepted_by = request.user
    share.save()
    
    messages.success(request, f'You now have access to the agent "{share.agent.name}"!')
    return redirect('agent_detail', slug=share.agent.slug)


@login_required
@require_http_methods(["GET"])
def get_user_conversations(request, user_id):
    """
    Get conversations and messages for a specific user.
    Only accessible by agent owners for their shared agents.
    """
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        # Get the target user
        target_user = get_object_or_404(User, id=user_id)
        
        # Get optional agent_id filter
        agent_id = request.GET.get('agent_id')
        
        # Get user's agents (as owner)
        user_agents = Agent.objects.filter(user=request.user, status='published')
        
        if not user_agents.exists():
            return JsonResponse({'error': 'You do not have any published agents'}, status=403)
        
        # Filter conversations by user's agents
        conversations_query = Conversation.objects.filter(
            agent__in=user_agents,
            user=target_user
        ).select_related('agent', 'user').prefetch_related('messages')
        
        # Filter by specific agent if provided
        if agent_id:
            try:
                agent = Agent.objects.get(slug=slug, user=request.user)
                conversations_query = conversations_query.filter(agent=agent)
            except Agent.DoesNotExist:
                return JsonResponse({'error': 'Agent not found or you do not have permission'}, status=404)
        
        # Get conversations ordered by most recent
        conversations = conversations_query.order_by('-updated_at')[:50]  # Limit to 50 most recent
        
        # Get pagination parameters for messages
        message_limit = int(request.GET.get('message_limit', 10))
        message_offset = int(request.GET.get('message_offset', 0))
        conversation_id = request.GET.get('conversation_id')  # Optional: load messages for specific conversation
        
        conversations_data = []
        for conv in conversations:
            # Get total message count
            total_message_count = conv.messages.count()
            
            # Get messages for this conversation with pagination
            # If conversation_id is specified, only return messages for that conversation
            if conversation_id and str(conv.id) != str(conversation_id):
                continue
            
            messages_query = conv.messages.all().order_by('created_at')
            
            # Apply pagination only if conversation_id is specified (loading more messages)
            if conversation_id:
                messages = messages_query[message_offset:message_offset + message_limit]
            else:
                # Initial load: show first 10 messages
                messages = messages_query[:message_limit]
            
            messages_data = []
            for msg in messages:
                messages_data.append({
                    'id': msg.id,
                    'message_type': msg.message_type,
                    'content': msg.content,
                    'tool_name': msg.tool_name if msg.tool_name else None,
                    'tool_parameters': msg.tool_parameters if msg.tool_parameters else None,
                    'tool_result': msg.tool_result if msg.tool_result else None,
                    'created_at': msg.created_at.isoformat(),
                })
            
            # Only add conversation if it's initial load or matches conversation_id
            if not conversation_id or str(conv.id) == str(conversation_id):
                conversations_data.append({
                    'id': conv.id,
                    'conversation_id': conv.conversation_id,
                    'agent_id': conv.agent.id,
                    'agent_name': conv.agent.name,
                    'status': conv.status,
                    'created_at': conv.created_at.isoformat(),
                    'updated_at': conv.updated_at.isoformat(),
                    'total_message_count': total_message_count,
                    'message_count': len(messages_data),
                    'has_more_messages': total_message_count > (message_offset + len(messages_data)) if conversation_id else total_message_count > message_limit,
                    'messages': messages_data,
                })
        
        response_data = {
            'user': {
                'id': target_user.id,
                'username': target_user.username,
                'email': target_user.email,
            },
            'conversations': conversations_data,
            'total_conversations': len(conversations_data) if not conversation_id else None,
        }
        
        # If loading more messages for a specific conversation, don't include total_conversations
        if conversation_id:
            del response_data['total_conversations']
        
        return JsonResponse(response_data)
        
    except Exception as e:
        logger.error(f"Error getting user conversations: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Failed to get conversations'}, status=500)

