"""Connector validation and status checking."""
import logging
import requests
from typing import Dict, Any, Optional
from django.utils import timezone
from .models import Connector, ConnectorType
from .jira_oauth import JIRAOAuthClient, ConfluenceOAuthClient
from .gitlab_oauth import GitLabOAuthClient
from .github_oauth import GitHubOAuthClient
from .google_oauth import GoogleOAuthClient
from .microsoft_oauth import MicrosoftOAuthClient

logger = logging.getLogger(__name__)


class ConnectorValidator:
    """Validates connector settings and connection status."""
    
    @staticmethod
    def validate_connector(connector: Connector) -> Dict[str, Any]:
        """Validate a connector's settings and connection status.
        
        Args:
            connector: The connector to validate
            
        Returns:
            Dict with validation results:
            {
                'valid': bool,
                'status': str,  # 'connected', 'disconnected', 'error'
                'message': str,
                'details': dict
            }
        """
        result = {
            'valid': False,
            'status': 'disconnected',
            'message': '',
            'details': {}
        }
        
        try:
            # Step 1: Validate basic settings
            if not connector.base_url:
                result['message'] = 'Base URL is required'
                result['status'] = 'error'
                return result
            
            if not connector.client_id or not connector.client_secret:
                result['message'] = 'OAuth credentials (Client ID and Secret) are required'
                result['status'] = 'error'
                return result
            
            # Step 2: Check if connector has access token
            if not connector.access_token:
                result['message'] = 'Not authenticated. Please complete OAuth flow.'
                result['status'] = 'disconnected'
                return result
            
            # Step 3: Check if token is expired
            if not connector.is_token_valid():
                result['message'] = 'Access token has expired. Please reconnect.'
                result['status'] = 'error'
                return result
            
            # Step 4: Test actual connection based on connector type
            if connector.connector_type == ConnectorType.JIRA:
                return ConnectorValidator._validate_jira(connector)
            elif connector.connector_type == ConnectorType.CONFLUENCE:
                return ConnectorValidator._validate_confluence(connector)
            elif connector.connector_type == ConnectorType.GITLAB:
                return ConnectorValidator._validate_gitlab(connector)
            elif connector.connector_type == ConnectorType.GITHUB:
                return ConnectorValidator._validate_github(connector)
            elif connector.connector_type == ConnectorType.GOOGLE:
                return ConnectorValidator._validate_google(connector)
            elif connector.connector_type == ConnectorType.MICROSOFT:
                return ConnectorValidator._validate_microsoft(connector)
            else:
                result['message'] = f'Unsupported connector type: {connector.connector_type}'
                result['status'] = 'error'
                return result
                
        except Exception as e:
            logger.error(f"Error validating connector {connector.id}: {str(e)}", exc_info=True)
            result['message'] = f'Validation error: {str(e)}'
            result['status'] = 'error'
            return result
    
    @staticmethod
    def _validate_jira(connector: Connector) -> Dict[str, Any]:
        """Validate JIRA connector."""
        result = {
            'valid': False,
            'status': 'disconnected',
            'message': '',
            'details': {}
        }
        
        try:
            client = JIRAOAuthClient(connector)
            headers = client._get_headers()
            api_base_url = client._get_api_base_url()
            
            # Test connection by fetching current user
            api_url = f"{api_base_url}/rest/api/3/myself"
            response = requests.get(api_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                user_data = response.json()
                result['valid'] = True
                result['status'] = 'connected'
                result['message'] = 'Connection successful'
                result['details'] = {
                    'user': user_data.get('displayName', user_data.get('name', 'Unknown')),
                    'email': user_data.get('emailAddress', 'N/A'),
                    'account_id': user_data.get('accountId', 'N/A'),
                }
            elif response.status_code == 401:
                result['message'] = 'Authentication failed. Token may be invalid or expired.'
                result['status'] = 'error'
                result['details'] = {'http_status': 401}
            elif response.status_code == 403:
                result['message'] = 'Access forbidden. Check OAuth app permissions.'
                result['status'] = 'error'
                result['details'] = {'http_status': 403}
            else:
                result['message'] = f'Connection test failed with status {response.status_code}'
                result['status'] = 'error'
                result['details'] = {'http_status': response.status_code}
                
        except Exception as e:
            logger.error(f"Error validating JIRA connector: {str(e)}", exc_info=True)
            result['message'] = f'Connection error: {str(e)}'
            result['status'] = 'error'
        
        return result
    
    @staticmethod
    def _validate_confluence(connector: Connector) -> Dict[str, Any]:
        """Validate Confluence connector."""
        result = {
            'valid': False,
            'status': 'disconnected',
            'message': '',
            'details': {}
        }
        
        try:
            client = ConfluenceOAuthClient(connector)
            headers = client._get_headers()
            
            # Test connection by fetching current user
            api_url = f"{connector.base_url.rstrip('/')}/rest/api/user/current"
            response = requests.get(api_url, headers=headers)
            
            if response.status_code == 200:
                user_data = response.json()
                result['valid'] = True
                result['status'] = 'connected'
                result['message'] = 'Connection successful'
                result['details'] = {
                    'user': user_data.get('displayName', user_data.get('username', 'Unknown')),
                    'email': user_data.get('email', 'N/A'),
                    'user_key': user_data.get('userKey', 'N/A'),
                }
            elif response.status_code == 401:
                result['message'] = 'Authentication failed. Token may be invalid or expired.'
                result['status'] = 'error'
                result['details'] = {'http_status': 401}
            elif response.status_code == 403:
                result['message'] = 'Access forbidden. Check OAuth app permissions.'
                result['status'] = 'error'
                result['details'] = {'http_status': 403}
            else:
                result['message'] = f'Connection test failed with status {response.status_code}'
                result['status'] = 'error'
                result['details'] = {'http_status': response.status_code}
                
        except Exception as e:
            logger.error(f"Error validating Confluence connector: {str(e)}", exc_info=True)
            result['message'] = f'Connection error: {str(e)}'
            result['status'] = 'error'
        
        return result
    
    @staticmethod
    def _validate_gitlab(connector: Connector) -> Dict[str, Any]:
        """Validate GitLab connector."""
        result = {
            'valid': False,
            'status': 'disconnected',
            'message': '',
            'details': {}
        }
        
        try:
            client = GitLabOAuthClient(connector)
            headers = client._get_headers()
            
            # Test connection by fetching current user
            api_url = f"{connector.base_url.rstrip('/')}/api/v4/user"
            response = requests.get(api_url, headers=headers)
            
            if response.status_code == 200:
                user_data = response.json()
                result['valid'] = True
                result['status'] = 'connected'
                result['message'] = 'Connection successful'
                result['details'] = {
                    'user': user_data.get('name', user_data.get('username', 'Unknown')),
                    'email': user_data.get('email', 'N/A'),
                    'username': user_data.get('username', 'N/A'),
                    'id': user_data.get('id', 'N/A'),
                }
            elif response.status_code == 401:
                result['message'] = 'Authentication failed. Token may be invalid or expired.'
                result['status'] = 'error'
                result['details'] = {'http_status': 401}
            elif response.status_code == 403:
                result['message'] = 'Access forbidden. Check OAuth app permissions.'
                result['status'] = 'error'
                result['details'] = {'http_status': 403}
            else:
                result['message'] = f'Connection test failed with status {response.status_code}'
                result['status'] = 'error'
                result['details'] = {'http_status': response.status_code}
                
        except Exception as e:
            logger.error(f"Error validating GitLab connector: {str(e)}", exc_info=True)
            result['message'] = f'Connection error: {str(e)}'
            result['status'] = 'error'
        
        return result
    
    @staticmethod
    def _validate_github(connector: Connector) -> Dict[str, Any]:
        """Validate GitHub connector."""
        result = {
            'valid': False,
            'status': 'disconnected',
            'message': '',
            'details': {}
        }
        
        try:
            client = GitHubOAuthClient(connector)
            headers = client._get_headers()
            
            # Test connection by fetching current user
            api_url = f"{client.API_BASE_URL}/user"
            response = requests.get(api_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                user_data = response.json()
                result['valid'] = True
                result['status'] = 'connected'
                result['message'] = 'Connection successful'
                result['details'] = {
                    'user': user_data.get('name', user_data.get('login', 'Unknown')),
                    'email': user_data.get('email', 'N/A'),
                    'username': user_data.get('login', 'N/A'),
                    'id': user_data.get('id', 'N/A'),
                }
            elif response.status_code == 401:
                result['message'] = 'Authentication failed. Token may be invalid or expired.'
                result['status'] = 'error'
                result['details'] = {'http_status': 401}
            elif response.status_code == 403:
                result['message'] = 'Access forbidden. Check OAuth app permissions.'
                result['status'] = 'error'
                result['details'] = {'http_status': 403}
            else:
                result['message'] = f'Connection test failed with status {response.status_code}'
                result['status'] = 'error'
                result['details'] = {'http_status': response.status_code}
                
        except Exception as e:
            logger.error(f"Error validating GitHub connector: {str(e)}", exc_info=True)
            result['message'] = f'Connection error: {str(e)}'
            result['status'] = 'error'
        
        return result
    
    @staticmethod
    def _validate_google(connector: Connector) -> Dict[str, Any]:
        """Validate Google connector. On 401, tries token refresh once and retries."""
        result = {
            'valid': False,
            'status': 'disconnected',
            'message': '',
            'details': {}
        }
        
        try:
            client = GoogleOAuthClient(connector)
            api_url = "https://www.googleapis.com/oauth2/v2/userinfo"
            
            def do_request():
                headers = client._get_headers()
                return requests.get(api_url, headers=headers)
            
            response = do_request()
            
            # On 401, try refreshing the token once and retry (handles expired token / missing expiry)
            if response.status_code == 401:
                logger.info(f"Google connector {connector.id} ({connector.name}): userinfo returned 401, attempting token refresh")
                if client._refresh_access_token():
                    logger.info(f"Google connector {connector.id}: refresh succeeded, retrying userinfo")
                    response = do_request()
                    if response.status_code != 200:
                        logger.warning(f"Google connector {connector.id}: retry returned {response.status_code}")
                else:
                    logger.warning(f"Google connector {connector.id}: token refresh failed (refresh_token may be revoked or invalid)")
            
            if response.status_code == 200:
                user_data = response.json()
                result['valid'] = True
                result['status'] = 'connected'
                result['message'] = 'Connection successful'
                result['details'] = {
                    'user': user_data.get('name', user_data.get('email', 'Unknown')),
                    'email': user_data.get('email', 'N/A'),
                    'id': user_data.get('id', 'N/A'),
                }
            elif response.status_code == 401:
                result['message'] = 'Authentication failed. Token may be invalid or expired.'
                result['status'] = 'error'
                result['details'] = {'http_status': 401}
            elif response.status_code == 403:
                result['message'] = 'Access forbidden. Check OAuth app permissions.'
                result['status'] = 'error'
                result['details'] = {'http_status': 403}
            else:
                result['message'] = f'Connection test failed with status {response.status_code}'
                result['status'] = 'error'
                result['details'] = {'http_status': response.status_code}
                
        except Exception as e:
            logger.error(f"Error validating Google connector: {str(e)}", exc_info=True)
            result['message'] = f'Connection error: {str(e)}'
            result['status'] = 'error'
        
        return result
    
    @staticmethod
    def _validate_microsoft(connector: Connector) -> Dict[str, Any]:
        """Validate Microsoft connector."""
        result = {
            'valid': False,
            'status': 'disconnected',
            'message': '',
            'details': {}
        }
        
        try:
            client = MicrosoftOAuthClient(connector)
            headers = client._get_headers()
            
            # Test connection by fetching current user
            api_url = "https://graph.microsoft.com/v1.0/me"
            response = requests.get(api_url, headers=headers)
            
            if response.status_code == 200:
                user_data = response.json()
                result['valid'] = True
                result['status'] = 'connected'
                result['message'] = 'Connection successful'
                result['details'] = {
                    'user': user_data.get('displayName', user_data.get('userPrincipalName', 'Unknown')),
                    'email': user_data.get('mail', user_data.get('userPrincipalName', 'N/A')),
                    'id': user_data.get('id', 'N/A'),
                }
            elif response.status_code == 401:
                result['message'] = 'Authentication failed. Token may be invalid or expired.'
                result['status'] = 'error'
                result['details'] = {'http_status': 401}
            elif response.status_code == 403:
                result['message'] = 'Access forbidden. Check OAuth app permissions.'
                result['status'] = 'error'
                result['details'] = {'http_status': 403}
            else:
                result['message'] = f'Connection test failed with status {response.status_code}'
                result['status'] = 'error'
                result['details'] = {'http_status': response.status_code}
                
        except Exception as e:
            logger.error(f"Error validating Microsoft connector: {str(e)}", exc_info=True)
            result['message'] = f'Connection error: {str(e)}'
            result['status'] = 'error'
        
        return result
    
    @staticmethod
    def validate_and_update_status(connector: Connector) -> Dict[str, Any]:
        """Validate connector and update its status in the database.
        
        Args:
            connector: The connector to validate and update
            
        Returns:
            Validation result dict
        """
        result = ConnectorValidator.validate_connector(connector)
        
        # Update connector status
        old_status = connector.status
        connector.status = result['status']
        
        # Update metadata with validation details
        if 'metadata' not in connector.metadata:
            connector.metadata = {}
        
        connector.metadata['last_validation'] = {
            'timestamp': timezone.now().isoformat(),
            'valid': result['valid'],
            'message': result['message'],
            'details': result.get('details', {}),
        }
        
        # Update connected_at if status changed to connected
        if result['status'] == 'connected' and old_status != 'connected':
            connector.connected_at = timezone.now()
        
        connector.save()
        
        logger.info(f"Connector {connector.id} ({connector.name}) status updated: {old_status} -> {result['status']}")
        
        return result

