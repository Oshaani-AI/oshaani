"""Chat views for home page."""
import json
import uuid
import logging
import os
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.db.models import Q, OuterRef, Subquery
from .models import Agent, Conversation, ConversationMessage, ConversationFile, AgentFeedback

logger = logging.getLogger(__name__)


def user_has_agent_access(user, agent):
    """Check if user has access to agent (owns it, has accepted share, or via public share)."""
    if agent.user == user:
        return True
    from .models import AgentShare, AgentPublicShare
    # Check email-based share
    share = AgentShare.objects.filter(
        agent=agent,
        email=user.email,
        is_accepted=True,
        accepted_by=user
    ).first()
    if share and not share.is_expired():
        return True
    # Check public share (anyone can access if there's an active public share)
    public_share = AgentPublicShare.objects.filter(
        agent=agent,
        is_active=True
    ).first()
    if public_share and public_share.is_valid():
        return True
    return False


@login_required
def chat_home(request, slug=None):
    """Chat home page with agent selection and conversation list."""
    from django.shortcuts import redirect
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    # Check if viewing another user's conversations (agent owner viewing shared agent user)
    view_user_id = request.GET.get('view_user_id')
    view_user = None
    is_view_mode = False
    
    if view_user_id:
        try:
            view_user = get_object_or_404(User, id=view_user_id)
            # Verify that the current user owns agents that were shared with view_user
            user_agents = Agent.objects.filter(user=request.user, status='published')
            if user_agents.exists():
                # Check if view_user has conversations with any of user's agents
                user_agent_ids = list(user_agents.values_list('id', flat=True))
                has_conversations = Conversation.objects.filter(
                    user=view_user,
                    agent_id__in=user_agent_ids
                ).exists()
                
                if has_conversations:
                    is_view_mode = True
                else:
                    from django.contrib import messages
                    messages.warning(request, 'No conversations found for this user with your agents.')
                    return redirect('agents_list')
            else:
                from django.contrib import messages
                messages.error(request, 'You do not have any published agents.')
                return redirect('agents_list')
        except (ValueError, TypeError):
            pass  # Invalid user_id, ignore
    
    # Check if user came from a public share link
    public_share_token = request.session.get('public_share_token')
    public_share_agent_id = request.session.get('public_share_agent_id')
    if public_share_token and public_share_agent_id:
        # Clear session variables
        del request.session['public_share_token']
        del request.session['public_share_agent_id']
        request.session.save()
        
        # Verify the public share is still valid
        try:
            from .models import AgentPublicShare
            public_share = AgentPublicShare.objects.get(token=public_share_token)
            if public_share.is_valid() and public_share.agent.id == public_share_agent_id:
                # Redirect to chat with the agent using SEO-friendly URL
                from django.urls import reverse
                # Get the agent's slug for the redirect
                agent_for_redirect = Agent.objects.get(id=public_share_agent_id)
                return redirect(reverse('agent_chat', kwargs={'slug': agent_for_redirect.slug}))
        except AgentPublicShare.DoesNotExist:
            pass
    
    # Get user's own and shared published agents using Q objects
    from django.db.models import Q
    from .models import AgentPublicShare
    
    # Build query using Q objects to avoid queryset combination issues
    # Only include user's own agents and agents shared with them via email
    # Do NOT include public agents unless user is the owner
    q = Q(
        user=request.user,
        status='published'
    )
    
    # Add shared agents if user has an email
    if request.user.email:
        q |= Q(
            shares__email=request.user.email,
            shares__is_accepted=True,
            shares__accepted_by=request.user,
            status='published'  # Only published shared agents
        )
    
    # Get combined queryset with model information
    published_agents = Agent.objects.filter(q).distinct().select_related('model').order_by('name')
    
    # Create a set of shared agent IDs for template use
    # Get shared agents separately for the set
    if request.user.email:
        shared_agents = Agent.objects.filter(
            shares__email=request.user.email,
            shares__is_accepted=True,
            shares__accepted_by=request.user,
            status='published'
        ).exclude(user=request.user)
        shared_agent_ids = set(shared_agents.values_list('id', flat=True))
    else:
        shared_agent_ids = set()
    
    # If not in view mode and no published agents, redirect to create agent
    if not is_view_mode and not published_agents.exists():
        from django.contrib import messages
        messages.info(request, 'You need to create and publish an agent before you can chat. Let\'s create your first agent!')
        return redirect('agent_create')
    
    # Skip loading conversations on initial chat page (/, /chat/) so response is fast; front-end loads via API
    path_stem = (request.path or '').rstrip('/')
    skip_conversations = (path_stem in ('', 'chat') and not is_view_mode and not slug)
    
    if skip_conversations:
        conv_list = []
        conversations_with_preview = []
    elif is_view_mode:
        user_agent_ids = list(Agent.objects.filter(user=request.user, status='published').values_list('id', flat=True))
        latest_msg_subq = ConversationMessage.objects.filter(conversation_id=OuterRef('id')).order_by('-created_at').values('id')[:1]
        conversations = Conversation.objects.filter(
            user=view_user,
            agent_id__in=user_agent_ids,
            agent__status='published'
        ).distinct().select_related('agent').annotate(latest_msg_id=Subquery(latest_msg_subq)).order_by('-updated_at')[:20]
        conv_list = list(conversations)
        msg_ids = [c.latest_msg_id for c in conv_list if getattr(c, 'latest_msg_id', None)]
        latest_messages = ConversationMessage.objects.filter(id__in=msg_ids).in_bulk() if msg_ids else {}
        conversations_with_preview = []
        seen = set()
        for conv in conv_list:
            if conv.conversation_id in seen:
                continue
            seen.add(conv.conversation_id)
            last_message = latest_messages.get(conv.latest_msg_id) if getattr(conv, 'latest_msg_id', None) else None
            conversations_with_preview.append({'conversation': conv, 'last_message': last_message})
    else:
        conversations = Conversation.objects.filter(
            user=request.user,
            status='active',
            agent__status='published'
        ).filter(
            Q(agent__user=request.user) |
            Q(
                agent__shares__email=request.user.email,
                agent__shares__is_accepted=True,
                agent__shares__accepted_by=request.user
            )
        )
        if slug:
            try:
                agent_for_filter = Agent.objects.get(slug=slug)
                conversations = conversations.filter(agent_id=agent_for_filter.id)
            except Agent.DoesNotExist:
                pass
        latest_msg_subq = ConversationMessage.objects.filter(conversation_id=OuterRef('id')).order_by('-created_at').values('id')[:1]
        conversations = conversations.distinct().select_related('agent').annotate(latest_msg_id=Subquery(latest_msg_subq)).order_by('-updated_at')[:20]
        conv_list = list(conversations)
        msg_ids = [c.latest_msg_id for c in conv_list if getattr(c, 'latest_msg_id', None)]
        latest_messages = ConversationMessage.objects.filter(id__in=msg_ids).in_bulk() if msg_ids else {}
        conversations_with_preview = []
        seen = set()
        for conv in conv_list:
            if conv.conversation_id in seen:
                continue
            seen.add(conv.conversation_id)
            last_message = latest_messages.get(conv.latest_msg_id) if getattr(conv, 'latest_msg_id', None) else None
            conversations_with_preview.append({'conversation': conv, 'last_message': last_message})
    
    # Get agent from URL path (SEO-friendly slug) or query parameter (backward compatibility with ID)
    selected_agent_id = None
    # First check function parameter (from URL path like /agent/my-agent/)
    # Then check URL kwargs (fallback)
    # Finally check query parameter (from ?agent_id=15 for backward compatibility)
    slug_from_path = slug or (request.resolver_match.kwargs.get('slug') if request.resolver_match else None)
    agent_id_param = request.GET.get('agent_id')  # Legacy support for ID-based query param
    
    # If we have a slug, look up the agent ID for it
    if slug_from_path:
        try:
            agent_by_slug = Agent.objects.get(slug=slug_from_path)
            agent_id_param = str(agent_by_slug.id)
        except Agent.DoesNotExist:
            agent_id_param = None
    
    # Check if the requested agent is a public agent that user doesn't own
    public_agent_to_add = None
    if agent_id_param:
        try:
            agent_id_int = int(agent_id_param)
            # First check if it's in user's own or shared agents (using queryset)
            if published_agents.filter(id=agent_id_int).exists():
                selected_agent_id = agent_id_int
            else:
                # Check if it's a valid public agent
                try:
                    public_agent = Agent.objects.get(id=agent_id_int, status='published')
                    # Check if it has an active public share
                    public_share = AgentPublicShare.objects.filter(
                        agent=public_agent,
                        is_active=True
                    ).first()
                    if public_share and public_share.is_valid():
                        # Add this public agent to the list
                        public_agent_to_add = public_agent
                        selected_agent_id = agent_id_int
                except Agent.DoesNotExist:
                    pass  # Agent doesn't exist or not published
        except (ValueError, TypeError):
            pass  # Invalid agent_id, ignore
    
    # Convert queryset to list for easier manipulation
    agents_list = list(published_agents)
    # Add public agent to the list if it was accessed via URL
    if public_agent_to_add:
        # Only add if not already in the list
        if not any(agent.id == public_agent_to_add.id for agent in agents_list):
            agents_list.append(public_agent_to_add)
        # Re-sort by name
        agents_list.sort(key=lambda x: x.name)
    
    # Use list for context (templates can iterate over lists just fine)
    published_agents = agents_list
    
    context = {
        'agents': published_agents,
        'shared_agent_ids': shared_agent_ids,
        'conversations': conv_list,
        'conversations_with_preview': conversations_with_preview,
        'selected_agent_id': selected_agent_id,
        'is_view_mode': is_view_mode,
        'view_user': view_user,
        'load_conversations_via_api': skip_conversations,
    }
    return render(request, 'chat/chat_home.html', context)


@login_required
@require_http_methods(["POST"])
def send_chat_message(request):
    """Send a chat message via AJAX."""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')
        message = data.get('message', '').strip()
        conversation_id = data.get('conversation_id')
        
        if not agent_id:
            return JsonResponse({
                'success': False,
                'error': 'agent_id is required'
            }, status=400)
        
        if not message:
            return JsonResponse({
                'success': False,
                'error': 'Message cannot be empty'
            }, status=400)
        
        # Get agent - must be published or testing, and either user's own or shared with user
        agent = get_object_or_404(
            Agent, 
            id=agent_id,
            status__in=['published', 'testing']
        )
        # Check if user has access
        if not user_has_agent_access(request.user, agent):
            return JsonResponse({
                'success': False,
                'error': 'Agent not found or access denied'
            }, status=404)
        
        # Check if agent has a model configured
        if not agent.model:
            return JsonResponse({
                'success': False,
                'error': 'Agent is not configured with a model. Please configure the agent with a valid LLM model.'
            }, status=400)
        
        # Get or create conversation
        # For testing status agents, always create a new conversation (start fresh)
        conversation = None
        
        if agent.status == 'testing':
            # Testing agents always start fresh - create new conversation
            conversation = Conversation.objects.create(
                agent=agent,
                user=request.user,
                conversation_id=str(uuid.uuid4()),
                status='active'
            )
        elif conversation_id:
            # Try to get existing conversation if conversation_id provided
            try:
                conversation = Conversation.objects.get(
                    conversation_id=conversation_id,
                    agent=agent,
                    user=request.user,
                    status='active'
                )
            except Conversation.DoesNotExist:
                conversation = None
        
        # Create new conversation if none exists
        if not conversation:
            conversation = Conversation.objects.create(
                agent=agent,
                user=request.user,
                conversation_id=str(uuid.uuid4()),
                status='active'
            )
        
        # Handle file uploads if provided
        file_ids = data.get('file_ids', [])
        uploaded_files = []
        if file_ids:
            for file_id in file_ids:
                try:
                    file_obj = ConversationFile.objects.get(
                        file_id=file_id,
                        agent=agent,
                        conversation__isnull=True  # Files not yet linked to conversation
                    )
                    # Link file to conversation
                    file_obj.conversation = conversation
                    file_obj.save()
                    uploaded_files.append({
                        'file_id': file_obj.file_id,
                        'file_name': file_obj.file_name,
                        'file_size': file_obj.file_size
                    })
                except ConversationFile.DoesNotExist:
                    logger.warning(f"File {file_id} not found for agent {agent.id}")
        
        # Get system prompt
        system_prompt = agent.configuration.get('instruction') or agent.configuration.get('system_prompt', '')
        
        # Optional: run in background (returns immediately; client polls for result)
        if data.get('background', False):
            from .tasks import process_chat_message_background
            from django.core.cache import cache
            task = process_chat_message_background.delay(
                agent_id=agent.id,
                conversation_id=conversation.conversation_id,
                user_id=request.user.id,
                message=message,
                system_prompt=system_prompt or '',
            )
            cache.set(f"chat_task_user_{task.id}", request.user.id, timeout=3600)
            return JsonResponse({
                'success': True,
                'background': True,
                'task_id': task.id,
                'conversation_id': conversation.conversation_id,
                'message': 'Processing in background. Poll GET /api/chat/task-status/<task_id>/ for result.',
            })
        
        # Check if streaming is requested and model supports it
        use_streaming = data.get('stream', False)
        is_ollama = agent.model and agent.model.provider == 'ollama'
        is_bedrock = agent.model and agent.model.provider == 'bedrock'
        supports_streaming = is_ollama or is_bedrock
        
        # Run long-running agent loop in background to avoid blocking the request
        # (prevents "Application instance took too long to shut down" on deploy/timeouts)
        if use_streaming and supports_streaming:
            from .tasks import process_chat_message_streaming_background
            from django.core.cache import cache
            task = process_chat_message_streaming_background.delay(
                agent_id=agent.id,
                conversation_id=conversation.conversation_id,
                user_id=request.user.id,
                message=message,
                system_prompt=system_prompt or '',
            )
            cache.set(f"chat_task_user_{task.id}", request.user.id, timeout=3600)
            return JsonResponse({
                'success': True,
                'background': True,
                'task_id': task.id,
                'conversation_id': conversation.conversation_id,
                'message': 'Processing in background. Poll GET /api/chat/task-status/<task_id>/ for result.',
            }, status=202)
        else:
            # Non-streaming: also run in background to avoid long request blocking shutdown
            from .tasks import process_chat_message_background
            from django.core.cache import cache
            task = process_chat_message_background.delay(
                agent_id=agent.id,
                conversation_id=conversation.conversation_id,
                user_id=request.user.id,
                message=message,
                system_prompt=system_prompt or '',
            )
            cache.set(f"chat_task_user_{task.id}", request.user.id, timeout=3600)
            return JsonResponse({
                'success': True,
                'background': True,
                'task_id': task.id,
                'conversation_id': conversation.conversation_id,
                'message': 'Processing in background. Poll GET /api/chat/task-status/<task_id>/ for result.',
            }, status=202)
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        logger.error(f"Error sending chat message: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to send message: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["GET"])
def get_chat_task_status(request, task_id):
    """Return status and result of a background chat task. Poll until status is 'success' or 'failure'."""
    from django.core.cache import cache
    from celery.result import AsyncResult

    # Ensure only the user who started the task can poll it
    allowed_user_id = cache.get(f"chat_task_user_{task_id}")
    if allowed_user_id is not None and request.user.id != allowed_user_id:
        return JsonResponse({'success': False, 'error': 'Access denied'}, status=403)

    # Prefer cache (set when task completes, or partial when task is running)
    cache_key = f"chat_background_{task_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        if cached.get('status') == 'running':
            return JsonResponse({
                'success': True,
                'status': 'running',
                'result': {
                    'tool_calls': cached.get('tool_calls', []),
                    'response': cached.get('response', ''),
                },
            }, status=202)
        return JsonResponse({
            'success': True,
            'status': 'success' if cached.get('success') else 'failure',
            'result': cached,
        })

    # Task still running or result not yet in cache - check Celery
    try:
        result = AsyncResult(task_id)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid task_id'}, status=400)

    if not result.ready():
        return JsonResponse({
            'success': True,
            'status': 'pending',
            'message': 'Processing in background. Poll again in a few seconds.',
        }, status=202)

    # Task finished - get result (may be in cache now from task; if not, use Celery result)
    out = cache.get(cache_key)
    if out is None and result.successful():
        out = result.result
    if out is None and result.failed():
        out = {'success': False, 'error': str(result.result) if result.result else 'Task failed'}
    if out is not None:
        return JsonResponse({
            'success': True,
            'status': 'success' if out.get('success') else 'failure',
            'result': out if isinstance(out, dict) else {'response': str(out)},
        })
    return JsonResponse({'success': True, 'status': 'pending', 'message': 'Result not yet available.'}, status=202)


@login_required
@require_http_methods(["GET"])
def get_conversation(request, conversation_id):
    """Get conversation messages via AJAX - returns last 10 messages."""
    try:
        # Try to get conversation - either owned by user OR owned by agent owner viewing shared agent user
        
        conversation = None
        try:
            # First try: conversation owned by current user
            conversation = Conversation.objects.get(
                conversation_id=conversation_id,
                user=request.user,
                status='active',
                agent__status='published'
            )
        except Conversation.DoesNotExist:
            # Second try: conversation owned by another user, but agent is owned by current user
            # This allows agent owners to view conversations of users who used their shared agents
            conversation = Conversation.objects.filter(
                conversation_id=conversation_id,
                agent__user=request.user,
                agent__status='published'
            ).first()
        
        if not conversation:
            return JsonResponse({
                'success': False,
                'error': 'Conversation not found or access denied'
            }, status=404)
        
        # Check if user has access to the agent (for shared agents)
        if conversation.user != request.user:
            # Agent owner viewing another user's conversation - verify agent ownership
            if conversation.agent.user != request.user:
                return JsonResponse({
                    'success': False,
                    'error': 'Conversation not found or access denied'
                }, status=404)
        else:
            # User viewing their own conversation - check agent access
            if not user_has_agent_access(request.user, conversation.agent):
                return JsonResponse({
                    'success': False,
                    'error': 'Conversation not found or access denied'
                }, status=404)
        
        # Get total message count (excluding tool calls and tool results)
        total_messages = ConversationMessage.objects.filter(
            conversation=conversation
        ).exclude(
            message_type__in=['tool_call', 'tool_result']
        ).count()
        
        # Get last 20 messages (most recent) - excluding tool calls and tool results
        # Filter out tool_call and tool_result messages from chat history
        messages = ConversationMessage.objects.filter(
            conversation=conversation
        ).exclude(
            message_type__in=['tool_call', 'tool_result']
        ).order_by('-created_at')[:20]
        
        # Convert to list and reverse to show oldest first in chat (for proper chronological display)
        messages_list = list(messages)
        messages_list.reverse()  # Now oldest is first, newest is last
        
        messages_data = []
        for msg in messages_list:
            message_data = {
                'id': msg.id,
                'type': msg.message_type,
                'content': msg.content,
                'created_at': msg.created_at.isoformat(),
                'tool_name': None,  # Tool name no longer needed since we filter out tool messages
                'files': [],
                'tool_calls': []
            }
            
            # Include files linked to this conversation that were created around the same time as agent messages
            # Files are created during tool execution, which happens BEFORE agent message is saved
            # So we need to look for files created before the agent message
            if msg.message_type == 'agent':
                # Get the previous user message to define the interaction window
                from datetime import timedelta
                
                # Find the user message that preceded this agent message
                user_msg = ConversationMessage.objects.filter(
                    conversation=conversation,
                    message_type='user',
                    created_at__lt=msg.created_at
                ).order_by('-created_at').first()
                
                # Get tool calls that occurred between user message and agent message
                tool_calls = []
                if user_msg:
                    # Get tool_call and tool_result messages between user message and agent message
                    tool_messages = ConversationMessage.objects.filter(
                        conversation=conversation,
                        message_type__in=['tool_call', 'tool_result'],
                        created_at__gte=user_msg.created_at - timedelta(seconds=5),
                        created_at__lte=msg.created_at + timedelta(seconds=5)
                    ).order_by('created_at')
                    
                    # Group tool_call and tool_result messages together
                    tool_call_dict = {}
                    for tool_msg in tool_messages:
                        tool_name = tool_msg.tool_name or 'unknown'
                        if tool_msg.message_type == 'tool_call':
                            if tool_name not in tool_call_dict:
                                tool_call_dict[tool_name] = {
                                    'tool': tool_name,
                                    'parameters': tool_msg.tool_parameters or {},
                                    'result': {}
                                }
                        elif tool_msg.message_type == 'tool_result':
                            if tool_name not in tool_call_dict:
                                tool_call_dict[tool_name] = {
                                    'tool': tool_name,
                                    'parameters': {},
                                    'result': tool_msg.tool_result or {}
                                }
                            else:
                                tool_call_dict[tool_name]['result'] = tool_msg.tool_result or {}
                    
                    tool_calls = list(tool_call_dict.values())
                else:
                    # Fallback: look for tool calls before agent message
                    tool_messages = ConversationMessage.objects.filter(
                        conversation=conversation,
                        message_type__in=['tool_call', 'tool_result'],
                        created_at__gte=msg.created_at - timedelta(seconds=30),
                        created_at__lte=msg.created_at + timedelta(seconds=5)
                    ).order_by('created_at')
                    
                    tool_call_dict = {}
                    for tool_msg in tool_messages:
                        tool_name = tool_msg.tool_name or 'unknown'
                        if tool_msg.message_type == 'tool_call':
                            if tool_name not in tool_call_dict:
                                tool_call_dict[tool_name] = {
                                    'tool': tool_name,
                                    'parameters': tool_msg.tool_parameters or {},
                                    'result': {}
                                }
                        elif tool_msg.message_type == 'tool_result':
                            if tool_name not in tool_call_dict:
                                tool_call_dict[tool_name] = {
                                    'tool': tool_name,
                                    'parameters': {},
                                    'result': tool_msg.tool_result or {}
                                }
                            else:
                                tool_call_dict[tool_name]['result'] = tool_msg.tool_result or {}
                    
                    tool_calls = list(tool_call_dict.values())
                
                message_data['tool_calls'] = tool_calls
                
                # Look for files created between the user message and agent message (with buffer)
                if user_msg:
                    files = ConversationFile.objects.filter(
                        conversation=conversation,
                        agent=conversation.agent,
                        uploaded_at__gte=user_msg.created_at - timedelta(seconds=5),
                        uploaded_at__lte=msg.created_at + timedelta(seconds=5)
                    ).order_by('uploaded_at')
                else:
                    # Fallback: look for files created before agent message
                    files = ConversationFile.objects.filter(
                        conversation=conversation,
                        agent=conversation.agent,
                        uploaded_at__gte=msg.created_at - timedelta(seconds=30),
                        uploaded_at__lte=msg.created_at + timedelta(seconds=5)
                    ).order_by('uploaded_at')
                
                for file_obj in files:
                    # Use authenticated media serving endpoint for file downloads
                    # This ensures files are accessible even when DEBUG=False
                    if file_obj.file_path:
                        # Extract relative path from file_path
                        file_path_str = file_obj.file_path.name if hasattr(file_obj.file_path, 'name') else str(file_obj.file_path)
                        # Use the authenticated media serving endpoint
                        file_url = f'/media/{file_path_str}'
                    else:
                        # Fallback to download_url if file_path is not available
                        file_url = file_obj.download_url or ''
                    
                    message_data['files'].append({
                        'file_id': file_obj.file_id,
                        'file_name': file_obj.file_name,
                        'file_url': file_url,
                        'file_type': file_obj.file_type,
                        'file_size': file_obj.file_size
                    })
            
            messages_data.append(message_data)
        
        # Determine if there are older messages (before the oldest message we loaded)
        # Check for older messages excluding tool calls and tool results
        has_more = False
        if messages_list:
            oldest_loaded = messages_list[0]  # Oldest message in this batch
            older_count = ConversationMessage.objects.filter(
                conversation=conversation,
                created_at__lt=oldest_loaded.created_at
            ).exclude(
                message_type__in=['tool_call', 'tool_result']
            ).count()
            has_more = older_count > 0
        
        return JsonResponse({
            'success': True,
            'conversation': {
                'id': conversation.conversation_id,
                'agent_id': conversation.agent.id,
                'agent_name': conversation.agent.name,
                'created_at': conversation.created_at.isoformat(),
                'updated_at': conversation.updated_at.isoformat(),
            },
            'messages': messages_data,
            'total_messages': total_messages,
            'has_more': has_more
        })
        
    except Exception as e:
        logger.error(f"Error getting conversation: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to get conversation: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["GET"])
def get_conversation_messages_paginated(request, conversation_id):
    """Get conversation messages with pagination."""
    try:
        # Try to get conversation - either owned by user OR owned by agent owner viewing shared agent user
        
        conversation = None
        try:
            # First try: conversation owned by current user
            conversation = Conversation.objects.get(
                conversation_id=conversation_id,
                user=request.user,
                status='active',
                agent__status='published'
            )
        except Conversation.DoesNotExist:
            # Second try: conversation owned by another user, but agent is owned by current user
            # Don't filter by status='active' for view mode - show all conversations
            conversation = Conversation.objects.filter(
                conversation_id=conversation_id,
                agent__user=request.user,
                agent__status='published'
            ).first()
        
        if not conversation:
            return JsonResponse({
                'success': False,
                'error': 'Conversation not found or access denied'
            }, status=404)
        
        # Check if user has access to the agent (for shared agents)
        if conversation.user != request.user:
            # Agent owner viewing another user's conversation - verify agent ownership
            if conversation.agent.user != request.user:
                return JsonResponse({
                    'success': False,
                    'error': 'Conversation not found or access denied'
                }, status=404)
        else:
            # User viewing their own conversation - check agent access
            if not user_has_agent_access(request.user, conversation.agent):
                return JsonResponse({
                    'success': False,
                    'error': 'Conversation not found or access denied'
                }, status=404)
        
        # Get pagination parameters
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))
        before_id = request.GET.get('before_id')  # Load messages before this ID
        
        # Get total count (excluding tool calls and tool results)
        total_messages = ConversationMessage.objects.filter(
            conversation=conversation
        ).exclude(
            message_type__in=['tool_call', 'tool_result']
        ).count()
        
        # Build query - exclude tool calls and tool results from chat history
        messages_query = ConversationMessage.objects.filter(
            conversation=conversation
        ).exclude(
            message_type__in=['tool_call', 'tool_result']
        )
        
        # If before_id is provided, load messages before that ID
        if before_id:
            try:
                before_message = ConversationMessage.objects.get(
                    id=before_id,
                    conversation=conversation
                )
                messages_query = messages_query.filter(
                    created_at__lt=before_message.created_at
                )
            except ConversationMessage.DoesNotExist:
                pass
        
        # Order by created_at descending (newest first)
        messages_query = messages_query.order_by('-created_at')
        
        # Get messages for this page
        start = (page - 1) * per_page
        end = start + per_page
        messages = list(messages_query[start:end])
        
        # Reverse to show oldest first
        messages = list(reversed(messages))
        
        messages_data = []
        for msg in messages:
            message_data = {
                'id': msg.id,
                'type': msg.message_type,
                'content': msg.content,
                'created_at': msg.created_at.isoformat(),
                'tool_name': None,  # Tool name no longer needed since we filter out tool messages
                'files': [],
                'tool_calls': []
            }
            
            # Include files linked to this conversation that were created around the same time as agent messages
            # Files are created during tool execution, which happens BEFORE agent message is saved
            # So we need to look for files created before the agent message
            if msg.message_type == 'agent':
                # Get the previous user message to define the interaction window
                from datetime import timedelta
                
                # Find the user message that preceded this agent message
                user_msg = ConversationMessage.objects.filter(
                    conversation=conversation,
                    message_type='user',
                    created_at__lt=msg.created_at
                ).order_by('-created_at').first()
                
                # Get tool calls that occurred between user message and agent message
                tool_calls = []
                if user_msg:
                    # Get tool_call and tool_result messages between user message and agent message
                    tool_messages = ConversationMessage.objects.filter(
                        conversation=conversation,
                        message_type__in=['tool_call', 'tool_result'],
                        created_at__gte=user_msg.created_at - timedelta(seconds=5),
                        created_at__lte=msg.created_at + timedelta(seconds=5)
                    ).order_by('created_at')
                    
                    # Group tool_call and tool_result messages together
                    tool_call_dict = {}
                    for tool_msg in tool_messages:
                        tool_name = tool_msg.tool_name or 'unknown'
                        if tool_msg.message_type == 'tool_call':
                            if tool_name not in tool_call_dict:
                                tool_call_dict[tool_name] = {
                                    'tool': tool_name,
                                    'parameters': tool_msg.tool_parameters or {},
                                    'result': {}
                                }
                        elif tool_msg.message_type == 'tool_result':
                            if tool_name not in tool_call_dict:
                                tool_call_dict[tool_name] = {
                                    'tool': tool_name,
                                    'parameters': {},
                                    'result': tool_msg.tool_result or {}
                                }
                            else:
                                tool_call_dict[tool_name]['result'] = tool_msg.tool_result or {}
                    
                    tool_calls = list(tool_call_dict.values())
                else:
                    # Fallback: look for tool calls before agent message
                    tool_messages = ConversationMessage.objects.filter(
                        conversation=conversation,
                        message_type__in=['tool_call', 'tool_result'],
                        created_at__gte=msg.created_at - timedelta(seconds=30),
                        created_at__lte=msg.created_at + timedelta(seconds=5)
                    ).order_by('created_at')
                    
                    tool_call_dict = {}
                    for tool_msg in tool_messages:
                        tool_name = tool_msg.tool_name or 'unknown'
                        if tool_msg.message_type == 'tool_call':
                            if tool_name not in tool_call_dict:
                                tool_call_dict[tool_name] = {
                                    'tool': tool_name,
                                    'parameters': tool_msg.tool_parameters or {},
                                    'result': {}
                                }
                        elif tool_msg.message_type == 'tool_result':
                            if tool_name not in tool_call_dict:
                                tool_call_dict[tool_name] = {
                                    'tool': tool_name,
                                    'parameters': {},
                                    'result': tool_msg.tool_result or {}
                                }
                            else:
                                tool_call_dict[tool_name]['result'] = tool_msg.tool_result or {}
                    
                    tool_calls = list(tool_call_dict.values())
                
                message_data['tool_calls'] = tool_calls
                
                # Look for files created between the user message and agent message (with buffer)
                if user_msg:
                    files = ConversationFile.objects.filter(
                        conversation=conversation,
                        agent=conversation.agent,
                        uploaded_at__gte=user_msg.created_at - timedelta(seconds=5),
                        uploaded_at__lte=msg.created_at + timedelta(seconds=5)
                    ).order_by('uploaded_at')
                else:
                    # Fallback: look for files created before agent message
                    files = ConversationFile.objects.filter(
                        conversation=conversation,
                        agent=conversation.agent,
                        uploaded_at__gte=msg.created_at - timedelta(seconds=30),
                        uploaded_at__lte=msg.created_at + timedelta(seconds=5)
                    ).order_by('uploaded_at')
                
                for file_obj in files:
                    # Use authenticated media serving endpoint for file downloads
                    # This ensures files are accessible even when DEBUG=False
                    if file_obj.file_path:
                        # Extract relative path from file_path
                        file_path_str = file_obj.file_path.name if hasattr(file_obj.file_path, 'name') else str(file_obj.file_path)
                        # Use the authenticated media serving endpoint
                        file_url = f'/media/{file_path_str}'
                    else:
                        # Fallback to download_url if file_path is not available
                        file_url = file_obj.download_url or ''
                    
                    message_data['files'].append({
                        'file_id': file_obj.file_id,
                        'file_name': file_obj.file_name,
                        'file_url': file_url,
                        'file_type': file_obj.file_type,
                        'file_size': file_obj.file_size
                    })
            
            messages_data.append(message_data)
        
        # Check if there are more messages (excluding tool calls and tool results)
        # If using before_id, check if there are more messages before the oldest loaded message
        if before_id:
            # Count messages before the oldest message we just loaded
            if messages_data:
                oldest_msg = messages[0]  # Oldest message in this batch
                older_count = ConversationMessage.objects.filter(
                    conversation=conversation,
                    created_at__lt=oldest_msg.created_at
                ).exclude(
                    message_type__in=['tool_call', 'tool_result']
                ).count()
                has_more = older_count > 0
            else:
                has_more = False
        else:
            # For initial load, check if there are more messages before the oldest loaded
            if messages_data:
                oldest_msg = messages[0]  # Oldest message in this batch
                older_count = ConversationMessage.objects.filter(
                    conversation=conversation,
                    created_at__lt=oldest_msg.created_at
                ).exclude(
                    message_type__in=['tool_call', 'tool_result']
                ).count()
                has_more = older_count > 0
            else:
                has_more = False
        
        return JsonResponse({
            'success': True,
            'messages': messages_data,
            'total_messages': total_messages,
            'page': page,
            'per_page': per_page,
            'has_more': has_more,
            'loaded_count': len(messages_data)
        })
        
    except Exception as e:
        logger.error(f"Error getting paginated messages: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to get messages: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["POST", "DELETE"])
def delete_conversation(request, conversation_id):
    """Delete a conversation owned by the current user (hard delete; cascades to messages, files, tool_calls, requests)."""
    try:
        conversation = Conversation.objects.filter(
            conversation_id=conversation_id,
            user=request.user,
        ).first()

        if not conversation:
            return JsonResponse({
                'success': False,
                'error': 'Conversation not found or access denied'
            }, status=404)

        agent_id = conversation.agent_id if conversation.agent_id else None
        conversation.delete()

        return JsonResponse({
            'success': True,
            'message': 'Conversation deleted',
            'conversation_id': conversation_id,
            'agent_id': agent_id,
        })
    except Exception as e:
        logger.error(f"Error deleting conversation {conversation_id}: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to delete conversation: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["GET"])
def download_conversation(request, conversation_id):
    """Download conversation chat history as text file."""
    try:
        conversation = get_object_or_404(
            Conversation,
            conversation_id=conversation_id,
            user=request.user,
            status='active',
            agent__status='published'
        )
        # Check if user has access to the agent
        if not user_has_agent_access(request.user, conversation.agent):
            return JsonResponse({
                'success': False,
                'error': 'Conversation not found or access denied'
            }, status=404)
        
        # Get all messages ordered by creation time (excluding tool calls and tool results)
        messages = ConversationMessage.objects.filter(
            conversation=conversation
        ).exclude(
            message_type__in=['tool_call', 'tool_result']
        ).order_by('created_at')
        
        # Build chat history text
        chat_history = []
        chat_history.append(f"Chat History: {conversation.agent.name}")
        chat_history.append(f"Conversation ID: {conversation.conversation_id}")
        chat_history.append(f"Started: {conversation.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        chat_history.append(f"Last Updated: {conversation.updated_at.strftime('%Y-%m-%d %H:%M:%S')}")
        chat_history.append("=" * 80)
        chat_history.append("")
        
        for msg in messages:
            timestamp = msg.created_at.strftime('%Y-%m-%d %H:%M:%S')
            if msg.message_type == 'user':
                chat_history.append(f"[{timestamp}] User:")
                chat_history.append(msg.content)
            elif msg.message_type == 'agent':
                chat_history.append(f"[{timestamp}] Agent:")
                chat_history.append(msg.content)
            chat_history.append("")
        
        # Create response with file download
        from django.http import HttpResponse
        response = HttpResponse('\n'.join(chat_history), content_type='text/plain')
        filename = f"chat_history_{conversation.agent.name.replace(' ', '_')}_{conversation.conversation_id[:8]}.txt"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
        
    except Exception as e:
        logger.error(f"Error downloading conversation: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to download conversation: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["GET"])
def get_conversations_list(request):
    """Get list of conversations for the user."""
    try:
        # Optional agent_id filter
        agent_id = request.GET.get('agent_id')
        
        conversations = Conversation.objects.filter(
            user=request.user,
            status='active',
            agent__status='published'
        ).filter(
            Q(agent__user=request.user) |
            Q(
                agent__shares__email=request.user.email,
                agent__shares__is_accepted=True,
                agent__shares__accepted_by=request.user
            )
        )
        
        # Filter by specific agent if provided
        if agent_id:
            try:
                conversations = conversations.filter(agent_id=int(agent_id))
            except (ValueError, TypeError):
                pass  # Invalid agent_id, ignore filter
        
        # Annotate with latest message id to avoid N+1
        latest_msg_subq = ConversationMessage.objects.filter(
            conversation_id=OuterRef('id')
        ).exclude(
            message_type__in=['tool_call', 'tool_result']
        ).order_by('-created_at').values('id')[:1]
        conversations = conversations.distinct().select_related('agent').annotate(
            latest_msg_id=Subquery(latest_msg_subq)
        ).order_by('-updated_at')[:20]
        
        conv_list = list(conversations)
        msg_ids = [c.latest_msg_id for c in conv_list if getattr(c, 'latest_msg_id', None)]
        latest_messages = ConversationMessage.objects.filter(
            id__in=msg_ids
        ).exclude(
            message_type__in=['tool_call', 'tool_result']
        ).in_bulk() if msg_ids else {}
        
        conversations_data = []
        for conv in conv_list:
            last_message = latest_messages.get(conv.latest_msg_id) if getattr(conv, 'latest_msg_id', None) else None
            conversations_data.append({
                'id': conv.conversation_id,
                'agent_id': conv.agent.id,
                'agent_name': conv.agent.name,
                'last_message': (last_message.content[:100] if last_message and last_message.content else '') or '',
                'last_message_type': last_message.message_type if last_message else None,
                'updated_at': conv.updated_at.isoformat(),
            })
        
        return JsonResponse({
            'success': True,
            'conversations': conversations_data
        })
        
    except Exception as e:
        logger.error(f"Error getting conversations list: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to get conversations: {str(e)}'
        }, status=500)


@login_required
def upload_chat_file(request):
    """Upload a file for chat conversation."""
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'error': 'Only POST method allowed'
        }, status=405)
    
    try:
        if 'file' not in request.FILES:
            return JsonResponse({
                'success': False,
                'error': 'No file provided'
            }, status=400)
        
        file = request.FILES['file']
        agent_id = request.POST.get('agent_id')
        
        if not agent_id:
            return JsonResponse({
                'success': False,
                'error': 'agent_id is required'
            }, status=400)
        
        # Get agent - must be published and either user's own or shared with user
        agent = get_object_or_404(
            Agent,
            id=agent_id,
            status='published'
        )
        # Check if user has access
        if not user_has_agent_access(request.user, agent):
            return JsonResponse({
                'success': False,
                'error': 'Agent not found or access denied'
            }, status=404)
        
        # Validate file size (max 100MB)
        max_size = 100 * 1024 * 1024  # 100MB
        if file.size > max_size:
            return JsonResponse({
                'success': False,
                'error': 'File size exceeds 100MB limit'
            }, status=400)
        
        # Optionally link to an existing conversation if a valid conversation_id is provided.
        # When omitted, the file is linked later when the chat message is sent.
        conversation = None
        conversation_id = request.POST.get('conversation_id')
        if conversation_id:
            conversation = Conversation.objects.filter(
                conversation_id=conversation_id,
                user=request.user,
                agent=agent
            ).first()
        
        # Generate unique file ID
        file_id = str(uuid.uuid4())
        file_ext = os.path.splitext(file.name)[1]
        unique_filename = f"{file_id}{file_ext}"
        file_path = f"conversation_files/{agent.id}/{unique_filename}"
        
        # Read file content
        file.seek(0)  # Reset file pointer
        file_content = file.read()
        file.seek(0)  # Reset again for potential future reads
        
        # Save file
        saved_path = default_storage.save(file_path, ContentFile(file_content))
        file_url = default_storage.url(saved_path)
        
        # Create file record (linked to conversation when provided, otherwise linked when message is sent)
        conversation_file = ConversationFile.objects.create(
            agent=agent,
            conversation=conversation,
            file_name=file.name,
            file_path=saved_path,
            file_type=file.content_type or '',
            file_size=file.size,
            file_id=file_id,
            download_url=file_url
        )
        
        return JsonResponse({
            'success': True,
            'file_id': file_id,
            'file_name': file.name,
            'file_size': file.size,
            'file_type': file.content_type,
            'download_url': file_url,
            'uploaded_at': conversation_file.uploaded_at.isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error uploading chat file: {str(e)}", exc_info=True)
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return JsonResponse({
            'success': False,
            'error': f'Failed to upload file: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["POST"])
def submit_feedback(request):
    """Submit feedback for an agent message."""
    try:
        data = json.loads(request.body)
        message_id = data.get('message_id')
        feedback_type = data.get('feedback_type')  # 'positive' or 'negative'
        feedback_text = data.get('feedback_text', '').strip()
        
        if not message_id:
            return JsonResponse({
                'success': False,
                'error': 'message_id is required'
            }, status=400)
        
        if feedback_type not in ['positive', 'negative']:
            return JsonResponse({
                'success': False,
                'error': 'feedback_type must be "positive" or "negative"'
            }, status=400)
        
        # Get message and verify it belongs to user's conversation
        message = get_object_or_404(
            ConversationMessage,
            id=message_id,
            message_type='agent',
            conversation__user=request.user,
            conversation__status='active'
        )
        
        # Get the user's query that prompted this response
        user_message = message.conversation.messages.filter(
            message_type='user',
            created_at__lt=message.created_at
        ).order_by('-created_at').first()
        
        query = user_message.content if user_message else ''
        response = message.content
        
        # Simplify approach: just create new feedback entry each time
        # This avoids collation mismatch issues entirely
        # If duplicate prevention is needed later, we can add a message_id field to the model
        try:
            # Normalize Unicode characters and ensure strings are properly encoded
            import unicodedata
            
            def normalize_text(text):
                """Normalize text to remove problematic Unicode characters."""
                if not text:
                    return text
                # Normalize to NFC form (canonical composition)
                text = unicodedata.normalize('NFC', text)
                # Remove zero-width and non-breaking spaces that can cause issues
                text = text.replace('\u200B', '')  # Zero-width space
                text = text.replace('\u200C', '')  # Zero-width non-joiner
                text = text.replace('\u200D', '')  # Zero-width joiner
                text = text.replace('\u2060', '')  # Word joiner
                text = text.replace('\uFEFF', '')  # Zero-width no-break space
                text = text.replace('\u202F', ' ')  # Narrow no-break space -> regular space
                text = text.replace('\u00A0', ' ')  # Non-breaking space -> regular space
                # Ensure UTF-8 encoding
                try:
                    text = text.encode('utf-8', errors='replace').decode('utf-8')
                except Exception:
                    # If encoding fails, remove non-ASCII characters
                    text = text.encode('ascii', errors='ignore').decode('ascii')
                return text
            
            # Normalize and truncate strings
            query_safe = normalize_text(query)
            query_safe = query_safe[:10000] if len(query_safe) > 10000 else query_safe
            
            response_safe = normalize_text(response)
            response_safe = response_safe[:10000] if len(response_safe) > 10000 else response_safe
            
            feedback_text_safe = normalize_text(feedback_text)
            feedback_text_safe = feedback_text_safe[:5000] if len(feedback_text_safe) > 5000 else feedback_text_safe
            
            # Create new feedback entry
            feedback = AgentFeedback.objects.create(
                agent=message.conversation.agent,
                user=request.user,
                query=query_safe,
                response=response_safe,
                feedback_type=feedback_type,
                feedback_text=feedback_text_safe
            )
            
            return JsonResponse({
                'success': True,
                'message': 'Feedback submitted successfully',
                'feedback_id': feedback.id
            })
        except Exception as e:
            # Log the error and return a proper error response
            logger.error(f"Failed to create feedback: {str(e)}", exc_info=True)
            return JsonResponse({
                'success': False,
                'error': f'Failed to submit feedback: {str(e)}'
            }, status=500)
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        logger.error(f"Error submitting feedback: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to submit feedback: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["GET"])
def get_conversation_tool_calls(request, conversation_id):
    """Get all tool calls for a conversation."""
    try:
        conversation = get_object_or_404(Conversation, conversation_id=conversation_id)
        
        # Check access
        if not user_has_agent_access(request.user, conversation.agent):
            return JsonResponse({
                'success': False,
                'error': 'Access denied'
            }, status=403)
        
        # Get all tool calls for this conversation
        from .models import ToolCall
        tool_calls = ToolCall.objects.filter(
            conversation=conversation
        ).order_by('-created_at')
        
        # Format tool calls for response
        tool_calls_data = []
        for tool_call in tool_calls:
            tool_calls_data.append({
                'id': tool_call.id,
                'tool_name': tool_call.tool_name,
                'parameters': tool_call.parameters,
                'result_content': tool_call.result_content,
                'result_files': tool_call.result_files,
                'error': tool_call.error,
                'state': tool_call.state,
                'created_at': tool_call.created_at.isoformat() if tool_call.created_at else None,
                'completed_at': tool_call.completed_at.isoformat() if tool_call.completed_at else None,
            })
        
        return JsonResponse({
            'success': True,
            'tool_calls': tool_calls_data,
            'count': len(tool_calls_data)
        })
        
    except Conversation.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Conversation not found'
        }, status=404)
    except Exception as e:
        logger.error(f"Error getting tool calls: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to get tool calls: {str(e)}'
        }, status=500)

