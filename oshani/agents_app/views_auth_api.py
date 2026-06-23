"""
REST API views for authentication (login, logout, LinkedIn OAuth, Google OAuth).
These endpoints return JSON responses for SPA frontend.
"""
import os
import json
import logging
import requests
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from .models import UserProfile
from .linkedin_oauth import (
    get_authorization_url,
    verify_state,
    exchange_code_for_token,
    get_user_info,
    get_or_create_user,
    store_state,
    LINKEDIN_AUTHORIZATION_URL,
    LINKEDIN_SCOPES,
)
from .google_oauth import (
    get_authorization_url as get_google_authorization_url,
    verify_state as verify_google_state,
    exchange_code_for_token as exchange_google_code_for_token,
    get_user_info as get_google_user_info,
    get_or_create_user as get_or_create_google_user,
    store_state as store_google_state,
    GOOGLE_AUTHORIZATION_URL,
    GOOGLE_SCOPES,
)

logger = logging.getLogger(__name__)


def get_json_data(request):
    """Parse JSON data from request body."""
    try:
        if request.content_type == 'application/json':
            return json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        pass
    return {}


@csrf_exempt
@require_http_methods(["POST"])
def api_login(request):
    """REST API endpoint for login. Returns JSON response."""
    try:
        json_data = get_json_data(request)
        username = request.POST.get('username') or json_data.get('username')
        password = request.POST.get('password') or json_data.get('password')
        
        if not username or not password:
            return JsonResponse({
                'success': False,
                'error': 'Username and password are required'
            }, status=400)
        
        # Authenticate user
        user = authenticate(request, username=username, password=password)
        
        if user is None:
            return JsonResponse({
                'success': False,
                'error': 'Invalid username or password'
            }, status=401)
        
        # Log the user in
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        
        # Ensure UserProfile exists and refresh from DB
        profile, created = UserProfile.objects.get_or_create(user=user)
        if created:
            logger.info(f"Created UserProfile for user {user.username}")
        # Refresh profile to ensure it's up to date
        profile.refresh_from_db()
        
        # Check if this is first login (user created recently, within last hour)
        from django.utils import timezone
        from datetime import timedelta
        is_first_login = False
        if user.date_joined:
            time_since_joined = timezone.now() - user.date_joined
            # Consider it first login if account was created within last hour
            is_first_login = time_since_joined < timedelta(hours=1)
        
        # Send welcome email on first login
        if is_first_login:
            try:
                from .welcome_email import send_welcome_email
                send_welcome_email(user, is_new_user=False)
            except Exception as e:
                logger.error(f"Failed to send welcome email on login to user {user.username}: {str(e)}", exc_info=True)
        
        logger.info(f"User {user.username} logged in via API")
        
        # Return user and profile data
        return JsonResponse({
            'success': True,
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
            },
            'profile': {
                'id': profile.id,
                'username': user.username,
                'email': user.email,
            }
        })
        
    except Exception as e:
        logger.error(f"Login API error: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'An error occurred during login'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_logout(request):
    """REST API endpoint for logout. Returns JSON response."""
    try:
        logout(request)
        logger.info("User logged out via API")
        return JsonResponse({
            'success': True,
            'message': 'Logged out successfully'
        })
    except Exception as e:
        logger.error(f"Logout API error: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'An error occurred during logout'
        }, status=500)


@ensure_csrf_cookie
@require_http_methods(["GET"])
def api_csrf_token(request):
    """Get CSRF token for API requests."""
    token = get_token(request)
    return JsonResponse({
        'csrf_token': token
    })


@require_http_methods(["GET"])
def api_linkedin_oauth_url(request):
    """Get LinkedIn OAuth authorization URL."""
    try:
        # Store origin and next URL for redirect after OAuth
        origin = request.headers.get('Origin', '')
        next_url = request.GET.get('next', '/')
        if origin:
            request.session['linkedin_oauth_origin'] = origin
        if next_url:
            request.session['linkedin_oauth_next'] = next_url
        
        # Use API callback endpoint for LinkedIn redirect
        # Temporarily override the redirect URI
        from django.urls import reverse
        from urllib.parse import urlencode
        import secrets
        from .linkedin_oauth import store_state, LINKEDIN_AUTHORIZATION_URL, LINKEDIN_SCOPES
        
        state = secrets.token_urlsafe(32)
        store_state(request, state)
        
        # Build callback URL pointing to API endpoint
        scheme = 'https' if request.is_secure() else 'http'
        host = request.get_host()
        redirect_uri = f"{scheme}://{host}{reverse('api_linkedin_oauth_callback')}"
        
        params = {
            'response_type': 'code',
            'client_id': os.getenv('LINKEDIN_CLIENT_ID', ''),
            'redirect_uri': redirect_uri,
            'state': state,
            'scope': ' '.join(LINKEDIN_SCOPES),
        }
        
        auth_url = f"{LINKEDIN_AUTHORIZATION_URL}?{urlencode(params)}"
        
        return JsonResponse({
            'success': True,
            'auth_url': auth_url,
            'state': state
        })
    except Exception as e:
        logger.error(f"LinkedIn OAuth URL generation error: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'Failed to generate LinkedIn OAuth URL'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_linkedin_oauth_callback(request):
    """Handle LinkedIn OAuth callback via API. Returns JSON response or redirects."""
    try:
        # Get parameters from GET (LinkedIn redirect) or POST (API call)
        code = request.GET.get('code') or request.POST.get('code')
        state = request.GET.get('state') or request.POST.get('state')
        error = request.GET.get('error') or request.POST.get('error')
        error_description = request.GET.get('error_description') or request.POST.get('error_description')
        
        # Check if this is a redirect from LinkedIn (GET request)
        is_redirect = request.method == 'GET'
        
        # Check for OAuth errors
        if error:
            logger.error(f"LinkedIn OAuth error: {error} - {error_description}")
            return JsonResponse({
                'success': False,
                'error': error_description or error
            }, status=400)
        
        # Verify required parameters
        if not code or not state:
            logger.error("Missing code or state parameter in LinkedIn OAuth callback")
            return JsonResponse({
                'success': False,
                'error': 'Missing code or state parameter'
            }, status=400)
        
        # Verify state
        if not verify_state(request, state):
            logger.error(f"Invalid state parameter: {state}")
            return JsonResponse({
                'success': False,
                'error': 'Invalid OAuth state'
            }, status=400)
        
        # Exchange code for access token
        # Use API callback URL for redirect_uri
        from django.urls import reverse
        scheme = 'https' if request.is_secure() else 'http'
        host = request.get_host()
        redirect_uri = f"{scheme}://{host}{reverse('api_linkedin_oauth_callback')}"
        
        # Exchange code for token with custom redirect_uri
        from .linkedin_oauth import LINKEDIN_TOKEN_URL, LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET
        
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
            if not access_token:
                raise ValueError("No access token in response")
        except Exception as e:
            logger.error(f"Failed to exchange code for token: {e}")
            raise ValueError(f"Failed to exchange authorization code: {str(e)}")
        
        # Get user information
        user_data = get_user_info(access_token)
        
        # Get or create user
        user, is_new_user = get_or_create_user(user_data)
        
        # Send welcome email if this is a new user (already sent in get_or_create_user)
        # or if it's first login for existing user
        if not is_new_user:
            # Check if this is first login (user created recently, within last hour)
            from django.utils import timezone
            from datetime import timedelta
            is_first_login = False
            if user.date_joined:
                time_since_joined = timezone.now() - user.date_joined
                # Consider it first login if account was created within last hour
                is_first_login = time_since_joined < timedelta(hours=1)
            
            # Send welcome email on first login
            if is_first_login:
                try:
                    from .welcome_email import send_welcome_email
                    send_welcome_email(user, is_new_user=False)
                except Exception as e:
                    logger.error(f"Failed to send welcome email on LinkedIn login to user {user.username}: {str(e)}", exc_info=True)
        
        # Ensure UserProfile exists and refresh from DB
        profile, created = UserProfile.objects.get_or_create(user=user)
        if created:
            logger.info(f"Created UserProfile for user {user.username} via LinkedIn OAuth")
        profile.refresh_from_db()
        
        # Log the user in
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        
        logger.info(f"User {user.username} logged in via LinkedIn OAuth API")
        
        # If this is a redirect from LinkedIn, redirect to oshaani.pro
        if is_redirect:
            origin = request.session.get('linkedin_oauth_origin', '')
            if origin and 'oshaani.pro' in origin:
                # Redirect back to oshaani.pro with success indicator
                from django.shortcuts import redirect
                from urllib.parse import urlencode
                next_url = request.session.pop('linkedin_oauth_next', '/')
                redirect_url = f"https://oshaani.pro{next_url}?linkedin_success=1&username={user.username}"
                return redirect(redirect_url)
        
        # Return JSON response for API calls
        return JsonResponse({
            'success': True,
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
            },
            'profile': {
                'id': profile.id,
                'username': user.username,
                'email': user.email,
            }
        })
        
    except ValueError as e:
        logger.error(f"LinkedIn OAuth error: {e}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)
    except Exception as e:
        logger.error(f"LinkedIn OAuth error: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'Failed to complete LinkedIn login'
        }, status=500)


@require_http_methods(["GET"])
def api_google_oauth_url(request):
    """Get Google OAuth authorization URL."""
    try:
        # Store origin and next URL for redirect after OAuth
        origin = request.headers.get('Origin', '')
        next_url = request.GET.get('next', '/')
        if origin:
            request.session['google_oauth_origin'] = origin
        if next_url:
            request.session['google_oauth_next'] = next_url
        
        # Use API callback endpoint for Google redirect
        from django.urls import reverse
        from urllib.parse import urlencode
        import secrets
        from .google_oauth import store_state, GOOGLE_AUTHORIZATION_URL, GOOGLE_SCOPES
        
        state = secrets.token_urlsafe(32)
        store_state(request, state)
        
        # Build callback URL pointing to API endpoint
        scheme = 'https' if request.is_secure() else 'http'
        host = request.get_host()
        redirect_uri = f"{scheme}://{host}{reverse('api_google_oauth_callback')}"
        
        params = {
            'response_type': 'code',
            'client_id': os.getenv('GOOGLE_CLIENT_ID', ''),
            'redirect_uri': redirect_uri,
            'state': state,
            'scope': ' '.join(GOOGLE_SCOPES),
            'access_type': 'online',
            'prompt': 'select_account',
        }
        
        auth_url = f"{GOOGLE_AUTHORIZATION_URL}?{urlencode(params)}"
        
        return JsonResponse({
            'success': True,
            'auth_url': auth_url,
            'state': state
        })
    except Exception as e:
        logger.error(f"Google OAuth URL generation error: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'Failed to generate Google OAuth URL'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_google_oauth_callback(request):
    """Handle Google OAuth callback via API. Returns JSON response or redirects."""
    try:
        # Get parameters from GET (Google redirect) or POST (API call)
        code = request.GET.get('code') or request.POST.get('code')
        state = request.GET.get('state') or request.POST.get('state')
        error = request.GET.get('error') or request.POST.get('error')
        error_description = request.GET.get('error_description') or request.POST.get('error_description')
        
        # Check if this is a redirect from Google (GET request)
        is_redirect = request.method == 'GET'
        
        # Check for OAuth errors
        if error:
            logger.error(f"Google OAuth error: {error} - {error_description}")
            return JsonResponse({
                'success': False,
                'error': error_description or error
            }, status=400)
        
        # Verify required parameters
        if not code or not state:
            logger.error("Missing code or state parameter in Google OAuth callback")
            return JsonResponse({
                'success': False,
                'error': 'Missing code or state parameter'
            }, status=400)
        
        # Verify state
        if not verify_google_state(request, state):
            logger.error(f"Invalid state parameter: {state}")
            return JsonResponse({
                'success': False,
                'error': 'Invalid OAuth state'
            }, status=400)
        
        # Exchange code for access token
        # Use API callback URL for redirect_uri
        from django.urls import reverse
        scheme = 'https' if request.is_secure() else 'http'
        host = request.get_host()
        redirect_uri = f"{scheme}://{host}{reverse('api_google_oauth_callback')}"
        
        # Exchange code for token with custom redirect_uri
        from .google_oauth import GOOGLE_TOKEN_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
        
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
            if not access_token:
                raise ValueError("No access token in response")
        except Exception as e:
            logger.error(f"Failed to exchange code for token: {e}")
            raise ValueError(f"Failed to exchange authorization code: {str(e)}")
        
        # Get user information
        user_data = get_google_user_info(access_token)
        
        # Get or create user
        user, is_new_user = get_or_create_google_user(user_data)
        
        # Send welcome email if this is a new user (already sent in get_or_create_user)
        # or if it's first login for existing user
        if not is_new_user:
            # Check if this is first login (user created recently, within last hour)
            from django.utils import timezone
            from datetime import timedelta
            is_first_login = False
            if user.date_joined:
                time_since_joined = timezone.now() - user.date_joined
                # Consider it first login if account was created within last hour
                is_first_login = time_since_joined < timedelta(hours=1)
            
            # Send welcome email on first login
            if is_first_login:
                try:
                    from .welcome_email import send_welcome_email
                    send_welcome_email(user, is_new_user=False)
                except Exception as e:
                    logger.error(f"Failed to send welcome email on Google login to user {user.username}: {str(e)}", exc_info=True)
        
        # Ensure UserProfile exists and refresh from DB
        profile, created = UserProfile.objects.get_or_create(user=user)
        if created:
            logger.info(f"Created UserProfile for user {user.username} via Google OAuth")
        profile.refresh_from_db()
        
        # Log the user in
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        
        logger.info(f"User {user.username} logged in via Google OAuth API")
        
        # If this is a redirect from Google, redirect to oshaani.pro
        if is_redirect:
            origin = request.session.get('google_oauth_origin', '')
            if origin and 'oshaani.pro' in origin:
                # Redirect back to oshaani.pro with success indicator
                from django.shortcuts import redirect
                from urllib.parse import urlencode
                next_url = request.session.pop('google_oauth_next', '/')
                redirect_url = f"https://oshaani.pro{next_url}?google_success=1&username={user.username}"
                return redirect(redirect_url)
        
        # Return JSON response for API calls
        return JsonResponse({
            'success': True,
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
            },
            'profile': {
                'id': profile.id,
                'username': user.username,
                'email': user.email,
            }
        })
        
    except ValueError as e:
        logger.error(f"Google OAuth error: {e}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)
    except Exception as e:
        logger.error(f"Google OAuth error: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'Failed to complete Google login'
        }, status=500)


@require_http_methods(["POST"])
def complete_onboarding_tour(request):
    """Mark onboarding tour as completed for the authenticated user."""
    if not request.user.is_authenticated:
        return JsonResponse({
            'success': False,
            'error': 'Authentication required'
        }, status=401)
    
    try:
        # Get or create user profile
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.onboarding_tour_completed = True
        profile.save(update_fields=['onboarding_tour_completed'])
        
        logger.info(f"User {request.user.username} completed onboarding tour")
        
        return JsonResponse({
            'success': True,
            'message': 'Onboarding tour marked as completed'
        })
    except Exception as e:
        logger.error(f"Error marking tour as completed: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'Failed to mark tour as completed'
        }, status=500)

