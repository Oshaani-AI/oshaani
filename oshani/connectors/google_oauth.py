"""Google OAuth implementation for fetching training data."""
import logging
import requests
from typing import Dict, Any, Optional, List
from django.utils import timezone
from datetime import timedelta
from .models import Connector, ConnectorSync

logger = logging.getLogger(__name__)


class GoogleOAuthClient:
    """Client for Google OAuth authentication and API access."""
    
    # Google OAuth endpoints (OAuth 2.0)
    OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
    API_BASE_URL = "https://www.googleapis.com"
    
    # Google API scopes
    SCOPES = {
        'drive': 'https://www.googleapis.com/auth/drive.readonly',
        'docs': 'https://www.googleapis.com/auth/documents.readonly',
        'sheets': 'https://www.googleapis.com/auth/spreadsheets.readonly',
        'gmail': 'https://www.googleapis.com/auth/gmail.readonly',
    }
    
    def __init__(self, connector: Connector):
        """Initialize Google OAuth client with connector."""
        self.connector = connector
        self.base_url = connector.base_url.rstrip('/') if connector.base_url else 'https://www.googleapis.com'
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
            sync_type = self.connector.configuration.get('sync_type', 'drive')
            scopes = []
            
            if sync_type == 'drive':
                scopes = [self.SCOPES['drive']]
            elif sync_type == 'docs':
                scopes = [self.SCOPES['docs'], self.SCOPES['drive']]
            elif sync_type == 'sheets':
                scopes = [self.SCOPES['sheets'], self.SCOPES['drive']]
            elif sync_type == 'gmail':
                scopes = [self.SCOPES['gmail']]
            else:
                scopes = [self.SCOPES['drive']]  # Default to Drive
            
            scope_string = ' '.join(scopes)
            
            params = {
                'client_id': self.client_id,
                'redirect_uri': callback_url,
                'response_type': 'code',
                'scope': scope_string,
                'access_type': 'offline',  # Required to get refresh token
                'prompt': 'consent',  # Force consent to get refresh token
                'state': state,
            }
            
            from urllib.parse import urlencode
            authorization_url = f"{self.OAUTH_AUTHORIZE_URL}?{urlencode(params)}"
            
            return {
                'authorization_url': authorization_url,
                'state': state,
            }
            
        except Exception as e:
            logger.error(f"Error getting Google authorization URL: {str(e)}", exc_info=True)
            raise Exception(f"Failed to get authorization URL: {str(e)}")
    
    def handle_oauth_callback(self, code: str, state: str, redirect_uri: str = None) -> Dict[str, Any]:
        """Handle OAuth callback and exchange code for access token.
        
        Args:
            code: Authorization code from callback
            state: State parameter for verification
            redirect_uri: Exact callback URL used in the authorization request (must match).
                          If not provided, falls back to connector.site_url + path.
            
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
            
            # Use the same redirect_uri that was used in the authorization request (required by Google).
            # If not passed, fall back to connector.site_url (may cause 400 if mismatch).
            if redirect_uri is None or redirect_uri == '':
                base = (self.connector.site_url or '').rstrip('/')
                redirect_uri = base + f'/api/connectors/{self.connector.id}/oauth/callback/'
            logger.info(f"Google OAuth token exchange using redirect_uri: {redirect_uri}")
            
            data = {
                'grant_type': 'authorization_code',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'code': code,
                'redirect_uri': redirect_uri,
            }
            
            # Add timeout to prevent hanging in async contexts
            response = requests.post(self.OAUTH_TOKEN_URL, data=data, timeout=10)
            if not response.ok:
                try:
                    err_body = response.json()
                    logger.error(
                        "Google token endpoint error: status=%s, error=%s, description=%s",
                        response.status_code,
                        err_body.get('error', ''),
                        err_body.get('error_description', response.text[:500]),
                    )
                except Exception:
                    logger.error("Google token endpoint error: status=%s, body=%s", response.status_code, response.text[:500])
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
            logger.error(f"Error handling Google OAuth callback: {str(e)}", exc_info=True)
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
                refresh_token=self.connector.refresh_token,  # Keep existing refresh token
                expires_in=token_data.get('expires_in')
            )
            self.connector.save()
            
            return True
            
        except Exception as e:
            logger.error(f"Error refreshing Google access token: {str(e)}", exc_info=True)
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
    
    def fetch_drive_files(self, folder_id: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch Google Drive files.
        
        Args:
            folder_id: Optional folder ID to filter files
            limit: Maximum number of files to fetch
            
        Returns:
            List of file dictionaries
        """
        try:
            headers = self._get_headers()
            api_url = f"{self.API_BASE_URL}/drive/v3/files"
            
            params = {
                'pageSize': min(limit, 100),
                'fields': 'nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, webViewLink, size)',
            }
            
            if folder_id:
                params['q'] = f"'{folder_id}' in parents"
            else:
                params['q'] = "trashed=false"
            
            all_files = []
            page_token = None
            
            while len(all_files) < limit:
                if page_token:
                    params['pageToken'] = page_token
                
                response = requests.get(api_url, headers=headers, params=params)
                response.raise_for_status()
                
                data = response.json()
                files = data.get('files', [])
                
                if not files:
                    break
                
                all_files.extend(files[:limit - len(all_files)])
                
                page_token = data.get('nextPageToken')
                if not page_token or len(all_files) >= limit:
                    break
            
            # Format files
            formatted_files = []
            for file in all_files:
                formatted_files.append({
                    'id': file.get('id'),
                    'name': file.get('name', ''),
                    'mimeType': file.get('mimeType', ''),
                    'createdTime': file.get('createdTime', ''),
                    'modifiedTime': file.get('modifiedTime', ''),
                    'webViewLink': file.get('webViewLink', ''),
                    'size': file.get('size', '0'),
                })
            
            return formatted_files
            
        except Exception as e:
            logger.error(f"Error fetching Google Drive files: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch Google Drive files: {str(e)}")
    
    def fetch_drive_file_content(self, file_id: str, mime_type: str = None) -> Dict[str, Any]:
        """Fetch content of a Google Drive file.
        
        Args:
            file_id: Google Drive file ID
            mime_type: MIME type of the file
            
        Returns:
            Dict with file content and metadata
        """
        try:
            headers = self._get_headers()
            
            # Determine export format based on MIME type
            export_mime_type = None
            if mime_type:
                if 'document' in mime_type or 'docs' in mime_type:
                    export_mime_type = 'text/plain'
                elif 'spreadsheet' in mime_type or 'sheets' in mime_type:
                    export_mime_type = 'text/csv'
                elif 'presentation' in mime_type or 'slides' in mime_type:
                    export_mime_type = 'text/plain'
            
            if export_mime_type:
                # Export Google Workspace files
                api_url = f"{self.API_BASE_URL}/drive/v3/files/{file_id}/export"
                params = {'mimeType': export_mime_type}
                response = requests.get(api_url, headers=headers, params=params)
            else:
                # Download regular files
                api_url = f"{self.API_BASE_URL}/drive/v3/files/{file_id}"
                params = {'alt': 'media'}
                response = requests.get(api_url, headers=headers, params=params)
            
            response.raise_for_status()
            
            # Get file metadata
            metadata_url = f"{self.API_BASE_URL}/drive/v3/files/{file_id}"
            metadata_response = requests.get(metadata_url, headers=headers, params={'fields': 'id, name, mimeType, size, createdTime, modifiedTime'})
            metadata = metadata_response.json() if metadata_response.status_code == 200 else {}
            
            content = response.text if hasattr(response, 'text') else response.content.decode('utf-8', errors='ignore')
            
            return {
                'id': file_id,
                'name': metadata.get('name', ''),
                'mimeType': metadata.get('mimeType', mime_type or ''),
                'content': content,
                'size': metadata.get('size', len(content)),
                'createdTime': metadata.get('createdTime', ''),
                'modifiedTime': metadata.get('modifiedTime', ''),
            }
            
        except Exception as e:
            logger.error(f"Error fetching Google Drive file content: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch file content: {str(e)}")
    
    def fetch_docs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch Google Docs documents.
        
        Args:
            limit: Maximum number of documents to fetch
            
        Returns:
            List of document dictionaries
        """
        try:
            # First get list of Google Docs from Drive
            files = self.fetch_drive_files(limit=limit)
            docs = [f for f in files if 'document' in f.get('mimeType', '')]
            
            # Fetch content for each document
            formatted_docs = []
            for doc in docs[:limit]:
                try:
                    content = self.fetch_drive_file_content(doc['id'], doc['mimeType'])
                    formatted_docs.append({
                        'id': content.get('id'),
                        'name': content.get('name', ''),
                        'content': content.get('content', ''),
                        'createdTime': content.get('createdTime', ''),
                        'modifiedTime': content.get('modifiedTime', ''),
                        'webViewLink': doc.get('webViewLink', ''),
                    })
                except Exception as e:
                    logger.warning(f"Error fetching doc {doc.get('id')}: {str(e)}")
                    # Add doc without content if fetch fails
                    formatted_docs.append({
                        'id': doc.get('id'),
                        'name': doc.get('name', ''),
                        'content': '',
                        'createdTime': doc.get('createdTime', ''),
                        'modifiedTime': doc.get('modifiedTime', ''),
                        'webViewLink': doc.get('webViewLink', ''),
                    })
            
            return formatted_docs
            
        except Exception as e:
            logger.error(f"Error fetching Google Docs: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch Google Docs: {str(e)}")
    
    def fetch_sheets(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch Google Sheets spreadsheets.
        
        Args:
            limit: Maximum number of spreadsheets to fetch
            
        Returns:
            List of spreadsheet dictionaries
        """
        try:
            # First get list of Google Sheets from Drive
            files = self.fetch_drive_files(limit=limit)
            sheets = [f for f in files if 'spreadsheet' in f.get('mimeType', '')]
            
            # Fetch content for each spreadsheet
            formatted_sheets = []
            for sheet in sheets[:limit]:
                try:
                    content = self.fetch_drive_file_content(sheet['id'], sheet['mimeType'])
                    formatted_sheets.append({
                        'id': content.get('id'),
                        'name': content.get('name', ''),
                        'content': content.get('content', ''),
                        'createdTime': content.get('createdTime', ''),
                        'modifiedTime': content.get('modifiedTime', ''),
                        'webViewLink': sheet.get('webViewLink', ''),
                    })
                except Exception as e:
                    logger.warning(f"Error fetching sheet {sheet.get('id')}: {str(e)}")
                    # Add sheet without content if fetch fails
                    formatted_sheets.append({
                        'id': sheet.get('id'),
                        'name': sheet.get('name', ''),
                        'content': '',
                        'createdTime': sheet.get('createdTime', ''),
                        'modifiedTime': sheet.get('modifiedTime', ''),
                        'webViewLink': sheet.get('webViewLink', ''),
                    })
            
            return formatted_sheets
            
        except Exception as e:
            logger.error(f"Error fetching Google Sheets: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch Google Sheets: {str(e)}")
    
    def fetch_gmail_messages(self, query: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch Gmail messages.
        
        Args:
            query: Gmail search query (optional)
            limit: Maximum number of messages to fetch
            
        Returns:
            List of message dictionaries
        """
        try:
            headers = self._get_headers()
            api_url = f"{self.API_BASE_URL}/gmail/v1/users/me/messages"
            
            params = {
                'maxResults': min(limit, 100),
            }
            
            if query:
                params['q'] = query
            
            all_messages = []
            page_token = None
            
            while len(all_messages) < limit:
                if page_token:
                    params['pageToken'] = page_token
                
                response = requests.get(api_url, headers=headers, params=params)
                response.raise_for_status()
                
                data = response.json()
                message_ids = data.get('messages', [])
                
                if not message_ids:
                    break
                
                # Fetch full message details
                for msg_id in message_ids[:limit - len(all_messages)]:
                    try:
                        msg_url = f"{self.API_BASE_URL}/gmail/v1/users/me/messages/{msg_id.get('id')}"
                        msg_response = requests.get(msg_url, headers=headers, params={'format': 'full'})
                        msg_response.raise_for_status()
                        msg_data = msg_response.json()
                        
                        # Extract message content
                        payload = msg_data.get('payload', {})
                        headers_list = payload.get('headers', [])
                        
                        subject = next((h['value'] for h in headers_list if h['name'] == 'Subject'), '')
                        sender = next((h['value'] for h in headers_list if h['name'] == 'From'), '')
                        date = next((h['value'] for h in headers_list if h['name'] == 'Date'), '')
                        
                        # Get body
                        body = ''
                        if 'parts' in payload:
                            for part in payload['parts']:
                                if part.get('mimeType') == 'text/plain':
                                    body_data = part.get('body', {}).get('data', '')
                                    if body_data:
                                        import base64
                                        body = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')
                                        break
                        elif payload.get('body', {}).get('data'):
                            import base64
                            body_data = payload['body']['data']
                            body = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')
                        
                        all_messages.append({
                            'id': msg_data.get('id'),
                            'threadId': msg_data.get('threadId'),
                            'subject': subject,
                            'from': sender,
                            'date': date,
                            'snippet': msg_data.get('snippet', ''),
                            'body': body,
                        })
                    except Exception as e:
                        logger.warning(f"Error fetching message {msg_id.get('id')}: {str(e)}")
                
                page_token = data.get('nextPageToken')
                if not page_token or len(all_messages) >= limit:
                    break
            
            return all_messages
            
        except Exception as e:
            logger.error(f"Error fetching Gmail messages: {str(e)}", exc_info=True)
            raise Exception(f"Failed to fetch Gmail messages: {str(e)}")

