"""
Custom adapters for django-allauth to integrate with existing user model.
"""
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.providers.linkedin_oauth2.provider import LinkedInOAuth2Provider
from allauth.socialaccount.providers.linkedin_oauth2.views import LinkedInOAuth2Adapter


class CustomAccountAdapter(DefaultAccountAdapter):
    """Custom account adapter for allauth."""
    
    def is_open_for_signup(self, request):
        """Allow signups."""
        return True
    
    def save_user(self, request, user, form, commit=True):
        """Save user with email as username if username not provided."""
        user = super().save_user(request, user, form, commit=False)
        
        # If username is empty, use email
        if not user.username:
            user.username = user.email
        
        if commit:
            user.save()
        return user


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Custom social account adapter for allauth."""
    
    def is_open_for_signup(self, request, sociallogin):
        """Allow social signups."""
        return True
    
    def pre_social_login(self, request, sociallogin):
        """Handle pre-social login logic."""
        # If user is already logged in, connect the social account
        if request.user.is_authenticated:
            sociallogin.connect(request, request.user)
    
    def get_app(self, request, provider, client_id=None):
        """Override to handle MultipleObjectsReturned error."""
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.shortcuts import get_current_site
        
        site = get_current_site(request)
        
        # Query with site filter
        apps = SocialApp.objects.filter(provider=provider, sites=site)
        
        if client_id:
            apps = apps.filter(client_id=client_id)
        
        app_count = apps.count()
        
        if app_count == 0:
            # Fallback: try without site filter if no app found
            apps = SocialApp.objects.filter(provider=provider)
            if client_id:
                apps = apps.filter(client_id=client_id)
            if apps.count() > 0:
                # Add site to first app found
                app = apps.first()
                app.sites.add(site)
                return app
            raise SocialApp.DoesNotExist(f"No {provider} app found")
        elif app_count > 1:
            # Multiple apps - use the first one and log warning
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Multiple {provider} apps found for site {site.domain}, using first one")
            return apps.first()
        else:
            return apps.first()
    
    def authentication_error(self, request, provider_id, error=None, exception=None, extra_context=None):
        """Handle authentication errors with better user feedback."""
        from django.contrib import messages
        from django.shortcuts import redirect
        from django.urls import reverse
        import traceback
        
        error_message = "LinkedIn authentication failed. Please try again."
        
        # Check for OAuth error codes in query parameters (LinkedIn often returns errors via URL params)
        oauth_error = None
        oauth_error_description = None
        if request and hasattr(request, 'GET'):
            oauth_error = request.GET.get('error')
            oauth_error_description = request.GET.get('error_description')
            if oauth_error and not error:
                error = oauth_error
        
        if error:
            error_str = str(error).lower()
            if 'redirect_uri' in error_str or 'callback' in error_str:
                error_message = (
                    "LinkedIn OAuth configuration error. "
                    "The callback URL may not be registered correctly in your LinkedIn app. "
                    "Please contact support."
                )
            elif 'access_denied' in error_str:
                error_message = "LinkedIn authentication was cancelled."
            elif 'invalid_client' in error_str:
                error_message = "LinkedIn app credentials are invalid. Please contact support."
            elif oauth_error_description:
                error_message = f"LinkedIn authentication error: {oauth_error_description}"
            else:
                error_message = f"LinkedIn authentication error: {error}"
        
        # Log the error for debugging with full context
        import logging
        logger = logging.getLogger(__name__)
        
        # Build comprehensive error log
        error_details = {
            'provider': provider_id,
            'error': str(error) if error else None,
            'oauth_error': oauth_error,
            'oauth_error_description': oauth_error_description,
            'exception_type': type(exception).__name__ if exception else None,
            'exception_message': str(exception) if exception else None,
            'request_path': request.path if request else None,
            'request_method': request.method if request else None,
            'request_host': request.get_host() if request else None,
            'query_params': dict(request.GET) if request else None,
        }
        
        # Add extra context if available
        if extra_context:
            error_details['extra_context'] = extra_context
        
        # Log exception traceback if available
        if exception:
            error_details['traceback'] = traceback.format_exc()
        
        logger.error(
            f"LinkedIn OAuth error - Provider: {provider_id}, "
            f"Error: {error}, OAuth Error: {oauth_error}, "
            f"OAuth Error Description: {oauth_error_description}, "
            f"Exception: {exception}, "
            f"Path: {request.path if request else 'N/A'}, "
            f"Host: {request.get_host() if request else 'N/A'}, "
            f"Query Params: {dict(request.GET) if request and hasattr(request, 'GET') else 'N/A'}, "
            f"Extra context: {extra_context}"
        )
        
        # Log full details as JSON for easier parsing
        import json
        logger.error(f"LinkedIn OAuth error details: {json.dumps(error_details, default=str)}")
        
        messages.error(request, error_message)
        return redirect('account_login')
    
    def populate_user(self, request, sociallogin, data):
        """Populate user data from social account.
        
        Handles both old LinkedIn API format and new OIDC format:
        - Old API: firstName, lastName, emailAddress
        - OIDC: given_name, family_name, email
        """
        user = super().populate_user(request, sociallogin, data)
        
        # Set username from email if not provided
        if not user.username:
            # Try OIDC format first (new API)
            email = sociallogin.account.extra_data.get('email', '')
            # Fallback to old API format
            if not email:
                email = sociallogin.account.extra_data.get('emailAddress', '')
            if email:
                user.username = email
            elif user.email:
                user.username = user.email
        
        # Set first_name from LinkedIn data
        # Try OIDC format first (given_name)
        if not user.first_name:
            first_name = data.get('given_name', '')
            if first_name:
                user.first_name = first_name
            else:
                # Fallback to old API format (firstName)
                first_name_data = data.get('firstName', {})
                if isinstance(first_name_data, dict):
                    user.first_name = first_name_data.get('localized', {}).get('en_US', '')
                elif isinstance(first_name_data, str):
                    user.first_name = first_name_data
        
        # Set last_name from LinkedIn data
        # Try OIDC format first (family_name)
        if not user.last_name:
            last_name = data.get('family_name', '')
            if last_name:
                user.last_name = last_name
            else:
                # Fallback to old API format (lastName)
                last_name_data = data.get('lastName', {})
                if isinstance(last_name_data, dict):
                    user.last_name = last_name_data.get('localized', {}).get('en_US', '')
                elif isinstance(last_name_data, str):
                    user.last_name = last_name_data
        
        return user


class CustomLinkedInOAuth2Adapter(LinkedInOAuth2Adapter):
    """Custom LinkedIn OAuth2 adapter that uses OIDC userinfo endpoint."""
    
    def __init__(self, *args, **kwargs):
        """Initialize custom adapter and log that it's being used."""
        import logging
        logger = logging.getLogger(__name__)
        logger.info("CustomLinkedInOAuth2Adapter.__init__ called - custom adapter is being instantiated")
        super().__init__(*args, **kwargs)
    
    def get_user_info(self, token):
        """Override to use OIDC userinfo endpoint instead of deprecated /v2/me endpoint."""
        import requests
        import logging
        
        logger = logging.getLogger(__name__)
        logger.info("CustomLinkedInOAuth2Adapter.get_user_info called - using OIDC userinfo endpoint")
        
        # Use OIDC userinfo endpoint for new API
        headers = {
            'Authorization': f'Bearer {token.token}',
            'Content-Type': 'application/json',
        }
        
        # Try OIDC userinfo endpoint first
        userinfo_url = 'https://api.linkedin.com/v2/userinfo'
        try:
            logger.info(f"Calling OIDC userinfo endpoint: {userinfo_url}")
            resp = requests.get(userinfo_url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"OIDC userinfo response received: {list(data.keys())}")
            
            # Map OIDC claims to expected format
            # OIDC returns: sub, name, given_name, family_name, email, picture
            # Map to LinkedIn format for compatibility
            mapped_data = {
                'id': data.get('sub', ''),
                'firstName': {
                    'localized': {'en_US': data.get('given_name', '')},
                    'preferredLocale': {'language': 'en', 'country': 'US'}
                },
                'lastName': {
                    'localized': {'en_US': data.get('family_name', '')},
                    'preferredLocale': {'language': 'en', 'country': 'US'}
                },
                'emailAddress': data.get('email', ''),
                'profilePicture': {
                    'displayImage': data.get('picture', '')
                }
            }
            
            logger.info(f"Successfully retrieved user info from OIDC userinfo endpoint")
            return mapped_data
            
        except requests.exceptions.HTTPError as e:
            # If OIDC endpoint fails, log and re-raise
            logger.error(f"Failed to get user info from OIDC endpoint: {e}")
            logger.error(f"Response: {resp.text if 'resp' in locals() else 'No response'}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error getting user info: {e}")
            raise
