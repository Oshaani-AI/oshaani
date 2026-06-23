"""
Google OAuth2 views.
"""
import logging
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth import login
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .google_oauth import (
    get_authorization_url,
    verify_state,
    exchange_code_for_token,
    get_user_info,
    get_or_create_user,
)

logger = logging.getLogger(__name__)


@require_http_methods(["GET"])
def google_oauth_login(request):
    """Initiate Google OAuth login flow."""
    try:
        # Store next URL and origin in session if provided
        next_url = request.GET.get('next')
        origin = request.headers.get('Origin', '')
        if next_url:
            request.session['google_oauth_next'] = next_url
        if origin:
            request.session['google_oauth_origin'] = origin
        
        auth_url, state = get_authorization_url(request)
        logger.info(f"Redirecting to Google OAuth with state: {state[:8]}...")
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"Error initiating Google OAuth: {e}", exc_info=True)
        messages.error(request, "Failed to initiate Google login. Please try again.")
        return redirect('login')


@require_http_methods(["GET"])
def google_oauth_callback(request):
    """Handle Google OAuth callback."""
    code = request.GET.get('code')
    state = request.GET.get('state')
    error = request.GET.get('error')
    error_description = request.GET.get('error_description')
    
    # Check for OAuth errors
    if error:
        logger.error(f"Google OAuth error: {error} - {error_description}")
        if error == 'access_denied':
            messages.info(request, "Google authentication was cancelled.")
        else:
            messages.error(request, f"Google authentication failed: {error_description or error}")
        return redirect('login')
    
    # Verify required parameters
    if not code or not state:
        logger.error("Missing code or state parameter in Google OAuth callback")
        messages.error(request, "Invalid OAuth callback. Please try again.")
        return redirect('login')
    
    # Verify state
    if not verify_state(request, state):
        logger.error(f"Invalid state parameter: {state}")
        messages.error(request, "Invalid OAuth state. Please try again.")
        return redirect('login')
    
    try:
        # Exchange code for access token
        access_token = exchange_code_for_token(request, code)
        
        # Get user information
        user_data = get_user_info(access_token)
        
        # Get or create user
        user, is_new_user = get_or_create_user(user_data)
        
        # Log the user in
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        
        # Record terms acceptance for new users
        if is_new_user:
            try:
                from .models import UserProfile
                from django.utils import timezone
                profile, _ = UserProfile.objects.get_or_create(user=user)
                profile.terms_accepted = True
                profile.terms_accepted_at = timezone.now()
                profile.privacy_accepted = True
                profile.privacy_accepted_at = timezone.now()
                profile.save(update_fields=['terms_accepted', 'terms_accepted_at', 'privacy_accepted', 'privacy_accepted_at'])
                logger.info(f"Recorded terms acceptance for new user {user.username}")
            except Exception as e:
                logger.error(f"Failed to record terms acceptance: {e}")
        
        # Send welcome email if this is first login for existing user (new users already got email)
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
        
        logger.info(f"User {user.username} logged in via Google OAuth")
        
        # Check if this is from oshaani.pro SPA
        origin = request.session.pop('google_oauth_origin', None)
        is_spa_request = origin and 'oshaani.pro' in origin
        
        if is_spa_request:
            # Redirect back to oshaani.pro with success parameters
            next_url = request.session.pop('google_oauth_next', None) or '/'
            # Build redirect URL with success indicator
            redirect_url = f"https://oshaani.pro{next_url}?google_success=1&username={user.username}"
            return redirect(redirect_url)
        
        # Traditional redirect for form-based auth
        messages.success(request, f"Welcome, {user.get_full_name() or user.username}!")
        next_url = request.session.pop('google_oauth_next', None) or request.GET.get('next') or reverse('agents_list')
        return redirect(next_url)
        
    except ValueError as e:
        logger.error(f"Google OAuth error: {e}")
        messages.error(request, str(e))
        return redirect('login')
    except Exception as e:
        logger.error(f"Google OAuth error: {e}", exc_info=True)
        messages.error(request, "Failed to complete Google login. Please try again.")
        return redirect('login')






