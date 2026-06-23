"""Analytics views for agent usage and sharing statistics."""
import logging
from datetime import timedelta
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db.models import Sum, Count, Max
from django.db.models.functions import TruncDate
from .models import Agent, SharedAgentUsage, AgentShare, Conversation, ConversationMessage, AgentPublicShare

logger = logging.getLogger(__name__)


@login_required
def analytics_dashboard(request):
    """
    Full analytics dashboard page showing comprehensive agent analytics.
    """
    from datetime import timedelta
    
    # Get date range filter
    days = int(request.GET.get('days', 30))
    start_date = timezone.now() - timedelta(days=days)
    end_date = timezone.now()
    
    # Get all user's agents
    agents = Agent.objects.filter(user=request.user).select_related('model')
    
    # Get agents shared with user
    shared_with_me = Agent.objects.filter(
        shares__email=request.user.email,
        shares__is_accepted=True,
        shares__accepted_by=request.user
    ).exclude(user=request.user).distinct().select_related('model', 'user')
    
    # Calculate overall statistics
    total_agents = agents.count()
    published_agents = agents.filter(status='published').count()
    draft_agents = agents.filter(status='draft').count()
    testing_agents = agents.filter(status='testing').count()
    training_agents = agents.filter(status='training').count()
    
    # Get conversation and message stats for user's agents
    own_conversations = Conversation.objects.filter(agent__in=agents)
    own_messages = ConversationMessage.objects.filter(conversation__in=own_conversations)
    
    total_conversations = own_conversations.count()
    total_messages = own_messages.count()
    
    # Recent conversations (last 30 days)
    recent_conversations = own_conversations.filter(created_at__gte=start_date).count()
    recent_messages = own_messages.filter(created_at__gte=start_date).count()
    
    # Shared agent analytics - agents I've shared
    shared_out_count = AgentShare.objects.filter(
        agent__in=agents,
        is_accepted=True
    ).count()
    
    public_shares_count = AgentPublicShare.objects.filter(
        agent__in=agents,
        is_active=True
    ).count()
    
    # Get public share statistics
    public_shares = AgentPublicShare.objects.filter(
        agent__in=agents,
        is_active=True
    ).select_related('agent')
    
    total_public_access = public_shares.aggregate(
        total=Sum('access_count')
    )['total'] or 0
    
    # Recent public share access (accessed within the period)
    recent_public_shares = public_shares.filter(
        last_accessed_at__gte=start_date
    )
    recent_public_access = recent_public_shares.aggregate(
        total=Sum('access_count')
    )['total'] or 0
    
    # Get detailed public share analytics
    public_share_analytics = []
    for ps in public_shares:
        public_share_analytics.append({
            'agent': ps.agent,
            'token': ps.token[:8] + '...',  # Truncated for display
            'access_count': ps.access_count,
            'last_accessed': ps.last_accessed_at,
            'created_at': ps.created_at,
            'expires_at': ps.expires_at,
            'is_expired': ps.is_expired() if ps.expires_at else False,
        })
    
    # Sort by access count (most accessed first)
    public_share_analytics.sort(key=lambda x: x['access_count'], reverse=True)
    
    # Shared usage: include any record whose period overlaps the date range
    shared_usage = SharedAgentUsage.objects.filter(
        agent__in=agents
    ).filter(
        period_start__lt=end_date,
        period_end__gt=start_date
    ).exclude(used_by=request.user)
    
    shared_messages_count = shared_usage.aggregate(
        total=Sum('message_count')
    )['total'] or 0
    
    shared_conversations_count = shared_usage.aggregate(
        total=Sum('conversation_count')
    )['total'] or 0
    
    unique_users_count = shared_usage.values('used_by').distinct().count()
    
    # Per-agent detailed analytics
    agent_analytics = []
    for agent in agents:
        # Get agent's conversations and messages
        agent_convs = Conversation.objects.filter(agent=agent)
        agent_msgs = ConversationMessage.objects.filter(conversation__in=agent_convs)
        
        recent_agent_convs = agent_convs.filter(created_at__gte=start_date)
        recent_agent_msgs = agent_msgs.filter(created_at__gte=start_date)
        
        # Shared usage for this agent (period overlaps date range)
        agent_shared_usage = SharedAgentUsage.objects.filter(
            agent=agent,
            period_start__lt=end_date,
            period_end__gt=start_date
        ).exclude(used_by=request.user)
        
        agent_shared_messages = agent_shared_usage.aggregate(
            total=Sum('message_count')
        )['total'] or 0
        
        agent_shared_conversations = agent_shared_usage.aggregate(
            total=Sum('conversation_count')
        )['total'] or 0
        
        agent_unique_users = agent_shared_usage.values('used_by').distinct().count()
        
        # Get share counts
        email_shares = AgentShare.objects.filter(agent=agent, is_accepted=True).count()
        public_share = AgentPublicShare.objects.filter(agent=agent, is_active=True).first()
        
        agent_analytics.append({
            'agent': agent,
            'total_conversations': agent_convs.count(),
            'total_messages': agent_msgs.count(),
            'recent_conversations': recent_agent_convs.count(),
            'recent_messages': recent_agent_msgs.count(),
            'shared_messages': agent_shared_messages,
            'shared_conversations': agent_shared_conversations,
            'unique_users': agent_unique_users,
            'email_shares': email_shares,
            'has_public_share': bool(public_share),
            'public_share_views': public_share.access_count if public_share else 0,
        })
    
    # Sort by total messages (most active first)
    agent_analytics.sort(key=lambda x: x['total_messages'] + x['shared_messages'], reverse=True)
    
    # Per-user usage stats (period overlaps date range)
    per_user_stats = SharedAgentUsage.objects.filter(
        agent__in=agents,
        period_start__lt=end_date,
        period_end__gt=start_date
    ).exclude(
        used_by=request.user
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
    
    # Per-user stats for PUBLIC shares (period overlaps date range)
    public_user_stats = SharedAgentUsage.objects.filter(
        agent__in=agents,
        period_start__lt=end_date,
        period_end__gt=start_date,
        share__isnull=True
    ).exclude(
        used_by=request.user
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
    
    # Count unique public share users (period overlaps date range)
    public_unique_users = SharedAgentUsage.objects.filter(
        agent__in=agents,
        period_start__lt=end_date,
        period_end__gt=start_date,
        share__isnull=True
    ).exclude(used_by=request.user).values('used_by').distinct().count()
    
    # Total messages/conversations from public share users
    public_usage_totals = SharedAgentUsage.objects.filter(
        agent__in=agents,
        period_start__lt=end_date,
        period_end__gt=start_date,
        share__isnull=True
    ).exclude(used_by=request.user).aggregate(
        total_messages=Sum('message_count'),
        total_conversations=Sum('conversation_count')
    )
    public_messages_count = public_usage_totals['total_messages'] or 0
    public_conversations_count = public_usage_totals['total_conversations'] or 0
    
    # My usage of shared agents (period overlaps date range)
    my_shared_usage = SharedAgentUsage.objects.filter(
        agent__in=shared_with_me,
        used_by=request.user,
        period_start__lt=end_date,
        period_end__gt=start_date
    )
    
    my_shared_messages = my_shared_usage.aggregate(
        total=Sum('message_count')
    )['total'] or 0
    
    my_shared_conversations = my_shared_usage.aggregate(
        total=Sum('conversation_count')
    )['total'] or 0
    
    # Analytics for shared with me agents (period overlaps date range)
    shared_with_me_analytics = []
    for agent in shared_with_me:
        usage = SharedAgentUsage.objects.filter(
            agent=agent,
            used_by=request.user,
            period_start__lt=end_date,
            period_end__gt=start_date
        )
        msgs = usage.aggregate(total=Sum('message_count'))['total'] or 0
        convs = usage.aggregate(total=Sum('conversation_count'))['total'] or 0
        
        shared_with_me_analytics.append({
            'agent': agent,
            'owner': agent.user.username,
            'messages': msgs,
            'conversations': convs,
        })
    
    shared_with_me_analytics.sort(key=lambda x: x['messages'], reverse=True)
    
    context = {
        'days': days,
        'start_date': start_date,
        'end_date': end_date,
        
        # Agent counts
        'total_agents': total_agents,
        'published_agents': published_agents,
        'draft_agents': draft_agents,
        'testing_agents': testing_agents,
        'training_agents': training_agents,
        
        # Overall usage
        'total_conversations': total_conversations,
        'total_messages': total_messages,
        'recent_conversations': recent_conversations,
        'recent_messages': recent_messages,
        
        # Sharing stats
        'shared_out_count': shared_out_count,
        'public_shares_count': public_shares_count,
        'shared_messages_count': shared_messages_count,
        'shared_conversations_count': shared_conversations_count,
        'unique_users_count': unique_users_count,
        
        # Public share stats
        'total_public_access': total_public_access,
        'recent_public_access': recent_public_access,
        'public_share_analytics': public_share_analytics,
        
        # Per-agent analytics
        'agent_analytics': agent_analytics,
        
        # Per-user stats
        'per_user_stats': list(per_user_stats),
        
        # Public share user stats
        'public_user_stats': list(public_user_stats),
        'public_unique_users': public_unique_users,
        'public_messages_count': public_messages_count,
        'public_conversations_count': public_conversations_count,
        
        # Shared with me
        'shared_with_me_count': shared_with_me.count(),
        'my_shared_messages': my_shared_messages,
        'my_shared_conversations': my_shared_conversations,
        'shared_with_me_analytics': shared_with_me_analytics,
    }
    
    return render(request, 'dashboard/analytics_dashboard.html', context)


@login_required
@require_http_methods(["GET"])
def shared_agent_usage_analytics(request, slug=None):
    """
    Get shared agent usage analytics.
    
    If slug is provided, returns analytics for that specific agent.
    Otherwise, returns analytics for all agents owned by the user.
    """
    try:
        
        # Get date range from query params
        # Support both 'days' (backward compatibility) and explicit start/end dates
        if 'start_days' in request.GET and 'end_days' in request.GET:
            # New format: start_days (negative for past) and end_days (positive for future)
            start_days = int(request.GET.get('start_days', -30))
            end_days = int(request.GET.get('end_days', 5))
            now = timezone.now()
            start_date = now + timedelta(days=start_days)
            end_date = now + timedelta(days=end_days)
        else:
            # Legacy format: days (default: last 30 days)
            days = int(request.GET.get('days', 30))
            start_date = timezone.now() - timedelta(days=days)
            end_date = timezone.now()
        
        if slug:
            # Get analytics for specific agent
            agent = Agent.objects.get(slug=slug, user=request.user)
            agents = [agent]
        else:
            # Get all user's agents
            agents = Agent.objects.filter(user=request.user)
        
        analytics_data = {
            'agents': [],
            'total_stats': {
                'total_messages': 0,
                'total_conversations': 0,
                'unique_users': 0,
                'total_shares': 0,
            },
            'time_series': [],
            'per_user_stats': [],
        }
        
        all_agent_ids = list(agents.values_list('id', flat=True))
        
        if not all_agent_ids:
            return JsonResponse(analytics_data)
        
        # Time series: include records whose period overlaps the date range
        time_series_query = SharedAgentUsage.objects.filter(
            agent_id__in=all_agent_ids,
            period_start__lt=end_date,
            period_end__gt=start_date
        ).annotate(
            date=TruncDate('period_start')
        ).values('date').annotate(
            total_messages=Sum('message_count'),
            total_conversations=Sum('conversation_count'),
            unique_users=Count('used_by', distinct=True)
        ).order_by('date')
        
        time_series = []
        for entry in time_series_query:
            time_series.append({
                'date': entry['date'].isoformat() if entry['date'] else None,
                'messages': entry['total_messages'] or 0,
                'conversations': entry['total_conversations'] or 0,
                'unique_users': entry['unique_users'] or 0,
            })
        
        analytics_data['time_series'] = time_series
        
        # Per-user statistics (period overlaps date range)
        per_user_query = SharedAgentUsage.objects.filter(
            agent_id__in=all_agent_ids,
            period_start__lt=end_date,
            period_end__gt=start_date
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
        
        per_user_stats = []
        for entry in per_user_query:
            per_user_stats.append({
                'user_id': entry['used_by__id'],
                'username': entry['used_by__username'],
                'email': entry['used_by__email'],
                'total_messages': entry['total_messages'] or 0,
                'total_conversations': entry['total_conversations'] or 0,
                'agents_used': entry['agents_used'] or 0,
                'last_used': entry['last_used'].isoformat() if entry['last_used'] else None,
            })
        
        analytics_data['per_user_stats'] = per_user_stats
        
        # Per-agent statistics (period overlaps date range)
        for agent in agents:
            agent_usage = SharedAgentUsage.objects.filter(
                agent=agent,
                period_start__lt=end_date,
                period_end__gt=start_date
            )
            
            agent_stats = {
                'agent_id': agent.id,
                'agent_name': agent.name,
                'total_messages': agent_usage.aggregate(Sum('message_count'))['message_count__sum'] or 0,
                'total_conversations': agent_usage.aggregate(Sum('conversation_count'))['conversation_count__sum'] or 0,
                'unique_users': agent_usage.values('used_by').distinct().count(),
                'active_shares': AgentShare.objects.filter(
                    agent=agent,
                    is_accepted=True
                ).count(),
                'time_series': [],
            }
            
            # Time series for this agent
            agent_time_series = SharedAgentUsage.objects.filter(
                agent=agent,
                period_start__lt=end_date,
                period_end__gt=start_date
            ).annotate(
                date=TruncDate('period_start')
            ).values('date').annotate(
                total_messages=Sum('message_count'),
                total_conversations=Sum('conversation_count'),
                unique_users=Count('used_by', distinct=True)
            ).order_by('date')
            
            for entry in agent_time_series:
                agent_stats['time_series'].append({
                    'date': entry['date'].isoformat() if entry['date'] else None,
                    'messages': entry['total_messages'] or 0,
                    'conversations': entry['total_conversations'] or 0,
                    'unique_users': entry['unique_users'] or 0,
                })
            
            analytics_data['agents'].append(agent_stats)
            analytics_data['total_stats']['total_messages'] += agent_stats['total_messages']
            analytics_data['total_stats']['total_conversations'] += agent_stats['total_conversations']
            analytics_data['total_stats']['total_shares'] += agent_stats['active_shares']
        
        # Unique users across all agents (period overlaps date range)
        unique_users = SharedAgentUsage.objects.filter(
            agent_id__in=all_agent_ids,
            period_start__lt=end_date,
            period_end__gt=start_date
        ).values('used_by').distinct().count()
        
        analytics_data['total_stats']['unique_users'] = unique_users
        
        return JsonResponse(analytics_data)
        
    except Agent.DoesNotExist:
        return JsonResponse({'error': 'Agent not found'}, status=404)
    except Exception as e:
        logger.error(f"Error getting shared agent usage analytics: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Failed to get analytics data'}, status=500)


@login_required
@require_http_methods(["GET"])
def my_shared_agents_usage(request):
    """
    Get usage statistics for agents shared with the current user.
    """
    try:
        days = int(request.GET.get('days', 30))
        start_date = timezone.now() - timedelta(days=days)
        end_date = timezone.now()
        
        # Get agents shared with user
        shared_agents = Agent.objects.filter(
            shares__email=request.user.email,
            shares__is_accepted=True,
            shares__accepted_by=request.user
        ).distinct()
        
        analytics_data = {
            'agents': [],
            'total_stats': {
                'total_messages': 0,
                'total_conversations': 0,
                'agents_used': 0,
            },
            'time_series': [],
        }
        
        for agent in shared_agents:
            agent_usage = SharedAgentUsage.objects.filter(
                agent=agent,
                used_by=request.user,
                period_start__lt=end_date,
                period_end__gt=start_date
            )
            
            total_messages = agent_usage.aggregate(Sum('message_count'))['message_count__sum'] or 0
            total_conversations = agent_usage.aggregate(Sum('conversation_count'))['conversation_count__sum'] or 0
            
            if total_messages > 0 or total_conversations > 0:
                agent_stats = {
                    'agent_id': agent.id,
                    'agent_name': agent.name,
                    'owner': agent.user.username,
                    'total_messages': total_messages,
                    'total_conversations': total_conversations,
                    'time_series': [],
                }
                
                # Time series for this agent
                agent_time_series = SharedAgentUsage.objects.filter(
                    agent=agent,
                    used_by=request.user,
                    period_start__lt=end_date,
                    period_end__gt=start_date
                ).annotate(
                    date=TruncDate('period_start')
                ).values('date').annotate(
                    total_messages=Sum('message_count'),
                    total_conversations=Sum('conversation_count')
                ).order_by('date')
                
                for entry in agent_time_series:
                    agent_stats['time_series'].append({
                        'date': entry['date'].isoformat() if entry['date'] else None,
                        'messages': entry['total_messages'] or 0,
                        'conversations': entry['total_conversations'] or 0,
                    })
                
                analytics_data['agents'].append(agent_stats)
                analytics_data['total_stats']['total_messages'] += total_messages
                analytics_data['total_stats']['total_conversations'] += total_conversations
                analytics_data['total_stats']['agents_used'] += 1
        
        # Combined time series (period overlaps date range)
        combined_time_series = SharedAgentUsage.objects.filter(
            agent__in=shared_agents,
            used_by=request.user,
            period_start__lt=end_date,
            period_end__gt=start_date
        ).annotate(
            date=TruncDate('period_start')
        ).values('date').annotate(
            total_messages=Sum('message_count'),
            total_conversations=Sum('conversation_count')
        ).order_by('date')
        
        for entry in combined_time_series:
            analytics_data['time_series'].append({
                'date': entry['date'].isoformat() if entry['date'] else None,
                'messages': entry['total_messages'] or 0,
                'conversations': entry['total_conversations'] or 0,
            })
        
        return JsonResponse(analytics_data)
        
    except Exception as e:
        logger.error(f"Error getting my shared agents usage: {str(e)}", exc_info=True)
        return JsonResponse({'error': 'Failed to get analytics data'}, status=500)

