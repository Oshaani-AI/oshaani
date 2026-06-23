"""GitLab OAuth implementation for fetching training data."""
import logging
import requests
from typing import Dict, Any, List
from django.utils import timezone
from .models import Connector

logger = logging.getLogger(__name__)


class GitLabOAuthClient:
    """Client for GitLab OAuth authentication and API access."""
    
    # GitLab OAuth endpoints (OAuth 2.0)
    OAUTH_AUTHORIZE_URL = "{base_url}/oauth/authorize"
    OAUTH_TOKEN_URL = "{base_url}/oauth/token"
    API_BASE_URL = "{base_url}/api/v4"
    
    def __init__(self, connector: Connector):
        """Initialize GitLab OAuth client with connector."""
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
                'scope': 'read_api read_repository',  # GitLab scopes
            }
            
            auth_url = self.OAUTH_AUTHORIZE_URL.format(base_url=self.base_url)
            from urllib.parse import urlencode
            authorization_url = f"{auth_url}?{urlencode(params)}"
            
            return {
                'authorization_url': authorization_url,
                'state': state,
            }
            
        except Exception as e:
            logger.error(f"Error getting GitLab authorization URL: {str(e)}", exc_info=True)
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
                logger.error(f"State mismatch for connector {self.connector.id}. Expected: {stored_state}, Got: {state}")
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
                'redirect_uri': self.connector.site_url + f'/api/connectors/{self.connector.id}/oauth/callback/',
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
            logger.error(f"Error handling GitLab OAuth callback: {str(e)}", exc_info=True)
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
    
    def fetch_projects(self, visibility: str = None, membership: bool = True) -> List[Dict[str, Any]]:
        """Fetch GitLab projects.
        
        Args:
            visibility: Filter by visibility (private, internal, public)
            membership: Only return projects user is a member of
            
        Returns:
            List of project dictionaries
        """
        try:
            headers = self._get_headers()
            api_url = f"{self.API_BASE_URL.format(base_url=self.base_url)}/projects"
            
            params = {
                'membership': 'true' if membership else 'false',
                'per_page': 100,
            }
            
            if visibility:
                params['visibility'] = visibility
            
            all_projects = []
            page = 1
            
            while True:
                params['page'] = page
                response = requests.get(api_url, headers=headers, params=params)
                response.raise_for_status()
                
                projects = response.json()
                if not projects:
                    break
                
                all_projects.extend(projects)
                
                # Check if there are more pages
                if len(projects) < 100:
                    break
                page += 1
            
            # Format projects
            formatted_projects = []
            for project in all_projects:
                formatted_projects.append({
                    'id': project.get('id'),
                    'name': project.get('name'),
                    'path': project.get('path'),
                    'path_with_namespace': project.get('path_with_namespace'),
                    'description': project.get('description', ''),
                    'visibility': project.get('visibility', ''),
                    'web_url': project.get('web_url', ''),
                    'created_at': project.get('created_at', ''),
                    'updated_at': project.get('last_activity_at', ''),
                })
            
            return formatted_projects
            
        except Exception as e:
            logger.error(f"Error fetching GitLab projects: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch GitLab projects: {str(e)}")
    
    def fetch_repository_files(self, project_id: int, path: str = '', ref: str = 'main') -> List[Dict[str, Any]]:
        """Fetch files from a GitLab repository.
        
        Args:
            project_id: GitLab project ID
            path: Repository path (empty for root)
            ref: Branch or tag name (default: main)
            
        Returns:
            List of file/directory dictionaries
        """
        try:
            headers = self._get_headers()
            api_url = f"{self.API_BASE_URL.format(base_url=self.base_url)}/projects/{project_id}/repository/tree"
            
            params = {
                'path': path,
                'ref': ref,
                'recursive': 'false',
                'per_page': 100,
            }
            
            response = requests.get(api_url, headers=headers, params=params)
            response.raise_for_status()
            
            files = response.json()
            
            return [
                {
                    'id': f.get('id'),
                    'name': f.get('name'),
                    'type': f.get('type'),  # 'blob' or 'tree'
                    'path': f.get('path'),
                    'mode': f.get('mode'),
                }
                for f in files
            ]
            
        except Exception as e:
            logger.error(f"Error fetching GitLab repository files: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch repository files: {str(e)}")
    
    def fetch_file_content(self, project_id: int, file_path: str, ref: str = 'main') -> Dict[str, Any]:
        """Fetch content of a specific file from GitLab repository.
        
        Args:
            project_id: GitLab project ID
            file_path: Path to the file in repository
            ref: Branch or tag name (default: main)
            
        Returns:
            Dict with file content and metadata
        """
        try:
            headers = self._get_headers()
            api_url = f"{self.API_BASE_URL.format(base_url=self.base_url)}/projects/{project_id}/repository/files/{file_path.replace('/', '%2F')}"
            
            params = {
                'ref': ref,
            }
            
            response = requests.get(api_url, headers=headers, params=params)
            response.raise_for_status()
            
            file_data = response.json()
            
            # Decode base64 content
            import base64
            content = base64.b64decode(file_data.get('content', '')).decode('utf-8', errors='ignore')
            
            return {
                'file_name': file_data.get('file_name'),
                'file_path': file_data.get('file_path'),
                'size': file_data.get('size'),
                'encoding': file_data.get('encoding'),
                'content': content,
                'content_sha256': file_data.get('content_sha256'),
                'ref': file_data.get('ref'),
                'blob_id': file_data.get('blob_id'),
                'commit_id': file_data.get('commit_id'),
            }
            
        except Exception as e:
            logger.error(f"Error fetching GitLab file content: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch file content: {str(e)}")
    
    def fetch_issues(self, project_id: int = None, state: str = 'opened', limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch GitLab issues.
        
        Args:
            project_id: Optional project ID to filter issues
            state: Issue state (opened, closed, all)
            limit: Maximum number of issues to fetch
            
        Returns:
            List of issue dictionaries
        """
        try:
            headers = self._get_headers()
            
            if project_id:
                api_url = f"{self.API_BASE_URL.format(base_url=self.base_url)}/projects/{project_id}/issues"
            else:
                api_url = f"{self.API_BASE_URL.format(base_url=self.base_url)}/issues"
            
            params = {
                'state': state,
                'per_page': min(limit, 100),
            }
            
            all_issues = []
            page = 1
            
            while len(all_issues) < limit:
                params['page'] = page
                response = requests.get(api_url, headers=headers, params=params)
                response.raise_for_status()
                
                issues = response.json()
                if not issues:
                    break
                
                all_issues.extend(issues[:limit - len(all_issues)])
                
                if len(issues) < 100 or len(all_issues) >= limit:
                    break
                page += 1
            
            # Format issues
            formatted_issues = []
            for issue in all_issues:
                formatted_issues.append({
                    'id': issue.get('id'),
                    'iid': issue.get('iid'),
                    'title': issue.get('title', ''),
                    'description': issue.get('description', ''),
                    'state': issue.get('state', ''),
                    'labels': issue.get('labels', []),
                    'author': issue.get('author', {}).get('name', '') if issue.get('author') else '',
                    'assignee': issue.get('assignee', {}).get('name', '') if issue.get('assignee') else '',
                    'created_at': issue.get('created_at', ''),
                    'updated_at': issue.get('updated_at', ''),
                    'web_url': issue.get('web_url', ''),
                    'project_id': issue.get('project_id'),
                })
            
            return formatted_issues
            
        except Exception as e:
            logger.error(f"Error fetching GitLab issues: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch GitLab issues: {str(e)}")
    
    def fetch_merge_requests(self, project_id: int = None, state: str = 'opened', limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch GitLab merge requests.
        
        Args:
            project_id: Optional project ID to filter merge requests
            state: MR state (opened, closed, merged, all)
            limit: Maximum number of MRs to fetch
            
        Returns:
            List of merge request dictionaries
        """
        try:
            headers = self._get_headers()
            
            if project_id:
                api_url = f"{self.API_BASE_URL.format(base_url=self.base_url)}/projects/{project_id}/merge_requests"
            else:
                api_url = f"{self.API_BASE_URL.format(base_url=self.base_url)}/merge_requests"
            
            params = {
                'state': state,
                'per_page': min(limit, 100),
            }
            
            all_mrs = []
            page = 1
            
            while len(all_mrs) < limit:
                params['page'] = page
                response = requests.get(api_url, headers=headers, params=params)
                response.raise_for_status()
                
                mrs = response.json()
                if not mrs:
                    break
                
                all_mrs.extend(mrs[:limit - len(all_mrs)])
                
                if len(mrs) < 100 or len(all_mrs) >= limit:
                    break
                page += 1
            
            # Format merge requests
            formatted_mrs = []
            for mr in all_mrs:
                formatted_mrs.append({
                    'id': mr.get('id'),
                    'iid': mr.get('iid'),
                    'title': mr.get('title', ''),
                    'description': mr.get('description', ''),
                    'state': mr.get('state', ''),
                    'source_branch': mr.get('source_branch', ''),
                    'target_branch': mr.get('target_branch', ''),
                    'author': mr.get('author', {}).get('name', '') if mr.get('author') else '',
                    'assignee': mr.get('assignee', {}).get('name', '') if mr.get('assignee') else '',
                    'created_at': mr.get('created_at', ''),
                    'updated_at': mr.get('updated_at', ''),
                    'web_url': mr.get('web_url', ''),
                    'project_id': mr.get('project_id'),
                })
            
            return formatted_mrs
            
        except Exception as e:
            logger.error(f"Error fetching GitLab merge requests: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch GitLab merge requests: {str(e)}")
    
    def fetch_wiki_pages(self, project_id: int) -> List[Dict[str, Any]]:
        """Fetch GitLab wiki pages.
        
        Args:
            project_id: GitLab project ID
            
        Returns:
            List of wiki page dictionaries
        """
        try:
            headers = self._get_headers()
            api_url = f"{self.API_BASE_URL.format(base_url=self.base_url)}/projects/{project_id}/wikis"
            
            response = requests.get(api_url, headers=headers)
            response.raise_for_status()
            
            pages = response.json()
            
            # Format wiki pages
            formatted_pages = []
            for page in pages:
                # Fetch individual page content
                try:
                    page_slug = page.get('slug', '')
                    page_api_url = f"{self.API_BASE_URL.format(base_url=self.base_url)}/projects/{project_id}/wikis/{page_slug}"
                    page_response = requests.get(page_api_url, headers=headers)
                    if page_response.status_code == 200:
                        page_data = page_response.json()
                        formatted_pages.append({
                            'slug': page_data.get('slug', ''),
                            'title': page_data.get('title', ''),
                            'content': page_data.get('content', ''),
                            'format': page_data.get('format', 'markdown'),
                            'created_at': page_data.get('created_at', ''),
                            'updated_at': page_data.get('updated_at', ''),
                        })
                except Exception as e:
                    logger.debug(f"Error fetching wiki page {page.get('slug')}: {str(e)}")
                    # Add page without content if fetch fails
                    formatted_pages.append({
                        'slug': page.get('slug', ''),
                        'title': page.get('title', ''),
                        'content': '',
                        'format': 'markdown',
                    })
            
            return formatted_pages
            
        except Exception as e:
            logger.error(f"Error fetching GitLab wiki pages: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch GitLab wiki pages: {str(e)}")

