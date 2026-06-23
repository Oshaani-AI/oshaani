"""OAuth utilities for social media platforms."""
import os
import secrets
import requests
import logging
from urllib.parse import urlencode
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def get_oauth_credentials(platform):
    """Get OAuth credentials from admin-managed configuration."""
    from .models import SocialMediaOAuthConfig
    config = SocialMediaOAuthConfig.get_config(platform)
    if config:
        return config.client_id, config.client_secret
    return None, None


class LinkedInPublishOAuth:
    """LinkedIn OAuth for publishing posts using the new Posts API."""
    
    AUTHORIZATION_URL = 'https://www.linkedin.com/oauth/v2/authorization'
    TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
    USERINFO_URL = 'https://api.linkedin.com/v2/userinfo'  # OpenID Connect userinfo
    POSTS_URL = 'https://api.linkedin.com/v2/posts'  # New Posts API
    
    # Scopes needed for publishing
    SCOPES = ['openid', 'profile', 'email', 'w_member_social']
    
    @staticmethod
    def get_authorization_url(request, redirect_uri, client_id=None):
        """Generate LinkedIn OAuth authorization URL."""
        state = secrets.token_urlsafe(32)
        cache.set(f'linkedin_publish_state_{state}', state, timeout=600)
        
        # Get client_id from admin-managed config
        if not client_id:
            client_id, _ = get_oauth_credentials('linkedin')
            if not client_id:
                raise ValueError("LinkedIn OAuth credentials not configured. Please contact administrator.")
        
        params = {
            'response_type': 'code',
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'state': state,
            'scope': ' '.join(LinkedInPublishOAuth.SCOPES),
        }
        
        return f"{LinkedInPublishOAuth.AUTHORIZATION_URL}?{urlencode(params)}", state
    
    @staticmethod
    def exchange_code_for_token(code, redirect_uri, client_id=None, client_secret=None):
        """Exchange authorization code for access token."""
        # Get credentials from admin-managed config if not provided
        if not client_id or not client_secret:
            admin_client_id, admin_client_secret = get_oauth_credentials('linkedin')
            if not admin_client_id or not admin_client_secret:
                raise ValueError("LinkedIn OAuth credentials not configured. Please contact administrator.")
            client_id = client_id or admin_client_id
            client_secret = client_secret or admin_client_secret
        
        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': client_id,
            'client_secret': client_secret,
        }
        
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        
        try:
            response = requests.post(LinkedInPublishOAuth.TOKEN_URL, data=data, headers=headers, timeout=30)
            response.raise_for_status()
            token_data = response.json()
            return token_data.get('access_token'), token_data.get('refresh_token'), token_data.get('expires_in')
        except Exception as e:
            logger.error(f"Failed to exchange LinkedIn code for token: {e}")
            raise
    
    @staticmethod
    def publish_post(access_token, text, share_url=None):
        """Publish a post to LinkedIn using the new Posts API."""
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'X-Restli-Protocol-Version': '2.0.0',
            'LinkedIn-Version': '202401',  # Use dated API version
        }
        
        # Get user info using OpenID Connect userinfo endpoint
        userinfo_response = requests.get(LinkedInPublishOAuth.USERINFO_URL, headers=headers, timeout=30)
        if userinfo_response.status_code != 200:
            logger.error(f"LinkedIn userinfo failed: {userinfo_response.status_code} - {userinfo_response.text}")
            userinfo_response.raise_for_status()
        
        userinfo = userinfo_response.json()
        # The 'sub' field contains the member ID
        member_id = userinfo.get('sub')
        if not member_id:
            raise ValueError("Could not get LinkedIn member ID from userinfo")
        
        author_urn = f"urn:li:person:{member_id}"
        
        # Build post using the new Posts API format
        post_data = {
            "author": author_urn,
            "commentary": text,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": []
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False
        }
        
        # Add article/link if share_url is provided
        if share_url:
            post_data["content"] = {
                "article": {
                    "source": share_url,
                    "title": text.split('\n')[0][:200] if text else "AI Agent",
                    "description": text[:200] if len(text) > 200 else text
                }
            }
        
        logger.info(f"Publishing to LinkedIn for {author_urn}")
        response = requests.post(LinkedInPublishOAuth.POSTS_URL, json=post_data, headers=headers, timeout=30)
        
        if response.status_code not in [200, 201]:
            logger.error(f"LinkedIn publish failed: {response.status_code} - {response.text}")
            response.raise_for_status()
        
        logger.info(f"LinkedIn post published successfully")
        return response.json() if response.text else {"success": True}


class FacebookPublishOAuth:
    """Facebook OAuth for publishing posts."""
    
    AUTHORIZATION_URL = 'https://www.facebook.com/v18.0/dialog/oauth'
    TOKEN_URL = 'https://graph.facebook.com/v18.0/oauth/access_token'
    GRAPH_API_URL = 'https://graph.facebook.com/v18.0'
    
    SCOPES = ['pages_manage_posts', 'pages_read_engagement', 'publish_to_groups']
    
    @staticmethod
    def get_authorization_url(request, redirect_uri, client_id=None):
        """Generate Facebook OAuth authorization URL."""
        state = secrets.token_urlsafe(32)
        cache.set(f'facebook_publish_state_{state}', state, timeout=600)
        
        # Get client_id from admin-managed config
        if not client_id:
            client_id, _ = get_oauth_credentials('facebook')
            if not client_id:
                raise ValueError("Facebook OAuth credentials not configured. Please contact administrator.")
        
        params = {
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'state': state,
            'scope': ','.join(FacebookPublishOAuth.SCOPES),
            'response_type': 'code',
        }
        
        return f"{FacebookPublishOAuth.AUTHORIZATION_URL}?{urlencode(params)}", state
    
    @staticmethod
    def exchange_code_for_token(code, redirect_uri, client_id=None, client_secret=None):
        """Exchange authorization code for access token."""
        # Get credentials from admin-managed config if not provided
        if not client_id or not client_secret:
            admin_client_id, admin_client_secret = get_oauth_credentials('facebook')
            if not admin_client_id or not admin_client_secret:
                raise ValueError("Facebook OAuth credentials not configured. Please contact administrator.")
            client_id = client_id or admin_client_id
            client_secret = client_secret or admin_client_secret
        
        data = {
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'code': code,
        }
        
        try:
            response = requests.get(FacebookPublishOAuth.TOKEN_URL, params=data, timeout=30)
            response.raise_for_status()
            token_data = response.json()
            return token_data.get('access_token'), None, token_data.get('expires_in')
        except Exception as e:
            logger.error(f"Failed to exchange Facebook code for token: {e}")
            raise
    
    @staticmethod
    def publish_post(access_token, text, share_url=None):
        """Publish a post to Facebook."""
        # Get user's pages
        headers = {'Authorization': f'Bearer {access_token}'}
        
        # For now, publish to user's feed
        # In production, you might want to let user select a page
        post_data = {
            'message': text,
        }
        
        if share_url:
            post_data['link'] = share_url
        
        response = requests.post(
            f"{FacebookPublishOAuth.GRAPH_API_URL}/me/feed",
            data=post_data,
            headers=headers,
            timeout=30
        )
        response.raise_for_status()
        return response.json()


class TwitterPublishOAuth:
    """X (formerly Twitter) OAuth 2.0 for publishing posts."""
    
    # X/Twitter OAuth endpoints - API still uses twitter.com domain
    AUTHORIZATION_URL = 'https://twitter.com/i/oauth2/authorize'
    TOKEN_URL = 'https://api.twitter.com/2/oauth2/token'
    TWEET_URL = 'https://api.twitter.com/2/tweets'
    
    SCOPES = ['tweet.read', 'tweet.write', 'users.read', 'offline.access']
    
    @staticmethod
    def get_authorization_url(request, redirect_uri, client_id=None):
        """Generate X OAuth authorization URL."""
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(32)
        cache.set(f'twitter_publish_state_{state}', {'state': state, 'code_verifier': code_verifier}, timeout=600)
        
        import hashlib
        import base64
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode().rstrip('=')
        
        # Get client_id from admin-managed config
        if not client_id:
            client_id, _ = get_oauth_credentials('twitter')
            if not client_id:
                raise ValueError("X OAuth credentials not configured. Please contact administrator.")
        
        params = {
            'response_type': 'code',
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'scope': ' '.join(TwitterPublishOAuth.SCOPES),
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
        }
        
        return f"{TwitterPublishOAuth.AUTHORIZATION_URL}?{urlencode(params)}", state
    
    @staticmethod
    def exchange_code_for_token(code, redirect_uri, code_verifier, client_id=None, client_secret=None):
        """Exchange authorization code for access token."""
        # Get credentials from admin-managed config if not provided
        if not client_id or not client_secret:
            admin_client_id, admin_client_secret = get_oauth_credentials('twitter')
            if not admin_client_id or not admin_client_secret:
                raise ValueError("X OAuth credentials not configured. Please contact administrator.")
            client_id = client_id or admin_client_id
            client_secret = client_secret or admin_client_secret
        
        import base64
        credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        
        data = {
            'code': code,
            'grant_type': 'authorization_code',
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'code_verifier': code_verifier,
        }
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': f'Basic {credentials}',
        }
        
        try:
            response = requests.post(TwitterPublishOAuth.TOKEN_URL, data=data, headers=headers, timeout=30)
            response.raise_for_status()
            token_data = response.json()
            return token_data.get('access_token'), token_data.get('refresh_token'), token_data.get('expires_in')
        except Exception as e:
            logger.error(f"Failed to exchange X code for token: {e}")
            raise
    
    @staticmethod
    def publish_post(access_token, text):
        """Publish a post to X (formerly Twitter)."""
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        }
        
        # X has 280 character limit
        if len(text) > 280:
            text = text[:277] + '...'
        
        post_data = {
            'text': text
        }
        
        response = requests.post(TwitterPublishOAuth.TWEET_URL, json=post_data, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()


class InstagramPublishOAuth:
    """Instagram OAuth for publishing posts (requires Facebook Page)."""
    
    # Instagram uses Facebook OAuth but requires a Facebook Page
    AUTHORIZATION_URL = 'https://www.facebook.com/v18.0/dialog/oauth'
    TOKEN_URL = 'https://graph.facebook.com/v18.0/oauth/access_token'
    GRAPH_API_URL = 'https://graph.facebook.com/v18.0'
    
    SCOPES = ['instagram_basic', 'instagram_content_publish', 'pages_show_list', 'pages_read_engagement']
    
    @staticmethod
    def get_authorization_url(request, redirect_uri, client_id=None):
        """Generate Instagram/Facebook OAuth authorization URL."""
        state = secrets.token_urlsafe(32)
        cache.set(f'instagram_publish_state_{state}', state, timeout=600)
        
        # Get client_id from admin-managed config
        if not client_id:
            client_id, _ = get_oauth_credentials('instagram')
            if not client_id:
                raise ValueError("Instagram OAuth credentials not configured. Please contact administrator.")
        
        params = {
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'state': state,
            'scope': ','.join(InstagramPublishOAuth.SCOPES),
            'response_type': 'code',
        }
        
        return f"{InstagramPublishOAuth.AUTHORIZATION_URL}?{urlencode(params)}", state
    
    @staticmethod
    def exchange_code_for_token(code, redirect_uri, client_id=None, client_secret=None):
        """Exchange authorization code for access token."""
        # Get credentials from admin-managed config if not provided
        if not client_id or not client_secret:
            admin_client_id, admin_client_secret = get_oauth_credentials('instagram')
            if not admin_client_id or not admin_client_secret:
                raise ValueError("Instagram OAuth credentials not configured. Please contact administrator.")
            client_id = client_id or admin_client_id
            client_secret = client_secret or admin_client_secret
        
        data = {
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'code': code,
        }
        
        try:
            response = requests.get(InstagramPublishOAuth.TOKEN_URL, params=data, timeout=30)
            response.raise_for_status()
            token_data = response.json()
            return token_data.get('access_token'), None, token_data.get('expires_in')
        except Exception as e:
            logger.error(f"Failed to exchange Instagram code for token: {e}")
            raise
    
    @staticmethod
    def publish_post(access_token, image_url, caption):
        """Publish a post to Instagram (requires image)."""
        # Instagram API requires a Facebook Page connected to Instagram Business account
        # This is more complex - for now, we'll return instructions
        headers = {'Authorization': f'Bearer {access_token}'}
        
        # Get user's Instagram Business Account ID (requires Facebook Page)
        # This is a simplified version - full implementation needs page selection
        raise NotImplementedError("Instagram publishing requires Facebook Page setup. Use the share link feature instead.")

