"""Dashboard views for connector CRUD operations with AJAX support."""
import logging
import json
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.contrib import messages
from .models import Connector, ConnectorType
from agents_app.models import Agent

logger = logging.getLogger(__name__)


@login_required
def connectors_list(request):
    """List all connectors for the user."""
    connectors = Connector.objects.filter(user=request.user)
    agents = Agent.objects.filter(user=request.user, status='published')
    
    context = {
        'connectors': connectors,
        'agents': agents,
        'connector_types': ConnectorType.choices,
    }
    return render(request, 'connectors/connectors_list.html', context)


@login_required
@require_http_methods(["POST"])
def connector_create_ajax(request):
    """Create a new connector via AJAX."""
    try:
        data = json.loads(request.body)
        
        connector_type = data.get('connector_type')
        name = data.get('name')
        base_url = data.get('base_url', '').strip()
        client_id = data.get('client_id', '').strip()
        client_secret = data.get('client_secret', '').strip()
        
        # Set default base_url for Google, Microsoft, and GitHub if not provided
        if not base_url:
            if connector_type == ConnectorType.GOOGLE:
                base_url = 'https://www.googleapis.com'
            elif connector_type == ConnectorType.MICROSOFT:
                base_url = 'https://graph.microsoft.com'
            elif connector_type == ConnectorType.GITHUB:
                base_url = 'https://api.github.com'
        
        # Validation
        if not all([connector_type, name]):
            return JsonResponse({
                'success': False,
                'error': 'Connector type and name are required'
            }, status=400)
        
        if connector_type not in [ct[0] for ct in ConnectorType.choices]:
            return JsonResponse({
                'success': False,
                'error': f'Invalid connector type. Must be one of: {[ct[0] for ct in ConnectorType.choices]}'
            }, status=400)
        
        # Check for duplicate
        existing = Connector.objects.filter(
            user=request.user,
            connector_type=connector_type,
            base_url=base_url
        ).first()
        
        if existing:
            return JsonResponse({
                'success': False,
                'error': 'A connector with this type and base URL already exists'
            }, status=400)
        
        # Create connector
        connector = Connector.objects.create(
            name=name,
            connector_type=connector_type,
            user=request.user,
            base_url=base_url,
            client_id=client_id,
            client_secret=client_secret,
            site_url=request.build_absolute_uri('/'),
            status='disconnected'
        )
        
        # Build callback URL (strip query parameters and fragment - they shouldn't be in the registered callback URL)
        from urllib.parse import urlparse, urlunparse
        callback_uri = request.build_absolute_uri(f'/api/connectors/{connector.id}/oauth/callback/')
        parsed = urlparse(callback_uri)
        callback_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, '', ''))
        
        return JsonResponse({
            'success': True,
            'connector': {
                'id': connector.id,
                'name': connector.name,
                'connector_type': connector.connector_type,
                'connector_type_display': connector.get_connector_type_display(),
                'status': connector.status,
                'status_display': connector.get_status_display(),
                'base_url': connector.base_url,
                'created_at': connector.created_at.isoformat(),
            },
            'callback_url': callback_url,
            'note': f'IMPORTANT: Register this callback URL in your OAuth provider: {callback_url}',
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        logger.error(f"Error creating connector: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to create connector: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["GET"])
def connector_get_ajax(request, connector_id):
    """Get connector details via AJAX."""
    try:
        connector = get_object_or_404(Connector, id=connector_id, user=request.user)
        
        return JsonResponse({
            'success': True,
            'connector': {
                'id': connector.id,
                'name': connector.name,
                'connector_type': connector.connector_type,
                'connector_type_display': connector.get_connector_type_display(),
                'status': connector.status,
                'status_display': connector.get_status_display(),
                'base_url': connector.base_url,
                'client_id': connector.client_id,
                'client_secret': '***' if connector.client_secret else '',
                'configuration': connector.configuration,
                'connected_at': connector.connected_at.isoformat() if connector.connected_at else None,
                'last_sync_at': connector.last_sync_at.isoformat() if connector.last_sync_at else None,
                'created_at': connector.created_at.isoformat(),
                'updated_at': connector.updated_at.isoformat(),
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting connector: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to get connector: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["POST"])
def connector_update_ajax(request, connector_id):
    """Update connector via AJAX."""
    try:
        connector = get_object_or_404(Connector, id=connector_id, user=request.user)
        data = json.loads(request.body)
        
        # Update fields
        if 'name' in data:
            connector.name = data['name']
        
        if 'base_url' in data:
            base_url = data['base_url'].strip()
            if base_url:
                connector.base_url = base_url
        
        if 'client_id' in data:
            connector.client_id = data['client_id'].strip()
        
        if 'client_secret' in data:
            client_secret = data['client_secret']
            # Only update if provided and not masked/empty
            if client_secret and client_secret.strip() and client_secret.strip() != '***':
                connector.client_secret = client_secret.strip()
        
        if 'configuration' in data:
            connector.configuration = data['configuration']
        
        connector.save()
        
        return JsonResponse({
            'success': True,
            'connector': {
                'id': connector.id,
                'name': connector.name,
                'connector_type': connector.connector_type,
                'connector_type_display': connector.get_connector_type_display(),
                'status': connector.status,
                'status_display': connector.get_status_display(),
                'base_url': connector.base_url,
                'configuration': connector.configuration,
                'updated_at': connector.updated_at.isoformat(),
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        logger.error(f"Error updating connector: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to update connector: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["POST"])
def connector_delete_ajax(request, connector_id):
    """Delete connector via AJAX."""
    try:
        connector = get_object_or_404(Connector, id=connector_id, user=request.user)
        connector_name = connector.name
        connector.delete()
        
        return JsonResponse({
            'success': True,
            'message': f'Connector "{connector_name}" deleted successfully'
        })
        
    except Exception as e:
        logger.error(f"Error deleting connector: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to delete connector: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["POST"])
def connector_connect_ajax(request, connector_id):
    """Initiate OAuth connection via AJAX."""
    try:
        connector = get_object_or_404(Connector, id=connector_id, user=request.user)
        
        # Build callback URL
        from urllib.parse import urlparse, urlunparse
        callback_uri = request.build_absolute_uri(f'/api/connectors/{connector_id}/oauth/callback/')
        parsed = urlparse(callback_uri)
        callback_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, '', ''))
        
        # Import OAuth clients
        from .jira_oauth import JIRAOAuthClient, ConfluenceOAuthClient
        from .gitlab_oauth import GitLabOAuthClient
        from .github_oauth import GitHubOAuthClient
        from .google_oauth import GoogleOAuthClient
        from .microsoft_oauth import MicrosoftOAuthClient
        
        auth_data = None
        
        if connector.connector_type == ConnectorType.JIRA:
            client = JIRAOAuthClient(connector)
            auth_data = client.get_authorization_url(callback_url)
            auth_data['note'] = f'Make sure this callback URL is registered in Atlassian Developer Console: {callback_url}'
            
        elif connector.connector_type == ConnectorType.CONFLUENCE:
            client = ConfluenceOAuthClient(connector)
            auth_data = client.get_authorization_url(callback_url)
            
        elif connector.connector_type == ConnectorType.GITLAB:
            client = GitLabOAuthClient(connector)
            auth_data = client.get_authorization_url(callback_url)
            
        elif connector.connector_type == ConnectorType.GITHUB:
            client = GitHubOAuthClient(connector)
            auth_data = client.get_authorization_url(callback_url)
            auth_data['note'] = f'Make sure this callback URL is registered in GitHub OAuth App settings: {callback_url}'
            
        elif connector.connector_type == ConnectorType.GOOGLE:
            client = GoogleOAuthClient(connector)
            auth_data = client.get_authorization_url(callback_url)
            
        elif connector.connector_type == ConnectorType.MICROSOFT:
            client = MicrosoftOAuthClient(connector)
            auth_data = client.get_authorization_url(callback_url)
            
        else:
            return JsonResponse({
                'success': False,
                'error': f'OAuth not implemented for connector type: {connector.connector_type}'
            }, status=400)
        
        if auth_data:
            return JsonResponse({
                'success': True,
                'authorization_url': auth_data.get('authorization_url'),
                'state': auth_data.get('state'),
                'callback_url': auth_data.get('callback_url', callback_url),
                'note': auth_data.get('note', ''),
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'Failed to generate authorization URL'
            }, status=500)
            
    except Exception as e:
        logger.error(f"Error initiating OAuth: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to initiate OAuth: {str(e)}'
        }, status=500)


@login_required
@require_http_methods(["POST"])
def connector_sync_ajax(request, connector_id):
    """Start sync operation via AJAX."""
    try:
        from .models import ConnectorSync
        
        data = json.loads(request.body)
        agent_id = data.get('agent_id')
        
        if not agent_id:
            return JsonResponse({
                'success': False,
                'error': 'agent_id is required'
            }, status=400)
        
        connector = get_object_or_404(Connector, id=connector_id, user=request.user)
        agent = get_object_or_404(Agent, id=agent_id, user=request.user)
        
        if connector.status != 'connected':
            return JsonResponse({
                'success': False,
                'error': 'Connector is not connected. Please complete OAuth flow first.'
            }, status=400)
        
        # Create sync record
        sync = ConnectorSync.objects.create(
            connector=connector,
            agent=agent,
            status='running',
            sync_type=f'{connector.connector_type}_sync',
            started_at=timezone.now()
        )
        
        # Start sync task (async)
        from .tasks import sync_connector_data
        sync_connector_data.delay(sync.id)
        
        return JsonResponse({
            'success': True,
            'sync_id': sync.id,
            'status': sync.status,
            'message': 'Sync started',
        })
            
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        logger.error(f"Error starting sync: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'Failed to start sync: {str(e)}'
        }, status=500)

