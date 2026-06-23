"""Microsoft OAuth implementation for fetching training data."""
import logging
import requests
from typing import Dict, Any, Optional, List
from django.utils import timezone
from datetime import timedelta
from .models import Connector, ConnectorSync

logger = logging.getLogger(__name__)


class MicrosoftOAuthClient:
    """Client for Microsoft OAuth authentication and API access."""
    
    # Microsoft OAuth endpoints (OAuth 2.0)
    OAUTH_AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    OAUTH_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    API_BASE_URL = "https://graph.microsoft.com/v1.0"
    
    # Microsoft API scopes
    SCOPES = {
        'onedrive': 'Files.Read',
        'sharepoint': 'Sites.Read.All',
        'outlook': 'Mail.Read',
        'teams': 'ChannelMessage.Read.All',
    }
    
    def __init__(self, connector: Connector):
        """Initialize Microsoft OAuth client with connector."""
        self.connector = connector
        self.base_url = connector.base_url.rstrip('/') if connector.base_url else 'https://graph.microsoft.com'
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
            
            # Determine scopes from configuration or use default
            sync_type = self.connector.configuration.get('sync_type', 'onedrive')
            scopes = []
            
            if sync_type == 'onedrive':
                scopes = [self.SCOPES['onedrive']]
            elif sync_type == 'sharepoint':
                scopes = [self.SCOPES['sharepoint'], self.SCOPES['onedrive']]
            elif sync_type == 'outlook':
                scopes = [self.SCOPES['outlook']]
            elif sync_type == 'teams':
                scopes = [self.SCOPES['teams']]
            else:
                scopes = [self.SCOPES['onedrive']]  # Default to OneDrive
            
            scope_string = ' '.join(scopes)
            
            params = {
                'client_id': self.client_id,
                'redirect_uri': callback_url,
                'response_type': 'code',
                'scope': scope_string,
                'response_mode': 'query',
                'state': state,
            }
            
            from urllib.parse import urlencode
            authorization_url = f"{self.OAUTH_AUTHORIZE_URL}?{urlencode(params)}"
            
            return {
                'authorization_url': authorization_url,
                'state': state,
            }
            
        except Exception as e:
            logger.error(f"Error getting Microsoft authorization URL: {str(e)}", exc_info=True)
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
            callback_url = self.connector.site_url + f'/api/connectors/{self.connector.id}/oauth/callback/'
            
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
            logger.error(f"Error handling Microsoft OAuth callback: {str(e)}", exc_info=True)
            self.connector.status = 'error'
            self.connector.save()
            raise Exception(f"Failed to complete OAuth flow: {str(e)}")
    
    def _refresh_access_token(self) -> bool:
        """Refresh the access token using refresh token."""
        try:
            if not self.connector.refresh_token:
                return False
            
            data = {
                'grant_type': 'refresh_token',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.connector.refresh_token,
            }
            
            # Add timeout to prevent hanging in async contexts
            response = requests.post(self.OAUTH_TOKEN_URL, data=data, timeout=10)
            response.raise_for_status()
            
            token_data = response.json()
            
            # Update connector with new access token
            self.connector.update_token(
                access_token=token_data.get('access_token'),
                refresh_token=token_data.get('refresh_token', self.connector.refresh_token),
                expires_in=token_data.get('expires_in')
            )
            self.connector.save()
            
            return True
            
        except Exception as e:
            logger.error(f"Error refreshing Microsoft access token: {str(e)}", exc_info=True)
            return False
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for authenticated API requests."""
        if not self.connector.is_token_valid():
            # Try to refresh token
            if not self._refresh_access_token():
                raise Exception("Access token is invalid or expired and refresh failed")
        
        return {
            'Authorization': f'Bearer {self.connector.access_token}',
            'Content-Type': 'application/json',
        }
    
    def fetch_onedrive_files(self, folder_path: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch OneDrive files.
        
        Args:
            folder_path: Optional folder path (e.g., '/Documents')
            limit: Maximum number of files to fetch
            
        Returns:
            List of file dictionaries
        """
        try:
            headers = self._get_headers()
            
            if folder_path:
                api_url = f"{self.API_BASE_URL}/me/drive/root:{folder_path}:/children"
            else:
                api_url = f"{self.API_BASE_URL}/me/drive/root/children"
            
            all_files = []
            next_link = api_url
            
            while len(all_files) < limit and next_link:
                params = {
                    '$top': min(limit - len(all_files), 100),
                    '$select': 'id,name,size,createdDateTime,lastModifiedDateTime,webUrl,file,mimeType',
                }
                
                response = requests.get(next_link, headers=headers, params=params)
                response.raise_for_status()
                
                data = response.json()
                files = data.get('value', [])
                
                if not files:
                    break
                
                all_files.extend(files[:limit - len(all_files)])
                
                next_link = data.get('@odata.nextLink')
                if not next_link or len(all_files) >= limit:
                    break
            
            # Format files
            formatted_files = []
            for file in all_files:
                formatted_files.append({
                    'id': file.get('id'),
                    'name': file.get('name', ''),
                    'size': file.get('size', 0),
                    'mimeType': file.get('file', {}).get('mimeType', '') if file.get('file') else '',
                    'createdDateTime': file.get('createdDateTime', ''),
                    'lastModifiedDateTime': file.get('lastModifiedDateTime', ''),
                    'webUrl': file.get('webUrl', ''),
                    'isFile': 'file' in file,
                })
            
            return formatted_files
            
        except Exception as e:
            logger.error(f"Error fetching OneDrive files: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch OneDrive files: {str(e)}")
    
    def fetch_onedrive_file_content(self, file_id: str) -> Dict[str, Any]:
        """Fetch content of a OneDrive file.
        
        Args:
            file_id: OneDrive file ID
            
        Returns:
            Dict with file content and metadata
        """
        try:
            headers = self._get_headers()
            
            # Get file metadata
            metadata_url = f"{self.API_BASE_URL}/me/drive/items/{file_id}"
            metadata_response = requests.get(metadata_url, headers=headers)
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            
            # Get file content
            content_url = f"{self.API_BASE_URL}/me/drive/items/{file_id}/content"
            content_response = requests.get(content_url, headers=headers)
            content_response.raise_for_status()
            
            content = content_response.text if hasattr(content_response, 'text') else content_response.content.decode('utf-8', errors='ignore')
            
            return {
                'id': metadata.get('id'),
                'name': metadata.get('name', ''),
                'mimeType': metadata.get('file', {}).get('mimeType', '') if metadata.get('file') else '',
                'content': content,
                'size': metadata.get('size', len(content)),
                'createdDateTime': metadata.get('createdDateTime', ''),
                'lastModifiedDateTime': metadata.get('lastModifiedDateTime', ''),
                'webUrl': metadata.get('webUrl', ''),
            }
            
        except Exception as e:
            logger.error(f"Error fetching OneDrive file content: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch file content: {str(e)}")
    
    def fetch_sharepoint_sites(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch SharePoint sites.
        
        Args:
            limit: Maximum number of sites to fetch
            
        Returns:
            List of site dictionaries
        """
        try:
            headers = self._get_headers()
            api_url = f"{self.API_BASE_URL}/sites"
            
            params = {
                '$top': min(limit, 100),
                '$select': 'id,name,webUrl,displayName,description',
            }
            
            response = requests.get(api_url, headers=headers, params=params)
            response.raise_for_status()
            
            data = response.json()
            sites = data.get('value', [])
            
            return [
                {
                    'id': site.get('id'),
                    'name': site.get('name', ''),
                    'displayName': site.get('displayName', ''),
                    'webUrl': site.get('webUrl', ''),
                    'description': site.get('description', ''),
                }
                for site in sites
            ]
            
        except Exception as e:
            logger.error(f"Error fetching SharePoint sites: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch SharePoint sites: {str(e)}")
    
    def fetch_sharepoint_documents(self, site_id: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch SharePoint documents.
        
        Args:
            site_id: Optional SharePoint site ID
            limit: Maximum number of documents to fetch
            
        Returns:
            List of document dictionaries
        """
        try:
            headers = self._get_headers()
            
            if site_id:
                api_url = f"{self.API_BASE_URL}/sites/{site_id}/drive/root/children"
            else:
                # Get default site
                api_url = f"{self.API_BASE_URL}/sites/root/drive/root/children"
            
            all_docs = []
            next_link = api_url
            
            while len(all_docs) < limit and next_link:
                params = {
                    '$top': min(limit - len(all_docs), 100),
                    '$select': 'id,name,size,createdDateTime,lastModifiedDateTime,webUrl,file,mimeType',
                }
                
                response = requests.get(next_link, headers=headers, params=params)
                response.raise_for_status()
                
                data = response.json()
                files = data.get('value', [])
                
                if not files:
                    break
                
                all_docs.extend(files[:limit - len(all_docs)])
                
                next_link = data.get('@odata.nextLink')
                if not next_link or len(all_docs) >= limit:
                    break
            
            # Format documents
            formatted_docs = []
            for doc in all_docs:
                formatted_docs.append({
                    'id': doc.get('id'),
                    'name': doc.get('name', ''),
                    'size': doc.get('size', 0),
                    'mimeType': doc.get('file', {}).get('mimeType', '') if doc.get('file') else '',
                    'createdDateTime': doc.get('createdDateTime', ''),
                    'lastModifiedDateTime': doc.get('lastModifiedDateTime', ''),
                    'webUrl': doc.get('webUrl', ''),
                })
            
            return formatted_docs
            
        except Exception as e:
            logger.error(f"Error fetching SharePoint documents: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch SharePoint documents: {str(e)}")
    
    def fetch_outlook_messages(self, folder_id: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch Outlook messages.
        
        Args:
            folder_id: Optional folder ID (e.g., 'Inbox')
            limit: Maximum number of messages to fetch
            
        Returns:
            List of message dictionaries
        """
        try:
            headers = self._get_headers()
            
            if folder_id:
                api_url = f"{self.API_BASE_URL}/me/mailFolders/{folder_id}/messages"
            else:
                api_url = f"{self.API_BASE_URL}/me/messages"
            
            params = {
                '$top': min(limit, 100),
                '$select': 'id,subject,from,receivedDateTime,bodyPreview,body,toRecipients',
            }
            
            all_messages = []
            next_link = api_url
            
            while len(all_messages) < limit and next_link:
                response = requests.get(next_link, headers=headers, params=params)
                response.raise_for_status()
                
                data = response.json()
                messages = data.get('value', [])
                
                if not messages:
                    break
                
                all_messages.extend(messages[:limit - len(all_messages)])
                
                next_link = data.get('@odata.nextLink')
                if not next_link or len(all_messages) >= limit:
                    break
            
            # Format messages
            formatted_messages = []
            for msg in all_messages:
                formatted_messages.append({
                    'id': msg.get('id'),
                    'subject': msg.get('subject', ''),
                    'from': msg.get('from', {}).get('emailAddress', {}).get('address', '') if msg.get('from') else '',
                    'receivedDateTime': msg.get('receivedDateTime', ''),
                    'bodyPreview': msg.get('bodyPreview', ''),
                    'body': msg.get('body', {}).get('content', '') if msg.get('body') else '',
                    'toRecipients': [r.get('emailAddress', {}).get('address', '') for r in msg.get('toRecipients', [])],
                })
            
            return formatted_messages
            
        except Exception as e:
            logger.error(f"Error fetching Outlook messages: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch Outlook messages: {str(e)}")
    
    def fetch_teams_messages(self, team_id: str = None, channel_id: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch Microsoft Teams messages.
        
        Args:
            team_id: Optional team ID
            channel_id: Optional channel ID
            limit: Maximum number of messages to fetch
            
        Returns:
            List of message dictionaries
        """
        try:
            headers = self._get_headers()
            
            if team_id and channel_id:
                api_url = f"{self.API_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages"
            elif team_id:
                # Get all channels for team, then messages from first channel
                channels_url = f"{self.API_BASE_URL}/teams/{team_id}/channels"
                channels_response = requests.get(channels_url, headers=headers)
                channels_response.raise_for_status()
                channels = channels_response.json().get('value', [])
                if not channels:
                    return []
                channel_id = channels[0].get('id')
                api_url = f"{self.API_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages"
            else:
                # Get all teams, then messages from first team/channel
                teams_url = f"{self.API_BASE_URL}/me/joinedTeams"
                teams_response = requests.get(teams_url, headers=headers)
                teams_response.raise_for_status()
                teams = teams_response.json().get('value', [])
                if not teams:
                    return []
                team_id = teams[0].get('id')
                channels_url = f"{self.API_BASE_URL}/teams/{team_id}/channels"
                channels_response = requests.get(channels_url, headers=headers)
                channels_response.raise_for_status()
                channels = channels_response.json().get('value', [])
                if not channels:
                    return []
                channel_id = channels[0].get('id')
                api_url = f"{self.API_BASE_URL}/teams/{team_id}/channels/{channel_id}/messages"
            
            params = {
                '$top': min(limit, 50),  # Teams API limit is lower
                '$select': 'id,createdDateTime,body,from',
            }
            
            response = requests.get(api_url, headers=headers, params=params)
            response.raise_for_status()
            
            data = response.json()
            messages = data.get('value', [])
            
            # Format messages
            formatted_messages = []
            for msg in messages[:limit]:
                formatted_messages.append({
                    'id': msg.get('id'),
                    'createdDateTime': msg.get('createdDateTime', ''),
                    'body': msg.get('body', {}).get('content', '') if msg.get('body') else '',
                    'from': msg.get('from', {}).get('user', {}).get('displayName', '') if msg.get('from') else '',
                })
            
            return formatted_messages
            
        except Exception as e:
            logger.error(f"Error fetching Teams messages: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch Teams messages: {str(e)}")

