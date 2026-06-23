"""GitHub OAuth implementation for fetching training data."""
import logging
import requests
from typing import Dict, Any, List
from django.utils import timezone
from .models import Connector

logger = logging.getLogger(__name__)


class GitHubOAuthClient:
    """Client for GitHub OAuth authentication and API access."""
    
    # GitHub OAuth endpoints (OAuth 2.0)
    OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
    API_BASE_URL = "https://api.github.com"
    
    def __init__(self, connector: Connector):
        """Initialize GitHub OAuth client with connector."""
        self.connector = connector
        # GitHub uses api.github.com for API, but base_url might be github.com or custom GitHub Enterprise
        self.base_url = connector.base_url.rstrip('/') if connector.base_url else 'https://api.github.com'
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
            
            # GitHub OAuth scopes
            scopes = [
                'repo',  # Full control of private repositories
                'read:org',  # Read org and team membership
                'read:user',  # Read user profile data
            ]
            
            params = {
                'client_id': self.client_id,
                'redirect_uri': callback_url,
                'scope': ' '.join(scopes),
                'state': state,
            }
            
            from urllib.parse import urlencode
            authorization_url = f"{self.OAUTH_AUTHORIZE_URL}?{urlencode(params)}"
            
            return {
                'authorization_url': authorization_url,
                'state': state,
            }
            
        except Exception as e:
            logger.error(f"Error getting GitHub authorization URL: {str(e)}", exc_info=True)
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
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'code': code,
            }
            
            headers = {
                'Accept': 'application/json',  # GitHub returns JSON when Accept header is set
            }
            
            # Add timeout to prevent hanging in async contexts
            response = requests.post(self.OAUTH_TOKEN_URL, data=data, headers=headers, timeout=10)
            response.raise_for_status()
            
            token_data = response.json()
            
            # GitHub returns access_token directly (not nested)
            access_token = token_data.get('access_token')
            if not access_token:
                raise Exception("No access token in response from GitHub")
            
            # GitHub doesn't provide refresh tokens for OAuth apps (only for GitHub Apps)
            # Tokens don't expire unless revoked
            refresh_token = token_data.get('refresh_token')
            expires_in = token_data.get('expires_in', None)  # Usually None for OAuth apps
            
            # Update connector with tokens
            self.connector.update_token(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=expires_in
            )
            self.connector.status = 'connected'
            self.connector.connected_at = timezone.now()
            self.connector.save()
            
            return {
                'success': True,
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expires_in': expires_in,
            }
            
        except Exception as e:
            logger.error(f"Error handling GitHub OAuth callback: {str(e)}", exc_info=True)
            self.connector.status = 'error'
            self.connector.save()
            raise Exception(f"Failed to complete OAuth flow: {str(e)}")
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for authenticated API requests."""
        if not self.connector.access_token:
            raise Exception("No access token available. Please reconnect the connector.")
        
        return {
            'Authorization': f'Bearer {self.connector.access_token}',  # GitHub OAuth uses Bearer token
            'Accept': 'application/vnd.github.v3+json',
        }
    
    def fetch_repositories(self, visibility: str = None, affiliation: str = 'owner,collaborator,organization_member') -> List[Dict[str, Any]]:
        """Fetch GitHub repositories for training data.
        
        Args:
            visibility: Filter by visibility ('all', 'public', 'private')
            affiliation: Comma-separated list of affiliations ('owner', 'collaborator', 'organization_member')
            
        Returns:
            List of repository dictionaries
        """
        try:
            headers = self._get_headers()
            api_url = f"{self.API_BASE_URL}/user/repos"
            
            params = {
                'affiliation': affiliation,
                'sort': 'updated',
                'direction': 'desc',
                'per_page': 100,  # GitHub API limit
            }
            
            if visibility:
                params['visibility'] = visibility
            
            response = requests.get(api_url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            
            repositories = response.json()
            
            # Format repositories for training data
            formatted_repos = []
            for repo in repositories:
                formatted_repo = {
                    'id': repo.get('id'),
                    'name': repo.get('name'),
                    'full_name': repo.get('full_name'),
                    'description': repo.get('description', ''),
                    'url': repo.get('html_url', ''),
                    'language': repo.get('language', ''),
                    'stars': repo.get('stargazers_count', 0),
                    'forks': repo.get('forks_count', 0),
                    'private': repo.get('private', False),
                    'created_at': repo.get('created_at', ''),
                    'updated_at': repo.get('updated_at', ''),
                }
                formatted_repos.append(formatted_repo)
            
            return formatted_repos
            
        except Exception as e:
            logger.error(f"Error fetching GitHub repositories: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch GitHub repositories: {str(e)}")
    
    def fetch_issues(self, owner: str, repo: str, state: str = 'closed', max_results: int = 100) -> List[Dict[str, Any]]:
        """Fetch GitHub issues for training data.
        
        Args:
            owner: Repository owner (username or organization)
            repo: Repository name
            state: Issue state ('open', 'closed', 'all')
            max_results: Maximum number of issues to fetch
            
        Returns:
            List of issue dictionaries
        """
        try:
            headers = self._get_headers()
            api_url = f"{self.API_BASE_URL}/repos/{owner}/{repo}/issues"
            
            params = {
                'state': state,
                'sort': 'updated',
                'direction': 'desc',
                'per_page': min(max_results, 100),  # GitHub API limit
            }
            
            response = requests.get(api_url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            
            issues = response.json()
            
            # Format issues for training data
            formatted_issues = []
            for issue in issues:
                # Skip pull requests (they have pull_request field)
                if issue.get('pull_request'):
                    continue
                
                formatted_issue = {
                    'number': issue.get('number'),
                    'title': issue.get('title', ''),
                    'body': issue.get('body', ''),
                    'state': issue.get('state', ''),
                    'labels': [label.get('name') for label in issue.get('labels', [])],
                    'assignee': issue.get('assignee', {}).get('login', '') if issue.get('assignee') else 'Unassigned',
                    'user': issue.get('user', {}).get('login', ''),
                    'created_at': issue.get('created_at', ''),
                    'updated_at': issue.get('updated_at', ''),
                    'closed_at': issue.get('closed_at', ''),
                    'url': issue.get('html_url', ''),
                }
                formatted_issues.append(formatted_issue)
            
            return formatted_issues
            
        except Exception as e:
            logger.error(f"Error fetching GitHub issues: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch GitHub issues: {str(e)}")

