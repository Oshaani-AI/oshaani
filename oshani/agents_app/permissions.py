"""Permission classes for agents_app."""
from rest_framework import permissions
from .models import Agent


class IsAgentOwner(permissions.BasePermission):
    """Permission to only allow owners of an agent or users with accepted shared access to access it.
    
    For read operations (GET, HEAD, OPTIONS): allows both owners and users with shared access.
    For write operations (POST, PUT, PATCH, DELETE): only allows owners.
    """
    
    def has_object_permission(self, request, view, obj):
        """Check if user owns the agent or has accepted shared access."""
        # User owns the agent - full access
        if obj.user == request.user:
            return True
        
        # For write operations, only owners can modify
        if request.method not in permissions.SAFE_METHODS:
            return False
        
        # For read operations, check if user has accepted shared access
        from .models import AgentShare
        share = AgentShare.objects.filter(
            agent=obj,
            email=request.user.email,
            is_accepted=True,
            accepted_by=request.user
        ).first()
        
        if share and not share.is_expired():
            return True
        
        return False


class IsAgentOwnerOrReadOnly(permissions.BasePermission):
    """Permission to allow read access to all, but write access only to agent owners."""
    
    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any request
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Write permissions are only allowed to the owner of the agent
        return obj.user == request.user


class HasAgentAPIKey(permissions.BasePermission):
    """Permission to require valid API key for agent interaction."""
    
    def has_permission(self, request, view):
        """Check if request has valid API key."""
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"HasAgentAPIKey.has_permission called")
        logger.info(f"request.auth: {request.auth}")
        logger.info(f"request.auth type: {type(request.auth)}")
        logger.info(f"hasattr(request, 'auth'): {hasattr(request, 'auth')}")
        if hasattr(request, 'auth'):
            logger.info(f"isinstance(request.auth, Agent): {isinstance(request.auth, Agent)}")
            if isinstance(request.auth, Agent):
                logger.info(f"request.auth.status: {request.auth.status}")
                result = request.auth.status == 'published'
                logger.info(f"Permission result: {result}")
                return result
        logger.warning(f"Permission check failed - request.auth is not an Agent instance")
        return False


class IsPublishedAgent(permissions.BasePermission):
    """Permission to ensure agent is published."""
    
    def has_object_permission(self, request, view, obj):
        """Check if agent is published."""
        return obj.status == 'published'


class SessionOrAgentAPIKeyPermission(permissions.BasePermission):
    """Permission that allows either session authentication (for UI) or agent API key (for external clients).
    
    For session auth: user must be authenticated and can access their own published agents
    For API key auth: agent must be authenticated via API key and be published
    """
    
    def has_permission(self, request, view):
        """Check if request has valid authentication."""
        import logging
        logger = logging.getLogger(__name__)
        
        # Check if authenticated via session (for UI)
        # Only check is_authenticated if request.user is a User object (not Agent)
        if request.user and hasattr(request.user, 'is_authenticated') and request.user.is_authenticated:
            logger.debug(f"Session authentication found for user: {request.user.username}")
            # For session auth, we'll check agent ownership in the view
            return True
        
        # Check if authenticated via agent API key (agent is in request.auth)
        if hasattr(request, 'auth') and isinstance(request.auth, Agent):
            logger.debug(f"Agent API key authentication found for agent: {request.auth.id}")
            return request.auth.status == 'published'
        
        logger.warning(f"Permission check failed - no valid authentication found")
        return False

