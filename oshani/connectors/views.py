"""Views for connector OAuth and data synchronization."""
import logging
import requests
from django.shortcuts import redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .models import Connector, ConnectorSync, ConnectorType
from .jira_oauth import JIRAOAuthClient, ConfluenceOAuthClient
from .gitlab_oauth import GitLabOAuthClient
from .github_oauth import GitHubOAuthClient
from .google_oauth import GoogleOAuthClient
from .microsoft_oauth import MicrosoftOAuthClient
from agents_app.models import Agent, TrainingData
from django.conf import settings

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_connector(request):
    """Create a new connector."""
    try:
        connector_type = request.data.get('connector_type')
        name = request.data.get('name')
        base_url = request.data.get('base_url')
        client_id = request.data.get('client_id')
        client_secret = request.data.get('client_secret')
        
        # Set default base_url for Google, Microsoft, and GitHub if not provided
        if not base_url:
            if connector_type == ConnectorType.GOOGLE:
                base_url = 'https://www.googleapis.com'
            elif connector_type == ConnectorType.MICROSOFT:
                base_url = 'https://graph.microsoft.com'
            elif connector_type == ConnectorType.GITHUB:
                base_url = 'https://api.github.com'
        
        if not all([connector_type, name, base_url, client_id, client_secret]):
            return Response(
                {'error': 'Missing required fields: connector_type, name, base_url, client_id, client_secret'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if connector_type not in [ct[0] for ct in ConnectorType.choices]:
            return Response(
                {'error': f'Invalid connector_type. Must be one of: {[ct[0] for ct in ConnectorType.choices]}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
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
        
        return Response({
            'id': connector.id,
            'name': connector.name,
            'connector_type': connector.connector_type,
            'status': connector.status,
            'base_url': connector.base_url,
            'callback_url': callback_url,
            'note': f'IMPORTANT: Register this callback URL in your OAuth provider: {callback_url}',
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        logger.error(f"Error creating connector: {str(e)}", exc_info=True)
        return Response(
            {'error': f'Failed to create connector: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_connectors(request):
    """List all connectors for the authenticated user."""
    connectors = Connector.objects.filter(user=request.user)
    
    return Response([{
        'id': c.id,
        'name': c.name,
        'connector_type': c.connector_type,
        'status': c.status,
        'base_url': c.base_url,
        'connected_at': c.connected_at.isoformat() if c.connected_at else None,
        'last_sync_at': c.last_sync_at.isoformat() if c.last_sync_at else None,
    } for c in connectors])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def initiate_oauth(request, connector_id):
    """Initiate OAuth flow for a connector."""
    try:
        connector = get_object_or_404(Connector, id=connector_id, user=request.user)
        
        # Build callback URL (strip query parameters and fragment - they shouldn't be in the registered callback URL)
        from urllib.parse import urlparse, urlunparse
        callback_uri = request.build_absolute_uri(f'/api/connectors/{connector_id}/oauth/callback/')
        # Parse and reconstruct without query parameters or fragment
        parsed = urlparse(callback_uri)
        callback_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, '', ''))
        
        if connector.connector_type == ConnectorType.JIRA:
            client = JIRAOAuthClient(connector)
            auth_data = client.get_authorization_url(callback_url)
            
            return Response({
                'authorization_url': auth_data['authorization_url'],
                'state': auth_data['state'],
                'callback_url': auth_data.get('callback_url', callback_url),  # Include for reference
                'note': f'Make sure this callback URL is registered in Atlassian Developer Console: {callback_url}',
            })
            
        elif connector.connector_type == ConnectorType.CONFLUENCE:
            client = ConfluenceOAuthClient(connector)
            auth_data = client.get_authorization_url(callback_url)
            
            return Response({
                'authorization_url': auth_data['authorization_url'],
                'state': auth_data['state'],
            })
            
        elif connector.connector_type == ConnectorType.GITLAB:
            client = GitLabOAuthClient(connector)
            auth_data = client.get_authorization_url(callback_url)
            
            return Response({
                'authorization_url': auth_data['authorization_url'],
                'state': auth_data['state'],
            })
        
        elif connector.connector_type == ConnectorType.GITHUB:
            client = GitHubOAuthClient(connector)
            auth_data = client.get_authorization_url(callback_url)
            
            return Response({
                'authorization_url': auth_data['authorization_url'],
                'state': auth_data['state'],
                'callback_url': callback_url,
                'note': f'Make sure this callback URL is registered in GitHub OAuth App settings: {callback_url}',
            })
        
        elif connector.connector_type == ConnectorType.GOOGLE:
            client = GoogleOAuthClient(connector)
            auth_data = client.get_authorization_url(callback_url)
            
            return Response({
                'authorization_url': auth_data['authorization_url'],
                'state': auth_data['state'],
            })
        
        elif connector.connector_type == ConnectorType.MICROSOFT:
            client = MicrosoftOAuthClient(connector)
            auth_data = client.get_authorization_url(callback_url)
            
            return Response({
                'authorization_url': auth_data['authorization_url'],
                'state': auth_data['state'],
            })
        
        else:
            return Response(
                {'error': f'OAuth not implemented for connector type: {connector.connector_type}'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
    except Exception as e:
        logger.error(f"Error initiating OAuth: {str(e)}", exc_info=True)
        return Response(
            {'error': f'Failed to initiate OAuth: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def oauth_callback(request, connector_id):
    """Handle OAuth callback.
    
    Note: This view may be called in async contexts (Channels), so all operations
    should complete quickly. HTTP requests have timeouts to prevent hanging.
    """
    try:
        connector = get_object_or_404(Connector, id=connector_id, user=request.user)
        
        if connector.connector_type == ConnectorType.JIRA:
            code = request.GET.get('code')
            state = request.GET.get('state')
            error = request.GET.get('error')
            error_description = request.GET.get('error_description', '')
            
            # Handle OAuth errors from Atlassian
            if error:
                callback_url = request.build_absolute_uri(f'/api/connectors/{connector_id}/oauth/callback/')
                error_html = f'''
                <html>
                <body style="font-family: Arial, sans-serif; padding: 20px;">
                    <h1 style="color: #d32f2f;">OAuth Error</h1>
                    <p><strong>Error:</strong> {error}</p>
                    {f'<p><strong>Description:</strong> {error_description}</p>' if error_description else ''}
                    {f'<p><strong>Callback URL used:</strong> <code>{callback_url}</code></p>' if 'callback' in error.lower() or 'redirect' in error.lower() else ''}
                    <hr>
                    <h2>How to Fix:</h2>
                    <ol>
                        <li>Go to <a href="https://developer.atlassian.com/console/myapps/" target="_blank">Atlassian Developer Console</a></li>
                        <li>Select your app</li>
                        <li>Go to <strong>APIS AND FEATURES</strong> → <strong>OAuth 2.0 (3LO)</strong></li>
                        <li>Under <strong>Authorization callback URL</strong>, add this exact URL:</li>
                        <li><code style="background: #f5f5f5; padding: 10px; display: block; margin: 10px 0;">{callback_url}</code></li>
                        <li>Make sure it matches <strong>EXACTLY</strong> (including https://, domain, path, and trailing slash)</li>
                        <li>Save the changes</li>
                        <li>Try connecting again</li>
                    </ol>
                    <p><a href="javascript:history.back()">Go Back</a></p>
                </body>
                </html>
                '''
                return HttpResponse(error_html, status=400)
            
            if not code or not state:
                callback_url = request.build_absolute_uri(f'/api/connectors/{connector_id}/oauth/callback/')
                return HttpResponse(
                    f'<html><body><h1>OAuth Error</h1><p>Missing code or state parameter</p><p>Callback URL: {callback_url}</p></body></html>',
                    status=400
                )
            
            client = JIRAOAuthClient(connector)
            # Build callback URL without query parameters for OAuth token exchange
            from urllib.parse import urlparse, urlunparse
            callback_uri = request.build_absolute_uri(f'/api/connectors/{connector_id}/oauth/callback/')
            parsed = urlparse(callback_uri)
            callback_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, '', ''))
            result = client.handle_oauth_callback(code, state, callback_url)
            
            if result.get('success'):
                # Check if there's a redirect parameter (from training page)
                redirect_url = request.GET.get('redirect', f'/dashboard/connectors/{connector_id}/?connected=1')
                # Add success message to redirect URL
                from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
                parsed = urlparse(redirect_url)
                params = parse_qs(parsed.query)
                params['connected'] = ['1']
                new_query = urlencode(params, doseq=True)
                redirect_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
                return redirect(redirect_url)
            else:
                return HttpResponse(
                    '<html><body><h1>OAuth Error</h1><p>Failed to complete OAuth flow</p></body></html>',
                    status=400
                )
                
        elif connector.connector_type == ConnectorType.CONFLUENCE:
            code = request.GET.get('code')
            state = request.GET.get('state')
            
            if not code or not state:
                return HttpResponse(
                    '<html><body><h1>OAuth Error</h1><p>Missing code or state</p></body></html>',
                    status=400
                )
            
            client = ConfluenceOAuthClient(connector)
            result = client.handle_oauth_callback(code, state)
            
            if result.get('success'):
                # Check if there's a redirect parameter (from training page)
                redirect_url = request.GET.get('redirect', f'/dashboard/connectors/{connector_id}/?connected=1')
                # Add success message to redirect URL
                from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
                parsed = urlparse(redirect_url)
                params = parse_qs(parsed.query)
                params['connected'] = ['1']
                new_query = urlencode(params, doseq=True)
                redirect_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
                return redirect(redirect_url)
            else:
                return HttpResponse(
                    '<html><body><h1>OAuth Error</h1><p>Failed to complete OAuth flow</p></body></html>',
                    status=400
                )
        
        elif connector.connector_type == ConnectorType.GITLAB:
            code = request.GET.get('code')
            state = request.GET.get('state')
            
            if not code or not state:
                return HttpResponse(
                    '<html><body><h1>OAuth Error</h1><p>Missing code or state</p></body></html>',
                    status=400
                )
            
            client = GitLabOAuthClient(connector)
            result = client.handle_oauth_callback(code, state)
            
            if result.get('success'):
                # Check if there's a redirect parameter (from training page)
                redirect_url = request.GET.get('redirect', f'/dashboard/connectors/{connector_id}/?connected=1')
                # Add success message to redirect URL
                from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
                parsed = urlparse(redirect_url)
                params = parse_qs(parsed.query)
                params['connected'] = ['1']
                new_query = urlencode(params, doseq=True)
                redirect_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
                return redirect(redirect_url)
            else:
                return HttpResponse(
                    '<html><body><h1>OAuth Error</h1><p>Failed to complete OAuth flow</p></body></html>',
                    status=400
                )
        
        elif connector.connector_type == ConnectorType.GITHUB:
            code = request.GET.get('code')
            state = request.GET.get('state')
            
            if not code or not state:
                return HttpResponse(
                    '<html><body><h1>OAuth Error</h1><p>Missing code or state</p></body></html>',
                    status=400
                )
            
            client = GitHubOAuthClient(connector)
            result = client.handle_oauth_callback(code, state)
            
            if result.get('success'):
                # Check if there's a redirect parameter (from training page)
                redirect_url = request.GET.get('redirect', f'/dashboard/connectors/{connector_id}/?connected=1')
                # Add success message to redirect URL
                from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
                parsed = urlparse(redirect_url)
                params = parse_qs(parsed.query)
                params['connected'] = ['1']
                new_query = urlencode(params, doseq=True)
                redirect_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
                return redirect(redirect_url)
            else:
                return HttpResponse(
                    '<html><body><h1>OAuth Error</h1><p>Failed to complete OAuth flow</p></body></html>',
                    status=400
                )
        
        elif connector.connector_type == ConnectorType.GOOGLE:
            code = request.GET.get('code')
            state = request.GET.get('state')
            
            if not code or not state:
                return HttpResponse(
                    '<html><body><h1>OAuth Error</h1><p>Missing code or state</p></body></html>',
                    status=400
                )
            
            # Use the same redirect_uri as the authorization request (Google requires exact match)
            from urllib.parse import urlparse, urlunparse
            callback_uri = request.build_absolute_uri(f'/api/connectors/{connector_id}/oauth/callback/')
            parsed = urlparse(callback_uri)
            redirect_uri = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, '', ''))
            
            client = GoogleOAuthClient(connector)
            result = client.handle_oauth_callback(code, state, redirect_uri=redirect_uri)
            
            if result.get('success'):
                # Check if there's a redirect parameter (from training page)
                redirect_url = request.GET.get('redirect', f'/dashboard/connectors/{connector_id}/?connected=1')
                # Add success message to redirect URL
                from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
                parsed = urlparse(redirect_url)
                params = parse_qs(parsed.query)
                params['connected'] = ['1']
                new_query = urlencode(params, doseq=True)
                redirect_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
                return redirect(redirect_url)
            else:
                return HttpResponse(
                    '<html><body><h1>OAuth Error</h1><p>Failed to complete OAuth flow</p></body></html>',
                    status=400
                )
        
        elif connector.connector_type == ConnectorType.MICROSOFT:
            code = request.GET.get('code')
            state = request.GET.get('state')
            
            if not code or not state:
                return HttpResponse(
                    '<html><body><h1>OAuth Error</h1><p>Missing code or state</p></body></html>',
                    status=400
                )
            
            client = MicrosoftOAuthClient(connector)
            result = client.handle_oauth_callback(code, state)
            
            if result.get('success'):
                # Check if there's a redirect parameter (from training page)
                redirect_url = request.GET.get('redirect', f'/dashboard/connectors/{connector_id}/?connected=1')
                # Add success message to redirect URL
                from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
                parsed = urlparse(redirect_url)
                params = parse_qs(parsed.query)
                params['connected'] = ['1']
                new_query = urlencode(params, doseq=True)
                redirect_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
                return redirect(redirect_url)
            else:
                return HttpResponse(
                    '<html><body><h1>OAuth Error</h1><p>Failed to complete OAuth flow</p></body></html>',
                    status=400
                )
        
        else:
            return HttpResponse(
                '<html><body><h1>OAuth Error</h1><p>Unsupported connector type</p></body></html>',
                status=400
            )
            
    except requests.exceptions.Timeout as e:
        logger.error(f"OAuth callback timeout for connector {connector_id}: {str(e)}", exc_info=True)
        return HttpResponse(
            '<html><body><h1>OAuth Error</h1><p>Request timed out. Please try again.</p></body></html>',
            status=504
        )
    except Exception as e:
        logger.error(f"Error in OAuth callback: {str(e)}", exc_info=True)
        return HttpResponse(
            f'<html><body><h1>OAuth Error</h1><p>{str(e)}</p></body></html>',
            status=500
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_data(request, connector_id):
    """Sync data from connector to agent training data."""
    try:
        connector = get_object_or_404(Connector, id=connector_id, user=request.user)
        agent_id = request.data.get('agent_id')
        
        if not agent_id:
            return Response(
                {'error': 'agent_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        agent = get_object_or_404(Agent, id=agent_id, user=request.user)
        
        if connector.status != 'connected':
            return Response(
                {'error': 'Connector is not connected. Please complete OAuth flow first.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
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
        
        return Response({
            'sync_id': sync.id,
            'status': sync.status,
            'message': 'Sync started',
        })
        
    except Exception as e:
        logger.error(f"Error starting sync: {str(e)}", exc_info=True)
        return Response(
            {'error': f'Failed to start sync: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_sync_status(request, connector_id, sync_id):
    """Get status of a sync operation."""
    try:
        connector = get_object_or_404(Connector, id=connector_id, user=request.user)
        sync = get_object_or_404(ConnectorSync, id=sync_id, connector=connector)
        
        return Response({
            'id': sync.id,
            'status': sync.status,
            'sync_type': sync.sync_type,
            'items_synced': sync.items_synced,
            'items_failed': sync.items_failed,
            'started_at': sync.started_at.isoformat() if sync.started_at else None,
            'completed_at': sync.completed_at.isoformat() if sync.completed_at else None,
            'error_message': sync.error_message,
        })
        
    except Exception as e:
        logger.error(f"Error getting sync status: {str(e)}", exc_info=True)
        return Response(
            {'error': f'Failed to get sync status: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_connector_data(request, connector_id):
    """Get available data from connector (projects, spaces, etc.) without syncing."""
    try:
        connector = get_object_or_404(Connector, id=connector_id, user=request.user)
        
        if connector.status != 'connected':
            return Response(
                {'error': 'Connector is not connected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if connector.connector_type == ConnectorType.JIRA:
            client = JIRAOAuthClient(connector)
            projects = client.fetch_projects()
            return Response({
                'projects': projects,
                'type': 'jira',
            })
            
        elif connector.connector_type == ConnectorType.CONFLUENCE:
            client = ConfluenceOAuthClient(connector)
            spaces = client.fetch_spaces()
            return Response({
                'spaces': spaces,
                'type': 'confluence',
            })
            
        elif connector.connector_type == ConnectorType.GITLAB:
            client = GitLabOAuthClient(connector)
            projects = client.fetch_projects()
            return Response({
                'projects': projects,
                'type': 'gitlab',
            })
        
        elif connector.connector_type == ConnectorType.GITHUB:
            client = GitHubOAuthClient(connector)
            repositories = client.fetch_repositories()
            return Response({
                'repositories': repositories,
                'type': 'github',
            })
        
        elif connector.connector_type == ConnectorType.GOOGLE:
            client = GoogleOAuthClient(connector)
            sync_type = connector.configuration.get('sync_type', 'drive')
            
            if sync_type == 'drive':
                files = client.fetch_drive_files(limit=50)
                return Response({
                    'files': files,
                    'type': 'google_drive',
                })
            elif sync_type == 'docs':
                docs = client.fetch_docs(limit=50)
                return Response({
                    'docs': docs,
                    'type': 'google_docs',
                })
            elif sync_type == 'sheets':
                sheets = client.fetch_sheets(limit=50)
                return Response({
                    'sheets': sheets,
                    'type': 'google_sheets',
                })
            else:
                files = client.fetch_drive_files(limit=50)
                return Response({
                    'files': files,
                    'type': 'google_drive',
                })
        
        elif connector.connector_type == ConnectorType.MICROSOFT:
            client = MicrosoftOAuthClient(connector)
            sync_type = connector.configuration.get('sync_type', 'onedrive')
            
            if sync_type == 'onedrive':
                files = client.fetch_onedrive_files(limit=50)
                return Response({
                    'files': files,
                    'type': 'microsoft_onedrive',
                })
            elif sync_type == 'sharepoint':
                sites = client.fetch_sharepoint_sites(limit=50)
                return Response({
                    'sites': sites,
                    'type': 'microsoft_sharepoint',
                })
            else:
                files = client.fetch_onedrive_files(limit=50)
                return Response({
                    'files': files,
                    'type': 'microsoft_onedrive',
                })
        
        else:
            return Response(
                {'error': f'Data fetching not implemented for: {connector.connector_type}'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
    except Exception as e:
        logger.error(f"Error getting connector data: {str(e)}", exc_info=True)
        return Response(
            {'error': f'Failed to get connector data: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
