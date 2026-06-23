"""Caching utilities for agent responses using Redis."""
import hashlib
import json
import logging
from typing import Dict, Any, Optional
from django.core.cache import cache
from django.conf import settings

logger = logging.getLogger(__name__)

# Cache settings
CACHE_PREFIX = 'agent_response'
CACHE_TIMEOUT = getattr(settings, 'AGENT_RESPONSE_CACHE_TIMEOUT', 3600)  # 1 hour default
CACHE_ENABLED = getattr(settings, 'AGENT_RESPONSE_CACHE_ENABLED', True)


def get_cache_version(agent_id: int) -> int:
    """
    Get current cache version for an agent.
    
    Args:
        agent_id: Agent ID
    
    Returns:
        Current cache version
    """
    try:
        version_key = f"{CACHE_PREFIX}:version:{agent_id}"
        return cache.get(version_key, 0)
    except Exception:
        return 0


def generate_cache_key(agent_id: int, query: str, system_prompt: Optional[str] = None, 
                       model_id: Optional[str] = None, training_data_hash: Optional[str] = None) -> str:
    """
    Generate a cache key for an agent response.
    
    The cache key includes:
    - Agent ID
    - Query text (normalized)
    - System prompt (if provided)
    - Model ID (if provided)
    - Training data hash (to invalidate when training data changes)
    
    Args:
        agent_id: Agent ID
        query: User query
        system_prompt: System prompt (optional)
        model_id: Model ID (optional)
        training_data_hash: Hash of training data (optional, for invalidation)
    
    Returns:
        Cache key string
    """
    # Normalize query (lowercase, strip whitespace)
    normalized_query = query.lower().strip()
    
    # Get cache version for invalidation
    cache_version = get_cache_version(agent_id)
    
    # Create a hash of the key components
    key_components = [
        str(agent_id),
        str(cache_version),  # Include version for invalidation
        normalized_query,
        system_prompt or '',
        model_id or '',
        training_data_hash or ''
    ]
    
    key_string = '|'.join(key_components)
    key_hash = hashlib.sha256(key_string.encode('utf-8')).hexdigest()[:16]
    
    return f"{CACHE_PREFIX}:{agent_id}:{key_hash}"


def get_cached_response(agent_id: int, query: str, system_prompt: Optional[str] = None,
                       model_id: Optional[str] = None, training_data_hash: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Get cached agent response if available.
    
    Args:
        agent_id: Agent ID
        query: User query
        system_prompt: System prompt (optional)
        model_id: Model ID (optional)
        training_data_hash: Hash of training data (optional)
    
    Returns:
        Cached response dict or None if not found
    """
    if not CACHE_ENABLED:
        return None
    
    try:
        cache_key = generate_cache_key(agent_id, query, system_prompt, model_id, training_data_hash)
        cached_data = cache.get(cache_key)
        
        if cached_data:
            logger.info(f"Cache HIT for agent {agent_id}: {query[:50]}...")
            return cached_data
        else:
            logger.debug(f"Cache MISS for agent {agent_id}: {query[:50]}...")
            return None
            
    except Exception as e:
        logger.warning(f"Error getting cached response: {str(e)}")
        return None


def set_cached_response(agent_id: int, query: str, response_data: Dict[str, Any],
                       system_prompt: Optional[str] = None, model_id: Optional[str] = None,
                       training_data_hash: Optional[str] = None, timeout: Optional[int] = None) -> bool:
    """
    Cache an agent response.
    
    Args:
        agent_id: Agent ID
        query: User query
        response_data: Response data to cache
        system_prompt: System prompt (optional)
        model_id: Model ID (optional)
        training_data_hash: Hash of training data (optional)
        timeout: Cache timeout in seconds (defaults to CACHE_TIMEOUT)
    
    Returns:
        True if cached successfully, False otherwise
    """
    if not CACHE_ENABLED:
        return False
    
    try:
        cache_key = generate_cache_key(agent_id, query, system_prompt, model_id, training_data_hash)
        timeout = timeout or CACHE_TIMEOUT
        
        # Store response data
        cache.set(cache_key, response_data, timeout)
        logger.info(f"Cached response for agent {agent_id}: {query[:50]}... (TTL: {timeout}s)")
        return True
        
    except Exception as e:
        logger.warning(f"Error caching response: {str(e)}")
        return False


def invalidate_agent_cache(agent_id: int) -> int:
    """
    Invalidate all cached responses for an agent.
    
    This is useful when:
    - Training data is updated
    - Agent configuration changes
    - System prompt changes
    
    Uses a version-based approach: increments a version number that's
    included in cache keys, effectively invalidating all cached responses.
    
    Args:
        agent_id: Agent ID
    
    Returns:
        Number of cache keys invalidated (1 if successful)
    """
    if not CACHE_ENABLED:
        return 0
    
    try:
        # Use version-based cache invalidation
        # Increment a version key that can be checked in cache key generation
        version_key = f"{CACHE_PREFIX}:version:{agent_id}"
        current_version = cache.get(version_key, 0)
        new_version = current_version + 1
        cache.set(version_key, new_version, timeout=None)  # Never expire
        
        # Also try to delete keys directly if using django-redis
        try:
            from django_redis import get_redis_connection
            redis_client = get_redis_connection("default")
            pattern = f"*{CACHE_PREFIX}:{agent_id}:*"
            keys = redis_client.keys(pattern)
            if keys:
                redis_client.delete(*keys)
                logger.info(f"Deleted {len(keys)} cache keys for agent {agent_id}")
        except Exception:
            # Fallback to version-based invalidation only
            pass
        
        logger.info(f"Invalidated cache for agent {agent_id} (version: {new_version})")
        return 1
        
    except Exception as e:
        logger.warning(f"Error invalidating cache for agent {agent_id}: {str(e)}")
        return 0


def get_training_data_hash(agent) -> Optional[str]:
    """
    Generate a hash of agent's training data for cache invalidation.
    
    Args:
        agent: Agent instance
    
    Returns:
        Hash string or None
    """
    try:
        # Get training data count and last update time
        training_data_count = agent.training_data_count or 0
        
        # Get last training data update time
        from .models import TrainingData
        last_training = TrainingData.objects.filter(agent=agent).order_by('-uploaded_at').first()
        last_update = last_training.uploaded_at.isoformat() if last_training else ''
        
        # Create hash from training data metadata
        hash_string = f"{agent.id}:{training_data_count}:{last_update}"
        return hashlib.sha256(hash_string.encode('utf-8')).hexdigest()[:16]
        
    except Exception as e:
        logger.warning(f"Error generating training data hash: {str(e)}")
        return None

