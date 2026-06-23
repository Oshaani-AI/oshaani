"""JIRA OAuth implementation for fetching training data."""
import logging
import requests
from typing import Dict, Any, Optional, List
from django.utils import timezone
from .models import Connector

logger = logging.getLogger(__name__)


class JIRAOAuthClient:
    """Client for JIRA Cloud OAuth 2.0 (3LO) authentication and API access."""
    
    # Atlassian OAuth 2.0 endpoints (for JIRA Cloud)
    OAUTH_AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
    OAUTH_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
    OAUTH_AUDIENCE = "api.atlassian.com"
    
    def __init__(self, connector: Connector):
        """Initialize JIRA OAuth client with connector."""
        self.connector = connector
        self.base_url = connector.base_url.rstrip('/')
        self.client_id = connector.client_id
        self.client_secret = connector.client_secret
    
    def test_base_url(self) -> Dict[str, Any]:
        """Test if the base URL is accessible and is a valid JIRA instance.
        
        Returns:
            Dict with test results
        """
        try:
            # Test basic connectivity
            response = requests.get(self.base_url, timeout=10, allow_redirects=True)
            
            # Check if it's a JIRA instance
            is_jira = False
            jira_version = None
            
            # Check for JIRA indicators in response
            if 'jira' in response.text.lower() or 'atlassian' in response.text.lower():
                is_jira = True
            
            # Try to access JIRA REST API
            try:
                api_response = requests.get(f"{self.base_url}/rest/api/3/serverInfo", timeout=10)
                if api_response.status_code == 200:
                    server_info = api_response.json()
                    jira_version = server_info.get('version', 'Unknown')
                    is_jira = True
            except Exception:
                pass
            
            return {
                'accessible': response.status_code < 400,
                'is_jira': is_jira,
                'jira_version': jira_version,
                'status_code': response.status_code,
                'final_url': response.url,
            }
        except Exception as e:
            return {
                'accessible': False,
                'is_jira': False,
                'error': str(e),
            }
    
    def get_authorization_url(self, callback_url: str) -> Dict[str, Any]:
        """Get OAuth 2.0 authorization URL for user to grant access.
        
        Args:
            callback_url: OAuth callback URL
        
        Returns:
            Dict with 'authorization_url' and 'state'
        """
        try:
            import secrets
            state = secrets.token_urlsafe(32)
            
            # Store state in metadata for verification
            # Ensure metadata is a dict
            if not isinstance(self.connector.metadata, dict):
                self.connector.metadata = {}
            self.connector.metadata['oauth_state'] = state
            self.connector.metadata['oauth_state_timestamp'] = timezone.now().isoformat()
            self.connector.metadata['jira_site_id'] = self._extract_site_id()
            self.connector.save(update_fields=['metadata'])
            logger.info(f"Stored OAuth state for connector {self.connector.id}: {state[:10]}...")
            
            # OAuth 2.0 scopes for JIRA Cloud
            scopes = [
                'read:jira-work',
                'read:jira-user',
                'offline_access',  # For refresh token
            ]
            
            params = {
                'audience': self.OAUTH_AUDIENCE,
                'client_id': self.client_id,
                'scope': ' '.join(scopes),
                'redirect_uri': callback_url,
                'state': state,
                'response_type': 'code',
                'prompt': 'consent',  # Force consent screen to ensure refresh token
            }
            
            from urllib.parse import urlencode
            authorization_url = f"{self.OAUTH_AUTHORIZE_URL}?{urlencode(params)}"
            
            logger.info(f"Generated JIRA OAuth 2.0 authorization URL for connector {self.connector.id}")
            logger.info(f"Callback URL being used: {callback_url}")
            logger.info(f"IMPORTANT: This exact callback URL must be registered in Atlassian Developer Console: {callback_url}")
            
            return {
                'authorization_url': authorization_url,
                'state': state,
                'callback_url': callback_url,  # Include in response for debugging
            }
            
        except Exception as e:
            logger.error(f"Error getting JIRA authorization URL: {str(e)}", exc_info=True)
            raise Exception(f"Failed to get authorization URL: {str(e)}")
    
    def _extract_site_id(self) -> Optional[str]:
        """Extract JIRA site ID from base URL.
        
        For JIRA Cloud, the site ID is typically the subdomain.
        Example: https://your-domain.atlassian.net -> your-domain
        """
        try:
            from urllib.parse import urlparse
            parsed = urlparse(self.base_url)
            hostname = parsed.hostname or ''
            
            if 'atlassian.net' in hostname or 'atlassian.com' in hostname:
                # Extract subdomain (site ID)
                site_id = hostname.split('.')[0]
                return site_id
            return None
        except Exception as e:
            logger.warning(f"Could not extract site ID from base URL: {str(e)}")
            return None
    
    def handle_oauth_callback(self, code: str, state: str, callback_url: str) -> Dict[str, Any]:
        """Handle OAuth callback and exchange code for access token.
        
        Args:
            code: Authorization code from callback
            state: State parameter for verification
            callback_url: Callback URL used in authorization request
            
        Returns:
            Dict with access token information
        """
        try:
            # Reload connector from database to get latest metadata
            self.connector.refresh_from_db()
            
            # Verify state
            # Ensure metadata is a dict
            if not isinstance(self.connector.metadata, dict):
                self.connector.metadata = {}
            
            stored_state = self.connector.metadata.get('oauth_state')
            if not stored_state:
                logger.warning(f"No stored state found for connector {self.connector.id}. This might happen if the connector was recreated. State validation skipped.")
                # Don't fail - allow the OAuth flow to continue if state is missing
                # This handles cases where the connector was recreated between auth and callback
            elif state != stored_state:
                logger.error(f"State mismatch for connector {self.connector.id}. Expected: {stored_state[:10]}..., Got: {state[:10]}...")
                raise Exception(f"Invalid state parameter - possible CSRF attack. Expected: {stored_state[:10]}..., Got: {state[:10]}...")
            else:
                logger.info(f"State validation passed for connector {self.connector.id}")
            
            # Clear the state after validation to prevent reuse
            self.connector.metadata.pop('oauth_state', None)
            self.connector.metadata.pop('oauth_state_timestamp', None)
            
            # Exchange code for token
            data = {
                'grant_type': 'authorization_code',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'code': code,
                'redirect_uri': callback_url,
            }
            
            # Add timeout to prevent hanging in async contexts
            response = requests.post(self.OAUTH_TOKEN_URL, data=data, timeout=10)
            response.raise_for_status()
            
            token_data = response.json()
            
            # Store cloud ID for API calls
            # For Atlassian OAuth 2.0, cloud_id might not be in token response
            # We'll fetch it from the access token or API
            cloud_id = token_data.get('cloud_id')
            if not cloud_id:
                # Try to get cloud ID from the access token or base URL
                cloud_id = self._get_cloud_id_from_token(token_data.get('access_token'))
            
            if cloud_id:
                # Ensure metadata is a dict
                if not isinstance(self.connector.metadata, dict):
                    self.connector.metadata = {}
                self.connector.metadata['cloud_id'] = cloud_id
                logger.info(f"Stored cloud_id {cloud_id} for connector {self.connector.id}")
            else:
                logger.warning(f"Could not determine cloud_id for connector {self.connector.id}. API calls may fail.")
            
            # Update connector with tokens
            self.connector.update_token(
                access_token=token_data.get('access_token'),
                refresh_token=token_data.get('refresh_token'),
                expires_in=token_data.get('expires_in', 3600)  # Default 1 hour
            )
            
            # If cloud_id wasn't in token response, try to fetch it now
            if not cloud_id and token_data.get('access_token'):
                cloud_id = self._get_cloud_id_from_token(token_data.get('access_token'))
                if cloud_id:
                    # Ensure metadata is a dict
                    if not isinstance(self.connector.metadata, dict):
                        self.connector.metadata = {}
                    self.connector.metadata['cloud_id'] = cloud_id
                    logger.info(f"Fetched and stored cloud_id {cloud_id} for connector {self.connector.id}")
            
            self.connector.status = 'connected'
            self.connector.connected_at = timezone.now()
            # Save with metadata to ensure cloud_id is persisted
            self.connector.save(update_fields=['status', 'connected_at', 'metadata', 'access_token', 'refresh_token', 'token_expires_at'])
            
            logger.info(f"Successfully completed JIRA OAuth 2.0 flow for connector {self.connector.id}")
            
            return {
                'success': True,
                'access_token': token_data.get('access_token'),
                'refresh_token': token_data.get('refresh_token'),
                'expires_in': token_data.get('expires_in'),
                'cloud_id': cloud_id,
            }
            
        except Exception as e:
            logger.error(f"Error handling JIRA OAuth callback: {str(e)}", exc_info=True)
            self.connector.status = 'error'
            self.connector.save()
            raise Exception(f"Failed to complete OAuth flow: {str(e)}")
    
    def refresh_access_token(self) -> Dict[str, Any]:
        """Refresh the access token using the refresh token.
        
        Returns:
            Dict with new access token information
        """
        try:
            if not self.connector.refresh_token:
                raise Exception("No refresh token available")
            
            data = {
                'grant_type': 'refresh_token',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.connector.refresh_token,
            }
            
            response = requests.post(self.OAUTH_TOKEN_URL, data=data, timeout=10)
            response.raise_for_status()
            
            token_data = response.json()
            
            # Update connector with new tokens
            self.connector.update_token(
                access_token=token_data.get('access_token'),
                refresh_token=token_data.get('refresh_token', self.connector.refresh_token),  # Keep existing if not provided
                expires_in=token_data.get('expires_in', 3600)
            )
            self.connector.save()
            
            return {
                'access_token': token_data.get('access_token'),
                'refresh_token': token_data.get('refresh_token'),
                'expires_in': token_data.get('expires_in'),
            }
            
        except Exception as e:
            logger.error(f"Error refreshing JIRA access token: {str(e)}", exc_info=True)
            raise Exception(f"Failed to refresh access token: {str(e)}")
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for authenticated API requests."""
        if not self.connector.access_token:
            raise Exception("No access token available. Please reconnect the connector.")
        
        # Check if token needs refresh
        if not self.connector.is_token_valid():
            logger.info(f"Access token expired for connector {self.connector.id}, refreshing...")
            try:
                self.refresh_access_token()
            except Exception as e:
                logger.error(f"Token refresh failed: {str(e)}")
                raise Exception(f"Access token is invalid or expired and refresh failed. Please reconnect the connector. Error: {str(e)}")
        
        return {
            'Authorization': f'Bearer {self.connector.access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
    
    def _get_cloud_id_from_token(self, access_token: str) -> Optional[str]:
        """Get cloud ID from access token or API.
        
        For Atlassian OAuth 2.0, we need to fetch the cloud ID from the accessible resources endpoint.
        """
        try:
            if not access_token:
                logger.warning("No access token provided for cloud_id lookup")
                return None
            
            # Get cloud ID from the accessible resources endpoint
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json',
            }
            
            logger.info(f"Fetching accessible resources for connector {self.connector.id}")
            response = requests.get('https://api.atlassian.com/oauth/token/accessible-resources', 
                                  headers=headers, timeout=10)
            
            if response.status_code == 200:
                resources = response.json()
                logger.info(f"Found {len(resources)} accessible resources")
                
                # Find the JIRA resource
                # Resources have structure: [{"id": "cloud_id", "name": "Site name", "url": "https://site.atlassian.net", "scopes": [...], "avatarUrl": "..."}]
                # First, try to match by base URL (most reliable)
                base_url_clean = self.base_url.rstrip('/').lower()
                matched_by_url = None
                
                for resource in resources:
                    resource_url = resource.get('url', '').rstrip('/').lower()
                    if resource_url == base_url_clean:
                        cloud_id = resource.get('id')
                        if cloud_id:
                            logger.info(f"Matched resource by exact URL match, cloud_id: {cloud_id}, url: {resource.get('url')}")
                            matched_by_url = cloud_id
                            break
                
                # If exact match found, return it
                if matched_by_url:
                    return matched_by_url
                
                # If no exact URL match, try partial match (e.g., https://payuindia.atlassian.net matches)
                for resource in resources:
                    resource_url = resource.get('url', '').rstrip('/').lower()
                    # Extract domain from base URL
                    from urllib.parse import urlparse
                    base_domain = urlparse(self.base_url).netloc.lower()
                    resource_domain = urlparse(resource_url).netloc.lower() if resource_url.startswith('http') else resource_url
                    
                    if base_domain == resource_domain:
                        cloud_id = resource.get('id')
                        if cloud_id:
                            logger.info(f"Matched resource by domain, cloud_id: {cloud_id}, url: {resource.get('url')}")
                            return cloud_id
                
                # Check each resource for JIRA access by scopes
                for resource in resources:
                    resource_id = resource.get('id')
                    resource_name = resource.get('name', '').lower()
                    resource_url = resource.get('url', '').rstrip('/')
                    scopes = resource.get('scopes', [])
                    
                    # Check if this resource has JIRA scopes
                    has_jira_scope = any('jira' in scope.lower() for scope in scopes)
                    is_jira_url = 'jira' in resource_url.lower() or 'atlassian.net' in resource_url
                    
                    if resource_id and (has_jira_scope or is_jira_url or 'jira' in resource_name):
                        logger.info(f"Found JIRA resource with cloud_id: {resource_id}, name: {resource.get('name')}, url: {resource_url}, scopes: {scopes}")
                        return resource_id
                
                # If still no match, use the first resource (assuming single JIRA instance)
                if resources and len(resources) > 0:
                    # Prefer resources with JIRA in the name or URL
                    for resource in resources:
                        resource_name = resource.get('name', '').lower()
                        resource_url = resource.get('url', '').lower()
                        if 'jira' in resource_name or 'jira' in resource_url:
                            cloud_id = resource.get('id')
                            if cloud_id:
                                logger.info(f"Using JIRA resource as cloud_id: {cloud_id}")
                                return cloud_id
                    
                    # Fallback to first resource
                    cloud_id = resources[0].get('id')
                    if cloud_id:
                        logger.warning(f"Using first resource as cloud_id (no exact match found): {cloud_id}, name: {resources[0].get('name')}, url: {resources[0].get('url')}")
                        return cloud_id
            elif response.status_code == 401:
                logger.error(f"Authentication failed when fetching accessible resources: {response.status_code}")
                logger.error(f"Response: {response.text[:200]}")
                return None
            else:
                logger.warning(f"Failed to fetch accessible resources: {response.status_code} - {response.text[:200]}")
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting cloud_id from token: {str(e)}", exc_info=True)
            return None
    
    def _get_api_base_url(self) -> str:
        """Get the API base URL for JIRA Cloud.
        
        For JIRA Cloud, we need to use the cloud ID in the API URL.
        """
        cloud_id = self.connector.metadata.get('cloud_id')
        if cloud_id:
            api_url = f"https://api.atlassian.com/ex/jira/{cloud_id}"
            logger.debug(f"Using cloud_id-based API URL: {api_url}")
            return api_url
        
        # If cloud_id is not available, try to get it now
        if self.connector.access_token:
            logger.info(f"Cloud ID not found in metadata for connector {self.connector.id}, fetching from API...")
            cloud_id = self._get_cloud_id_from_token(self.connector.access_token)
            if cloud_id:
                # Ensure metadata is a dict
                if not isinstance(self.connector.metadata, dict):
                    self.connector.metadata = {}
                self.connector.metadata['cloud_id'] = cloud_id
                # Save to database to persist cloud_id for future use
                self.connector.save(update_fields=['metadata'])
                logger.info(f"Successfully fetched and stored cloud_id {cloud_id} in database for connector {self.connector.id}")
                api_url = f"https://api.atlassian.com/ex/jira/{cloud_id}"
                logger.info(f"Using API URL: {api_url}")
                return api_url
            else:
                logger.error(f"Failed to fetch cloud_id for connector {self.connector.id}. API calls may fail.")
        else:
            logger.error(f"No access token available for connector {self.connector.id}")
        
        # Fallback to base URL if cloud_id not available (may not work for OAuth 2.0)
        logger.warning(f"Using base URL fallback for connector {self.connector.id}. Cloud ID not available. This may cause 401 errors.")
        logger.warning(f"Attempting to fetch cloud_id one more time before using fallback...")
        
        # Last attempt: try to get cloud_id if we have a valid token
        if self.connector.access_token and self.connector.is_token_valid():
            try:
                cloud_id = self._get_cloud_id_from_token(self.connector.access_token)
                if cloud_id:
                    # Ensure metadata is a dict
                    if not isinstance(self.connector.metadata, dict):
                        self.connector.metadata = {}
                    self.connector.metadata['cloud_id'] = cloud_id
                    # Save to database to persist cloud_id for future use
                    self.connector.save(update_fields=['metadata'])
                    logger.info(f"Successfully fetched and stored cloud_id {cloud_id} in database on last attempt")
                    api_url = f"https://api.atlassian.com/ex/jira/{cloud_id}"
                    logger.info(f"Using API URL: {api_url}")
                    return api_url
            except Exception as e:
                logger.error(f"Final attempt to fetch cloud_id failed: {str(e)}")
        
        logger.error(f"CRITICAL: Cloud ID is required for JIRA OAuth 2.0 but could not be determined.")
        logger.error(f"Please reconnect the connector to ensure cloud_id is properly stored.")
        return self.base_url
    
    def fetch_issues(self, jql: str = None, max_results: int = 100) -> List[Dict[str, Any]]:
        """Fetch JIRA issues for training data.
        
        Args:
            jql: JQL query string (optional, defaults to closed/completed issues only)
            max_results: Maximum number of issues to fetch
            
        Returns:
            List of issue dictionaries
        """
        try:
            headers = self._get_headers()
            api_base_url = self._get_api_base_url()
            
            # JIRA REST API endpoint - using new /search/jql endpoint
            # The old /search endpoint has been deprecated, must use /search/jql
            jql_query = jql if jql else 'statusCategory = Done ORDER BY updated DESC'
            api_url = f"{api_base_url}/rest/api/3/search/jql"
            
            params = {
                'jql': jql_query,
                'maxResults': min(max_results, 100),  # JIRA API limit
                'fields': 'summary,description,comment,status,statusCategory,priority,assignee,reporter,created,updated',
            }
            
            response = requests.get(api_url, headers=headers, params=params, timeout=30)
            
            # Handle 410 Gone error - cloud_id might be invalid or resource moved
            if response.status_code == 410:
                logger.error(f"410 Gone error for connector {self.connector.id}")
                logger.error(f"API URL: {api_url}")
                logger.error(f"Cloud ID: {self.connector.metadata.get('cloud_id')}")
                logger.error(f"Base URL: {self.base_url}")
                logger.error(f"Response: {response.text[:500]}")
                
                # Try to re-fetch cloud_id and retry
                logger.info("Attempting to re-fetch cloud_id due to 410 error...")
                if self.connector.access_token:
                    new_cloud_id = self._get_cloud_id_from_token(self.connector.access_token)
                    if new_cloud_id:
                        if new_cloud_id != self.connector.metadata.get('cloud_id'):
                            logger.info(f"Found different cloud_id: {new_cloud_id} (was: {self.connector.metadata.get('cloud_id')}), updating connector...")
                            # Ensure metadata is a dict
                            if not isinstance(self.connector.metadata, dict):
                                self.connector.metadata = {}
                            self.connector.metadata['cloud_id'] = new_cloud_id
                            # Save to database to persist the updated cloud_id
                            self.connector.save(update_fields=['metadata'])
                            logger.info(f"Updated cloud_id in database: {new_cloud_id}")
                        else:
                            logger.warning(f"Re-fetched same cloud_id: {new_cloud_id}. The cloud_id might be correct but the resource is gone.")
                        
                        # Validate the cloud_id by trying to access serverInfo endpoint
                        test_url = f"https://api.atlassian.com/ex/jira/{new_cloud_id}/rest/api/3/serverInfo"
                        test_response = requests.get(test_url, headers=headers, timeout=10)
                        if test_response.status_code == 200:
                            logger.info(f"Cloud_id {new_cloud_id} validated successfully. Retrying search...")
                            # Retry with validated cloud_id
                            api_base_url = f"https://api.atlassian.com/ex/jira/{new_cloud_id}"
                            api_url = f"{api_base_url}/rest/api/3/search/jql"
                            logger.info(f"Retrying with validated API URL: {api_url}")
                            response = requests.get(api_url, headers=headers, params=params, timeout=30)
                        elif test_response.status_code == 410:
                            logger.error(f"Cloud_id {new_cloud_id} also returns 410. This resource may no longer exist.")
                            raise Exception("410 Gone error: The JIRA resource associated with this connector no longer exists or is inaccessible. Please reconnect the connector with a valid JIRA instance.")
                        else:
                            logger.warning(f"Cloud_id validation returned {test_response.status_code}. Proceeding with retry anyway...")
                            # Retry anyway
                            api_base_url = f"https://api.atlassian.com/ex/jira/{new_cloud_id}"
                            api_url = f"{api_base_url}/rest/api/3/search/jql"
                            response = requests.get(api_url, headers=headers, params=params, timeout=30)
                    else:
                        logger.error("Could not fetch cloud_id from accessible resources")
                        raise Exception("410 Gone error: Cloud ID appears invalid and could not be re-fetched. Please reconnect the connector.")
                else:
                    raise Exception("410 Gone error: No access token available. Please reconnect the connector.")
            
            # Handle authentication errors with better diagnostics
            if response.status_code == 401:
                logger.error(f"Authentication failed for connector {self.connector.id}")
                logger.error(f"API URL: {api_url}")
                logger.error(f"Base URL: {self.base_url}")
                logger.error(f"Cloud ID: {self.connector.metadata.get('cloud_id', 'NOT SET')}")
                logger.error(f"Token valid: {self.connector.is_token_valid()}")
                logger.error(f"Token expires at: {self.connector.token_expires_at}")
                logger.error(f"Response: {response.text[:500]}")
                
                # Check if we're using the wrong API URL (base URL instead of cloud_id URL)
                if not api_url.startswith('https://api.atlassian.com/ex/jira/'):
                    logger.error("CRITICAL: Using base URL instead of cloud_id-based API URL. This will always fail with OAuth 2.0.")
                    logger.error("Attempting to fetch cloud_id and retry...")
                    try:
                        cloud_id = self._get_cloud_id_from_token(self.connector.access_token)
                        if cloud_id:
                            self.connector.metadata['cloud_id'] = cloud_id
                            self.connector.save(update_fields=['metadata'])
                            api_url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search/jql"
                            logger.info(f"Retrying with cloud_id-based URL: {api_url}")
                            response = requests.get(api_url, headers=headers, params=params, timeout=30)
                            if response.status_code != 401:
                                # Success! Continue with the response
                                pass
                            else:
                                logger.error("Still getting 401 even with cloud_id-based URL")
                        else:
                            raise Exception("Could not determine cloud_id. Please reconnect the connector.")
                    except Exception as cloud_id_error:
                        logger.error(f"Failed to fetch cloud_id: {str(cloud_id_error)}")
                        raise Exception("Authentication failed. Cloud ID is required for JIRA OAuth 2.0 but could not be determined. Please reconnect the connector.")
                
                # Try to refresh token if it's expired
                if not self.connector.is_token_valid():
                    logger.info("Token appears expired, attempting refresh...")
                    try:
                        self.refresh_access_token()
                        # Retry with new token
                        headers = self._get_headers()
                        # Make sure we're using the cloud_id URL with new endpoint
                        api_base_url = self._get_api_base_url()
                        api_url = f"{api_base_url}/rest/api/3/search/jql"
                        response = requests.get(api_url, headers=headers, params=params, timeout=30)
                        if response.status_code == 401:
                            raise Exception("Authentication failed even after token refresh. Please reconnect the connector.")
                    except Exception as refresh_error:
                        logger.error(f"Token refresh failed: {str(refresh_error)}")
                        raise Exception(f"Authentication failed. Token may be invalid or expired. Please reconnect the connector. Error: {str(refresh_error)}")
            
            # Raise for any other HTTP errors (will be handled by response.raise_for_status())
            response.raise_for_status()
            
            data = response.json()
            issues = data.get('issues', [])
            
            # Format issues for training data
            formatted_issues = []
            for issue in issues:
                fields = issue.get('fields', {})
                formatted_issue = {
                    'key': issue.get('key'),
                    'summary': fields.get('summary', ''),
                    'description': fields.get('description', ''),
                    'status': fields.get('status', {}).get('name', ''),
                    'priority': fields.get('priority', {}).get('name', ''),
                    'assignee': fields.get('assignee', {}).get('displayName', '') if fields.get('assignee') else 'Unassigned',
                    'reporter': fields.get('reporter', {}).get('displayName', '') if fields.get('reporter') else '',
                    'created': fields.get('created', ''),
                    'updated': fields.get('updated', ''),
                    'comments': [],
                }
                
                # Fetch comments
                comments = fields.get('comment', {}).get('comments', [])
                for comment in comments:
                    formatted_issue['comments'].append({
                        'author': comment.get('author', {}).get('displayName', ''),
                        'body': comment.get('body', ''),
                        'created': comment.get('created', ''),
                    })
                
                formatted_issues.append(formatted_issue)
            
            return formatted_issues
            
        except Exception as e:
            logger.error(f"Error fetching JIRA issues: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch JIRA issues: {str(e)}")
    
    def fetch_projects(self) -> List[Dict[str, Any]]:
        """Fetch JIRA projects."""
        try:
            headers = self._get_headers()
            api_base_url = self._get_api_base_url()
            api_url = f"{api_base_url}/rest/api/3/project"
            
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            projects = response.json()
            return [
                {
                    'key': p.get('key'),
                    'name': p.get('name'),
                    'description': p.get('description', ''),
                    'projectTypeKey': p.get('projectTypeKey', ''),
                }
                for p in projects
            ]
            
        except Exception as e:
            logger.error(f"Error fetching JIRA projects: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch JIRA projects: {str(e)}")


class ConfluenceOAuthClient:
    """Client for Confluence OAuth authentication and API access."""
    
    # Confluence OAuth endpoints (OAuth 2.0)
    OAUTH_AUTHORIZE_URL = "{base_url}/plugins/servlet/oauth/authorize"
    OAUTH_TOKEN_URL = "{base_url}/plugins/servlet/oauth/access-token"
    
    def __init__(self, connector: Connector):
        """Initialize Confluence OAuth client with connector."""
        self.connector = connector
        self.base_url = connector.base_url.rstrip('/')
        self.client_id = connector.client_id
        self.client_secret = connector.client_secret
    
    def get_authorization_url(self, callback_url: str, state: str = None) -> Dict[str, Any]:
        """Get OAuth 2.0 authorization URL.
        
        Args:
            callback_url: OAuth callback URL
            state: Optional state parameter for security
            
        Returns:
            Dict with 'authorization_url' and 'state'
        """
        try:
            import secrets
            if not state:
                state = secrets.token_urlsafe(32)
            
            # Store state in metadata for verification
            # Ensure metadata is a dict
            if not isinstance(self.connector.metadata, dict):
                self.connector.metadata = {}
            self.connector.metadata['oauth_state'] = state
            self.connector.metadata['oauth_state_timestamp'] = timezone.now().isoformat()
            self.connector.save(update_fields=['metadata'])
            logger.info(f"Stored OAuth state for connector {self.connector.id}: {state[:10]}...")
            
            params = {
                'client_id': self.client_id,
                'redirect_uri': callback_url,
                'response_type': 'code',
                'state': state,
                'scope': 'read',
            }
            
            auth_url = self.OAUTH_AUTHORIZE_URL.format(base_url=self.base_url)
            from urllib.parse import urlencode
            authorization_url = f"{auth_url}?{urlencode(params)}"
            
            return {
                'authorization_url': authorization_url,
                'state': state,
            }
            
        except Exception as e:
            logger.error(f"Error getting Confluence authorization URL: {str(e)}", exc_info=True)
            raise Exception(f"Failed to get authorization URL: {str(e)}")
    
    def handle_oauth_callback(self, code: str, state: str) -> Dict[str, Any]:
        """Handle OAuth callback and exchange code for access token.
        
        Args:
            code: Authorization code from callback
            state: State parameter for verification
            
        Returns:
            Dict with access token information
        """
        try:
            # Reload connector from database to get latest metadata
            self.connector.refresh_from_db()
            
            # Verify state
            # Ensure metadata is a dict
            if not isinstance(self.connector.metadata, dict):
                self.connector.metadata = {}
            
            stored_state = self.connector.metadata.get('oauth_state')
            if not stored_state:
                logger.warning(f"No stored state found for connector {self.connector.id}. This might happen if the connector was recreated. State validation skipped.")
                # Don't fail - allow the OAuth flow to continue if state is missing
                # This handles cases where the connector was recreated between auth and callback
            elif state != stored_state:
                logger.error(f"State mismatch for connector {self.connector.id}. Expected: {stored_state[:10]}..., Got: {state[:10]}...")
                raise Exception(f"Invalid state parameter - possible CSRF attack. Expected: {stored_state[:10]}..., Got: {state[:10]}...")
            else:
                logger.info(f"State validation passed for connector {self.connector.id}")
            
            # Clear the state after validation to prevent reuse
            self.connector.metadata.pop('oauth_state', None)
            self.connector.metadata.pop('oauth_state_timestamp', None)
            
            # Exchange code for token
            token_url = self.OAUTH_TOKEN_URL.format(base_url=self.base_url)
            
            data = {
                'grant_type': 'authorization_code',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'code': code,
            }
            
            # Add timeout to prevent hanging in async contexts
            response = requests.post(token_url, data=data, timeout=10)
            response.raise_for_status()
            
            token_data = response.json()
            
            # Update connector with tokens
            self.connector.update_token(
                access_token=token_data.get('access_token'),
                refresh_token=token_data.get('refresh_token'),
                expires_in=token_data.get('expires_in')
            )
            self.connector.status = 'connected'
            self.connector.connected_at = timezone.now()
            self.connector.save()
            
            return {
                'success': True,
                'access_token': token_data.get('access_token'),
                'refresh_token': token_data.get('refresh_token'),
                'expires_in': token_data.get('expires_in'),
            }
            
        except Exception as e:
            logger.error(f"Error handling Confluence OAuth callback: {str(e)}", exc_info=True)
            self.connector.status = 'error'
            self.connector.save()
            raise Exception(f"Failed to complete OAuth flow: {str(e)}")
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for authenticated API requests."""
        if not self.connector.is_token_valid():
            raise Exception("Access token is invalid or expired")
        
        return {
            'Authorization': f'Bearer {self.connector.access_token}',
            'Content-Type': 'application/json',
        }
    
    def fetch_pages(self, space_key: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch Confluence pages for training data.
        
        Args:
            space_key: Optional space key to filter pages
            limit: Maximum number of pages to fetch
            
        Returns:
            List of page dictionaries
        """
        try:
            headers = self._get_headers()
            api_url = f"{self.base_url}/rest/api/content"
            
            params = {
                'limit': min(limit, 100),  # Confluence API limit
                'expand': 'body.storage,version,space',
            }
            
            if space_key:
                params['spaceKey'] = space_key
            
            response = requests.get(api_url, headers=headers, params=params)
            response.raise_for_status()
            
            data = response.json()
            pages = data.get('results', [])
            
            # Format pages for training data
            formatted_pages = []
            for page in pages:
                body = page.get('body', {}).get('storage', {}).get('value', '')
                space = page.get('space', {})
                
                formatted_page = {
                    'id': page.get('id'),
                    'title': page.get('title', ''),
                    'body': body,
                    'space_key': space.get('key', ''),
                    'space_name': space.get('name', ''),
                    'version': page.get('version', {}).get('number', 1),
                    'created': page.get('_links', {}).get('base', '') + page.get('_links', {}).get('webui', ''),
                    'updated': page.get('version', {}).get('when', ''),
                }
                
                formatted_pages.append(formatted_page)
            
            return formatted_pages
            
        except Exception as e:
            logger.error(f"Error fetching Confluence pages: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch Confluence pages: {str(e)}")
    
    def fetch_spaces(self) -> List[Dict[str, Any]]:
        """Fetch Confluence spaces."""
        try:
            headers = self._get_headers()
            api_url = f"{self.base_url}/rest/api/space"
            
            response = requests.get(api_url, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            spaces = data.get('results', [])
            
            return [
                {
                    'key': s.get('key'),
                    'name': s.get('name', ''),
                    'type': s.get('type', ''),
                    'description': s.get('description', {}).get('plain', {}).get('value', ''),
                }
                for s in spaces
            ]
            
        except Exception as e:
            logger.error(f"Error fetching Confluence spaces: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch Confluence spaces: {str(e)}")

