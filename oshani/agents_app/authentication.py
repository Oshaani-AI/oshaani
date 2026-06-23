"""Authentication classes for agents_app."""
import logging
from rest_framework import authentication
from rest_framework import exceptions
from .models import Agent, UserProfile, UserAPIKey, AgentAPIKey
from .utils import hash_api_key

logger = logging.getLogger(__name__)


def _find_published_agent_for_api_key(api_key):
    """
    Resolve a plaintext API key to a published agent.

    Returns (agent, agent_api_key_row_or_none) or (None, None).
    """
    if not api_key:
        return None, None
    h = hash_api_key(api_key)
    ak = (
        AgentAPIKey.objects.filter(api_key_hash=h, is_active=True, agent__status='published')
        .select_related('agent')
        .first()
    )
    if ak:
        return ak.agent, ak
    agent = Agent.objects.filter(api_key_hash=h, status='published').first()
    if agent:
        return agent, None
    return None, None


def _find_any_agent_for_api_key(api_key):
    """Find any agent (any status) matching the key, for error messages."""
    if not api_key:
        return None
    h = hash_api_key(api_key)
    ak = AgentAPIKey.objects.filter(api_key_hash=h, is_active=True).select_related('agent').first()
    if ak:
        return ak.agent
    return Agent.objects.filter(api_key_hash=h).first()


class UserAPIKeyAuthentication(authentication.BaseAuthentication):
    """Custom authentication using user-level API keys."""
    
    def authenticate(self, request):
        """Authenticate request using user API key."""
        api_key = self.get_api_key(request)
        
        if not api_key:
            logger.debug("No API key found in request")
            return None
        
        logger.debug(f"Attempting authentication with API key: {api_key[:20]}...")
        
        # Try user-level API keys (new multiple keys model)
        try:
            user_api_keys = UserAPIKey.objects.filter(is_active=True, api_key_hash__isnull=False)
            for user_api_key in user_api_keys:
                if user_api_key.verify_api_key(api_key):
                    # Update last used timestamp
                    user_api_key.update_last_used()
                    logger.info(f"User API key authenticated successfully for user: {user_api_key.user.username} (key: {user_api_key.id})")
                    return (user_api_key.user, None)
        except Exception as e:
            logger.error(f"Error during user API key authentication: {str(e)}", exc_info=True)
        
        # Fallback to legacy UserProfile API key (for backward compatibility)
        try:
            profiles = UserProfile.objects.filter(api_key_hash__isnull=False)
            for profile in profiles:
                if profile.verify_api_key(api_key):
                    # Update last used timestamp
                    profile.update_last_used()
                    logger.info(f"Legacy user API key authenticated successfully for user: {profile.user.username}")
                    return (profile.user, None)
        except Exception as e:
            logger.error(f"Error during legacy user authentication: {str(e)}", exc_info=True)
        
        raise exceptions.AuthenticationFailed('Invalid API key')
    
    def get_api_key(self, request):
        """Extract API key from request headers."""
        # Check Authorization header: "ApiKey <key>" or "Bearer <key>"
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('ApiKey '):
            return auth_header.split(' ', 1)[1]
        elif auth_header.startswith('Bearer '):
            return auth_header.split(' ', 1)[1]
        
        # Check X-API-Key header
        api_key = request.META.get('HTTP_X_API_KEY', '')
        if api_key:
            return api_key
        
        return None


class AgentAPIKeyAuthentication(authentication.BaseAuthentication):
    """Custom authentication using agent API keys ONLY.
    
    Agent keys are tightly bound to specific agents and should only be used
    for agent-specific operations. For MCP and other user-level operations,
    use UserAPIKeyAuthentication instead.
    """
    
    def authenticate(self, request):
        """Authenticate request using agent API key ONLY (no user key fallback)."""
        api_key = self.get_api_key(request)
        
        if not api_key:
            logger.debug("No API key found in request")
            return None
        
        logger.debug(f"Attempting agent authentication with API key: {api_key[:20]}...")
        
        try:
            agent, ak_row = _find_published_agent_for_api_key(api_key)
            if agent:
                if ak_row:
                    ak_row.update_last_used()
                logger.info(f"Agent API key authenticated successfully for agent: {agent.id} ({agent.name})")
                return (None, agent)

            unpublished = _find_any_agent_for_api_key(api_key)
            if unpublished and unpublished.status != 'published':
                logger.warning(
                    f"API key matches agent {unpublished.id} but agent status is '{unpublished.status}', not 'published'"
                )
                raise exceptions.AuthenticationFailed(
                    f'Agent API key is valid but agent "{unpublished.name}" (ID: {unpublished.id}) '
                    f'is not published (current status: {unpublished.status}). '
                    f'Only published agents can be accessed via API key. Please publish the agent first.'
                )

            logger.warning("API key did not match any published agent")
        except exceptions.AuthenticationFailed:
            raise
        except Exception as e:
            logger.error(f"Error during agent authentication: {str(e)}", exc_info=True)
        
        raise exceptions.AuthenticationFailed(
            'Invalid agent API key. The API key may have been regenerated, the agent may not be published, '
            'or the key may be incorrect. Please verify the API key and ensure the agent is published.'
        )
    
    def get_api_key(self, request):
        """Extract API key from request headers."""
        # Check Authorization header: "ApiKey <key>"
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        logger.debug(f"Authorization header: {repr(auth_header[:50])}...")
        if auth_header.startswith('ApiKey '):
            api_key = auth_header.split(' ', 1)[1]
            logger.debug(f"Extracted API key from Authorization header (length: {len(api_key)})")
            return api_key.strip()  # Strip whitespace in case there's any
        
        # Check X-API-Key header
        api_key = request.META.get('HTTP_X_API_KEY', '')
        if api_key:
            logger.debug(f"Extracted API key from X-API-Key header (length: {len(api_key)})")
            return api_key.strip()  # Strip whitespace in case there's any
        
        logger.debug("No API key found in request headers")
        return None


class SessionOrAgentAPIKeyAuthentication(authentication.BaseAuthentication):
    """Combined authentication supporting both session (for UI) and agent API key (for external clients).
    
    This allows endpoints to work with:
    1. Session authentication (from UI) - user must be authenticated via Django session
    2. Agent API key authentication (from external clients) - agent is authenticated via API key
    """
    
    def authenticate(self, request):
        """Try session authentication first, then API key authentication."""
        # First, try session authentication (for UI)
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            logger.debug(f"Session authentication found for user: {user.username}")
            # Return None to let DRF's SessionAuthentication handle it
            return None
        
        # If no session, try agent API key authentication
        api_key = self.get_api_key(request)
        
        if not api_key:
            logger.debug("No API key found in request")
            return None
        
        logger.debug(f"Attempting agent authentication with API key: {api_key[:20]}...")
        
        try:
            agent, ak_row = _find_published_agent_for_api_key(api_key)
            if agent:
                if ak_row:
                    ak_row.update_last_used()
                logger.info(f"Agent API key authenticated successfully for agent: {agent.id} ({agent.name})")
                return (None, agent)
            
            logger.warning("API key did not match any published agent")
        except Exception as e:
            logger.error(f"Error during agent authentication: {str(e)}", exc_info=True)
        
        # Don't raise here - let it return None so other authenticators can try
        return None
    
    def get_api_key(self, request):
        """Extract API key from request headers."""
        # Check Authorization header: "ApiKey <key>"
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        logger.debug(f"Authorization header: {repr(auth_header[:50])}...")
        if auth_header.startswith('ApiKey '):
            api_key = auth_header.split(' ', 1)[1]
            logger.debug(f"Extracted API key from Authorization header (length: {len(api_key)})")
            return api_key.strip()  # Strip whitespace in case there's any
        
        # Check X-API-Key header
        api_key = request.META.get('HTTP_X_API_KEY', '')
        if api_key:
            logger.debug(f"Extracted API key from X-API-Key header (length: {len(api_key)})")
            return api_key.strip()  # Strip whitespace in case there's any
        
        logger.debug("No API key found in request headers")
        return None
