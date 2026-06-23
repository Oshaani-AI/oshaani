"""
Custom LinkedIn OAuth2 implementation without django-allauth.
"""
import os
import secrets
import requests
import logging
from urllib.parse import urlencode
from django.contrib.auth.models import User
from django.urls import reverse
from django.core.cache import cache

logger = logging.getLogger(__name__)

# LinkedIn OAuth2 Configuration
LINKEDIN_CLIENT_ID = os.getenv('LINKEDIN_CLIENT_ID', '')
LINKEDIN_CLIENT_SECRET = os.getenv('LINKEDIN_CLIENT_SECRET', '')
LINKEDIN_AUTHORIZATION_URL = 'https://www.linkedin.com/oauth/v2/authorization'
LINKEDIN_TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
LINKEDIN_USERINFO_URL = 'https://api.linkedin.com/v2/userinfo'

# OIDC Scopes
LINKEDIN_SCOPES = ['openid', 'profile', 'email']


def get_redirect_uri(request):
    """Get the redirect URI for LinkedIn OAuth callback."""
    scheme = 'https' if request.is_secure() else 'http'
    host = request.get_host()
    return f"{scheme}://{host}{reverse('linkedin_oauth_callback')}"


def generate_state():
    """Generate a random state for OAuth flow."""
    return secrets.token_urlsafe(32)


def store_state(request, state):
    """Store state in cache for verification."""
    cache.set(f'linkedin_oauth_state_{state}', state, timeout=600)  # 10 minutes


def verify_state(request, state):
    """Verify the state parameter."""
    cached_state = cache.get(f'linkedin_oauth_state_{state}')
    if cached_state and cached_state == state:
        cache.delete(f'linkedin_oauth_state_{state}')
        return True
    return False


def get_authorization_url(request):
    """Generate LinkedIn OAuth authorization URL."""
    state = generate_state()
    store_state(request, state)
    
    redirect_uri = get_redirect_uri(request)
    
    params = {
        'response_type': 'code',
        'client_id': LINKEDIN_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'state': state,
        'scope': ' '.join(LINKEDIN_SCOPES),
    }
    
    auth_url = f"{LINKEDIN_AUTHORIZATION_URL}?{urlencode(params)}"
    logger.info(f"Generated LinkedIn authorization URL with state: {state[:8]}...")
    return auth_url, state


def exchange_code_for_token(request, code):
    """Exchange authorization code for access token."""
    redirect_uri = get_redirect_uri(request)
    
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'client_id': LINKEDIN_CLIENT_ID,
        'client_secret': LINKEDIN_CLIENT_SECRET,
    }
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    
    try:
        response = requests.post(LINKEDIN_TOKEN_URL, data=data, headers=headers, timeout=30)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        logger.info("Successfully exchanged code for access token")
        return access_token
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to exchange code for token: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response: {e.response.text}")
        raise


def get_user_info(access_token):
    """Get user information from LinkedIn using OIDC userinfo endpoint."""
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }
    
    try:
        response = requests.get(LINKEDIN_USERINFO_URL, headers=headers, timeout=30)
        response.raise_for_status()
        user_data = response.json()
        logger.info(f"Successfully retrieved user info: {list(user_data.keys())}")
        return user_data
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to get user info: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response: {e.response.text}")
        raise


def get_or_create_user(user_data):
    """Get or create a user from LinkedIn user data.
    
    Returns:
        tuple: (user, is_new_user) where is_new_user is True if user was just created
    """
    # OIDC returns: sub, name, given_name, family_name, email, picture
    email = user_data.get('email', '')
    given_name = user_data.get('given_name', '')
    family_name = user_data.get('family_name', '')
    
    if not email:
        raise ValueError("Email is required but not provided by LinkedIn")
    
    # Try to find existing user by email
    try:
        user = User.objects.get(email=email)
        logger.info(f"Found existing user: {user.username}")
        is_new_user = False
    except User.DoesNotExist:
        # Create new user
        username = email  # Use email as username
        # Ensure username is unique
        base_username = username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}_{counter}"
            counter += 1
        
        user = User.objects.create_user(
            username=username,
            email=email,
            first_name=given_name,
            last_name=family_name,
        )
        logger.info(f"Created new user: {user.username}")
        is_new_user = True
        
        # Send welcome email for new user registration
        try:
            from .welcome_email import send_welcome_email
            send_welcome_email(user, is_new_user=True)
        except Exception as e:
            logger.error(f"Failed to send welcome email to new user {user.username}: {str(e)}", exc_info=True)
    
    # Update user info if available
    if given_name and not user.first_name:
        user.first_name = given_name
    if family_name and not user.last_name:
        user.last_name = family_name
    if user.first_name or user.last_name:
        user.save()
    
    return user, is_new_user














