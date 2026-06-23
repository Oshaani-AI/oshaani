"""
Custom Google OAuth2 implementation without django-allauth.
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

# Google OAuth2 Configuration
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
GOOGLE_AUTHORIZATION_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v2/userinfo'

# OAuth Scopes
GOOGLE_SCOPES = ['openid', 'https://www.googleapis.com/auth/userinfo.email', 'https://www.googleapis.com/auth/userinfo.profile']


def get_redirect_uri(request):
    """Get the redirect URI for Google OAuth callback."""
    scheme = 'https' if request.is_secure() else 'http'
    host = request.get_host()
    return f"{scheme}://{host}{reverse('google_oauth_callback')}"


def generate_state():
    """Generate a random state for OAuth flow."""
    return secrets.token_urlsafe(32)


def store_state(request, state):
    """Store state in cache for verification."""
    cache.set(f'google_oauth_state_{state}', state, timeout=600)  # 10 minutes


def verify_state(request, state):
    """Verify the state parameter."""
    cached_state = cache.get(f'google_oauth_state_{state}')
    if cached_state and cached_state == state:
        cache.delete(f'google_oauth_state_{state}')
        return True
    return False


def get_authorization_url(request):
    """Generate Google OAuth authorization URL."""
    state = generate_state()
    store_state(request, state)
    
    redirect_uri = get_redirect_uri(request)
    
    params = {
        'response_type': 'code',
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'state': state,
        'scope': ' '.join(GOOGLE_SCOPES),
        'access_type': 'online',
        'prompt': 'select_account',
    }
    
    auth_url = f"{GOOGLE_AUTHORIZATION_URL}?{urlencode(params)}"
    logger.info(f"Generated Google authorization URL with state: {state[:8]}...")
    return auth_url, state


def exchange_code_for_token(request, code):
    """Exchange authorization code for access token."""
    redirect_uri = get_redirect_uri(request)
    
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
    }
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    
    try:
        response = requests.post(GOOGLE_TOKEN_URL, data=data, headers=headers, timeout=30)
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
    """Get user information from Google."""
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }
    
    try:
        response = requests.get(GOOGLE_USERINFO_URL, headers=headers, timeout=30)
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
    """Get or create a user from Google user data.
    
    Returns:
        tuple: (user, is_new_user) where is_new_user is True if user was just created
    """
    # Google returns: id, email, verified_email, name, given_name, family_name, picture, locale
    email = user_data.get('email', '')
    given_name = user_data.get('given_name', '')
    family_name = user_data.get('family_name', '')
    name = user_data.get('name', '')
    
    if not email:
        raise ValueError("Email is required but not provided by Google")
    
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
            first_name=given_name or name.split()[0] if name else '',
            last_name=family_name or ' '.join(name.split()[1:]) if name and len(name.split()) > 1 else '',
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
    elif name and not user.first_name:
        user.first_name = name.split()[0] if name else ''
    if family_name and not user.last_name:
        user.last_name = family_name
    elif name and not user.last_name and len(name.split()) > 1:
        user.last_name = ' '.join(name.split()[1:])
    if user.first_name or user.last_name:
        user.save()
    
    return user, is_new_user










