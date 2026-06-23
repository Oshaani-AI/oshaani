"""
Social Media OAuth callback views for public agent sharing.

OAuth callback URLs should NOT contain dynamic parameters like agent_id.
Instead, agent_id is passed via the OAuth state parameter.

Callback URLs (to register with OAuth providers):
- LinkedIn: https://oshaani.com/oauth/social-media/linkedin/callback/
- Facebook: https://oshaani.com/oauth/social-media/facebook/callback/
- X (formerly Twitter): https://oshaani.com/oauth/social-media/twitter/callback/
- Google: https://oshaani.com/oauth/social-media/google/callback/
- Instagram: https://oshaani.com/oauth/social-media/instagram/callback/
"""
import json
import base64
import logging
import secrets
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Supported platforms
SUPPORTED_PLATFORMS = ['linkedin', 'facebook', 'twitter', 'google', 'instagram']


def encode_oauth_state(agent_id: int, user_id: int, platform: str, csrf_token: str = None) -> str:
    """
    Encode agent_id, user_id, and other data into OAuth state parameter.
    
    Args:
        agent_id: ID of the agent being published
        user_id: ID of the user initiating the OAuth flow
        platform: Social media platform name
        csrf_token: Optional CSRF token for additional security
    
    Returns:
        Base64-encoded state string
    """
    if csrf_token is None:
        csrf_token = secrets.token_urlsafe(16)
    
    state_data = {
        'agent_id': agent_id,
        'user_id': user_id,
        'platform': platform,
        'csrf_token': csrf_token,
    }
    return base64.urlsafe_b64encode(json.dumps(state_data).encode()).decode()


def decode_oauth_state(state: str) -> dict:
    """
    Decode the OAuth state parameter to extract agent_id and other data.
    
    Args:
        state: Base64-encoded state string from OAuth callback
    
    Returns:
        Dict containing agent_id, user_id, platform, csrf_token
    
    Raises:
        ValueError: If state is invalid or cannot be decoded
    """
    try:
        state_data = json.loads(base64.urlsafe_b64decode(state.encode()).decode())
        required_keys = ['agent_id', 'user_id', 'platform', 'csrf_token']
        for key in required_keys:
            if key not in state_data:
                raise ValueError(f"Missing required key in state: {key}")
        return state_data
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to decode OAuth state: {str(e)}")
        raise ValueError(f"Invalid OAuth state: {str(e)}")


def verify_csrf_token(state_data: dict, request) -> bool:
    """
    Verify the CSRF token in the state matches what was stored.
    
    Args:
        state_data: Decoded state data containing csrf_token
        request: HTTP request object
    
    Returns:
        True if CSRF token is valid, False otherwise
    """
    csrf_token = state_data.get('csrf_token')
    user_id = state_data.get('user_id')
    platform = state_data.get('platform')
    
    # Retrieve stored CSRF token from cache
    cache_key = f"social_oauth_csrf_{user_id}_{platform}"
    stored_token = cache.get(cache_key)
    
    if not stored_token:
        logger.warning(f"No stored CSRF token found for user {user_id}, platform {platform}")
        # Allow flow to continue if no stored token (might have expired)
        return True
    
    if csrf_token != stored_token:
        logger.error(f"CSRF token mismatch for user {user_id}, platform {platform}")
        return False
    
    # Clear the token after successful verification
    cache.delete(cache_key)
    return True


@require_http_methods(["GET"])
def social_media_oauth_callback(request, platform):
    """
    Handle OAuth callback for social media publishing.
    
    The agent_id is NOT in the URL - it's extracted from the state parameter.
    This allows OAuth providers to use fixed callback URLs.
    
    Callback URLs:
    - LinkedIn: https://oshaani.com/oauth/social-media/linkedin/callback/
    - Facebook: https://oshaani.com/oauth/social-media/facebook/callback/
    - X (formerly Twitter): https://oshaani.com/oauth/social-media/twitter/callback/
    - Google: https://oshaani.com/oauth/social-media/google/callback/
    - Instagram: https://oshaani.com/oauth/social-media/instagram/callback/
    
    Args:
        request: HTTP request
        platform: Social media platform (linkedin, facebook, twitter, google, instagram)
    
    Returns:
        Redirect to appropriate page after OAuth completion
    """
    # Validate platform
    if platform not in SUPPORTED_PLATFORMS:
        logger.error(f"Invalid platform in OAuth callback: {platform}")
        messages.error(request, f"Invalid platform: {platform}")
        return redirect('agents_list')
    
    # Get OAuth parameters
    code = request.GET.get('code')
    state = request.GET.get('state')
    error = request.GET.get('error')
    error_description = request.GET.get('error_description', '')
    
    # Handle OAuth errors
    if error:
        logger.error(f"OAuth error for {platform}: {error} - {error_description}")
        if error == 'access_denied':
            messages.info(request, f"{platform.title()} authorization was cancelled.")
        else:
            messages.error(request, f"{platform.title()} authentication failed: {error_description or error}")
        return redirect('agents_list')
    
    # Validate required parameters
    if not code or not state:
        logger.error(f"Missing code or state in {platform} OAuth callback")
        messages.error(request, "Invalid OAuth callback. Missing required parameters.")
        return redirect('agents_list')
    
    try:
        # Decode state to get agent_id
        state_data = decode_oauth_state(state)
        agent_id = state_data['agent_id']
        user_id = state_data['user_id']
        
        logger.info(f"Processing {platform} OAuth callback for agent {agent_id}, user {user_id}")
        
        # Verify CSRF token
        if not verify_csrf_token(state_data, request):
            messages.error(request, "Security validation failed. Please try again.")
            return redirect('agent_detail', agent_id=agent_id)
        
        # Verify the user matches (if user is logged in)
        if request.user.is_authenticated and request.user.id != user_id:
            logger.warning(f"User mismatch in OAuth callback. Expected {user_id}, got {request.user.id}")
            messages.error(request, "User session mismatch. Please try again.")
            return redirect('agents_list')
        
        # Import agent model here to avoid circular imports
        from agents_app.models import Agent
        
        # Get the agent (verify ownership)
        try:
            if request.user.is_authenticated:
                agent = Agent.objects.get(id=agent_id, user=request.user)
            else:
                agent = Agent.objects.get(id=agent_id)
        except Agent.DoesNotExist:
            logger.error(f"Agent {agent_id} not found or user doesn't have access")
            messages.error(request, "Agent not found or you don't have access to it.")
            return redirect('agents_list')
        
        # Exchange code for access token based on platform
        access_token_data = exchange_code_for_token(platform, code, request)
        
        if not access_token_data:
            messages.error(request, f"Failed to get access token from {platform.title()}.")
            return redirect('agent_detail', agent_id=agent_id)
        
        # Store access token securely (you might want to encrypt this)
        # For now, we'll store it in the agent's metadata or a separate model
        store_social_media_token(agent, platform, access_token_data, request.user)
        
        messages.success(request, f"Successfully connected to {platform.title()}! You can now publish your agent.")
        return redirect('agent_detail', agent_id=agent_id)
        
    except ValueError as e:
        logger.error(f"Failed to decode OAuth state: {str(e)}")
        messages.error(request, "Invalid OAuth callback. Please try again.")
        return redirect('agents_list')
    except Exception as e:
        logger.error(f"Error in {platform} OAuth callback: {str(e)}", exc_info=True)
        messages.error(request, f"An error occurred during {platform.title()} authentication.")
        return redirect('agents_list')


def exchange_code_for_token(platform: str, code: str, request) -> dict:
    """
    Exchange authorization code for access token.
    
    Args:
        platform: Social media platform
        code: Authorization code from OAuth callback
        request: HTTP request object
    
    Returns:
        Dict containing access token data, or None on failure
    """
    import requests
    from django.conf import settings
    
    # Build callback URL
    callback_url = request.build_absolute_uri(
        reverse('social_media_oauth_callback', kwargs={'platform': platform})
    )
    
    # Get OAuth config based on platform
    # In production, these should come from environment variables or database
    oauth_configs = {
        'linkedin': {
            'token_url': 'https://www.linkedin.com/oauth/v2/accessToken',
            'client_id': getattr(settings, 'LINKEDIN_PUBLISH_CLIENT_ID', ''),
            'client_secret': getattr(settings, 'LINKEDIN_PUBLISH_CLIENT_SECRET', ''),
        },
        'facebook': {
            'token_url': 'https://graph.facebook.com/v18.0/oauth/access_token',
            'client_id': getattr(settings, 'FACEBOOK_CLIENT_ID', ''),
            'client_secret': getattr(settings, 'FACEBOOK_CLIENT_SECRET', ''),
        },
        'twitter': {
            'token_url': 'https://api.twitter.com/2/oauth2/token',
            'client_id': getattr(settings, 'TWITTER_CLIENT_ID', ''),
            'client_secret': getattr(settings, 'TWITTER_CLIENT_SECRET', ''),
        },
        'google': {
            'token_url': 'https://oauth2.googleapis.com/token',
            'client_id': getattr(settings, 'GOOGLE_PUBLISH_CLIENT_ID', ''),
            'client_secret': getattr(settings, 'GOOGLE_PUBLISH_CLIENT_SECRET', ''),
        },
        'instagram': {
            'token_url': 'https://api.instagram.com/oauth/access_token',
            'client_id': getattr(settings, 'INSTAGRAM_CLIENT_ID', ''),
            'client_secret': getattr(settings, 'INSTAGRAM_CLIENT_SECRET', ''),
        },
    }
    
    config = oauth_configs.get(platform)
    if not config:
        logger.error(f"No OAuth config found for platform: {platform}")
        return None
    
    if not config['client_id'] or not config['client_secret']:
        logger.error(f"Missing OAuth credentials for {platform}")
        return None
    
    try:
        # Exchange code for token
        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': callback_url,
            'client_id': config['client_id'],
            'client_secret': config['client_secret'],
        }
        
        response = requests.post(
            config['token_url'],
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=30
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Token exchange failed for {platform}: {response.status_code} - {response.text}")
            return None
            
    except requests.RequestException as e:
        logger.error(f"Request error during token exchange for {platform}: {str(e)}")
        return None


def store_social_media_token(agent, platform: str, token_data: dict, user):
    """
    Store the social media access token for an agent.
    
    Args:
        agent: Agent model instance
        platform: Social media platform
        token_data: Access token data from OAuth
        user: User who authorized the token
    """
    from django.utils import timezone
    
    # Store in agent's metadata (you might want a separate model for this)
    if not agent.metadata:
        agent.metadata = {}
    
    if 'social_media_tokens' not in agent.metadata:
        agent.metadata['social_media_tokens'] = {}
    
    agent.metadata['social_media_tokens'][platform] = {
        'access_token': token_data.get('access_token'),
        'refresh_token': token_data.get('refresh_token'),
        'expires_in': token_data.get('expires_in'),
        'token_type': token_data.get('token_type', 'Bearer'),
        'scope': token_data.get('scope', ''),
        'authorized_by': user.id,
        'authorized_at': timezone.now().isoformat(),
    }
    
    agent.save(update_fields=['metadata'])
    logger.info(f"Stored {platform} token for agent {agent.id}")


def initiate_social_media_oauth(request, agent_id: int, platform: str) -> str:
    """
    Generate the OAuth authorization URL for social media publishing.
    
    This should be called to start the OAuth flow. The agent_id is encoded
    in the state parameter, NOT in the callback URL.
    
    Args:
        request: HTTP request object
        agent_id: ID of the agent to publish
        platform: Social media platform
    
    Returns:
        OAuth authorization URL to redirect the user to
    """
    from django.conf import settings
    from urllib.parse import urlencode
    
    # Generate CSRF token and store it
    csrf_token = secrets.token_urlsafe(16)
    cache_key = f"social_oauth_csrf_{request.user.id}_{platform}"
    cache.set(cache_key, csrf_token, timeout=600)  # 10 minutes
    
    # Encode state with agent_id
    state = encode_oauth_state(
        agent_id=agent_id,
        user_id=request.user.id,
        platform=platform,
        csrf_token=csrf_token
    )
    
    # Build callback URL (fixed, without agent_id)
    callback_url = request.build_absolute_uri(
        reverse('social_media_oauth_callback', kwargs={'platform': platform})
    )
    
    # OAuth authorization URLs and scopes
    oauth_configs = {
        'linkedin': {
            'auth_url': 'https://www.linkedin.com/oauth/v2/authorization',
            'client_id': getattr(settings, 'LINKEDIN_PUBLISH_CLIENT_ID', ''),
            'scope': 'w_member_social',
        },
        'facebook': {
            'auth_url': 'https://www.facebook.com/v18.0/dialog/oauth',
            'client_id': getattr(settings, 'FACEBOOK_CLIENT_ID', ''),
            'scope': 'pages_manage_posts,pages_read_engagement',
        },
        'twitter': {
            'auth_url': 'https://twitter.com/i/oauth2/authorize',
            'client_id': getattr(settings, 'TWITTER_CLIENT_ID', ''),
            'scope': 'tweet.read tweet.write users.read offline.access',
        },
        'google': {
            'auth_url': 'https://accounts.google.com/o/oauth2/v2/auth',
            'client_id': getattr(settings, 'GOOGLE_PUBLISH_CLIENT_ID', ''),
            'scope': 'https://www.googleapis.com/auth/youtube.upload',
        },
        'instagram': {
            'auth_url': 'https://api.instagram.com/oauth/authorize',
            'client_id': getattr(settings, 'INSTAGRAM_CLIENT_ID', ''),
            'scope': 'instagram_basic,instagram_content_publish',
        },
    }
    
    config = oauth_configs.get(platform)
    if not config:
        raise ValueError(f"Unsupported platform: {platform}")
    
    params = {
        'client_id': config['client_id'],
        'redirect_uri': callback_url,
        'scope': config['scope'],
        'state': state,
        'response_type': 'code',
    }
    
    # Twitter uses PKCE
    if platform == 'twitter':
        params['code_challenge'] = 'challenge'
        params['code_challenge_method'] = 'plain'
    
    auth_url = f"{config['auth_url']}?{urlencode(params)}"
    return auth_url




