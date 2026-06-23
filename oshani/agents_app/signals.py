"""Django signals for agents_app to ensure data isolation and cleanup."""
import logging
from django.db.models.signals import post_delete, pre_delete, post_save
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from .models import Agent, TrainingData, UserProfile

logger = logging.getLogger(__name__)


@receiver(pre_delete, sender=Agent)
def cleanup_agent_rag_vectors(sender, instance, **kwargs):
    """
    Clean up RAG vectors when an agent is deleted.
    
    This ensures that training data vectors are completely removed
    when an agent is deleted, maintaining strict data isolation.
    """
    try:
        from .rag_service import get_rag_service
        from django.conf import settings
        
        # Determine embedding provider based on agent's model
        embedding_provider = 'bedrock'
        if instance.model and instance.model.provider == 'ollama':
            embedding_provider = 'ollama'
        
        # Get vector store backend from settings or agent configuration
        vector_store_backend = getattr(settings, 'RAG_VECTOR_STORE_BACKEND', 'qdrant')
        if instance.configuration and 'rag_vector_store_backend' in instance.configuration:
            vector_store_backend = instance.configuration['rag_vector_store_backend']
        
        # Get RAG service and clear vectors for this agent
        rag_service = get_rag_service(
            embedding_provider=embedding_provider,
            vector_store_backend=vector_store_backend
        )
        
        # Clear all vectors for this agent
        rag_service.vector_store.clear_agent_vectors(instance.id)
        logger.info(f"Cleaned up RAG vectors for deleted agent {instance.id} ({instance.name})")
        
    except Exception as e:
        # Log error but don't prevent deletion
        logger.error(f"Error cleaning up RAG vectors for agent {instance.id}: {str(e)}", exc_info=True)


@receiver(post_delete, sender=TrainingData)
def cleanup_training_data_rag_index(sender, instance, **kwargs):
    """
    Clean up RAG index status and invalidate cache when training data is deleted.
    Also sync the agent's training_data_count to keep it accurate.
    
    Note: The actual vectors are cleared when the agent is deleted or
    when re-indexing happens. This just cleans up the index status records.
    """
    try:
        from .models import RAGIndexStatus
        
        # Delete RAG index status records for this training data
        RAGIndexStatus.objects.filter(
            agent=instance.agent,
            training_data=instance
        ).delete()
        
        # Sync training data count to ensure accuracy
        agent = instance.agent
        actual_count = agent.training_data.count()
        if agent.training_data_count != actual_count:
            logger.info(f"Syncing training_data_count for agent {agent.id}: {agent.training_data_count} -> {actual_count}")
            agent.training_data_count = actual_count
            agent.save(update_fields=['training_data_count'])
        
        # Invalidate agent cache since training data changed
        from .cache_utils import invalidate_agent_cache
        invalidate_agent_cache(instance.agent.id)
        
        logger.debug(f"Cleaned up RAG index status and invalidated cache for deleted training data {instance.id}")
        
    except Exception as e:
        logger.error(f"Error cleaning up RAG index status for training data {instance.id}: {str(e)}")


@receiver(post_save, sender=TrainingData)
def invalidate_cache_on_training_data_update(sender, instance, **kwargs):
    """
    Invalidate agent cache when training data is added or updated.
    Also sync the agent's training_data_count to keep it accurate.
    
    This ensures cached responses are invalidated when training data changes,
    so agents always use the latest knowledge base.
    """
    try:
        # Sync training data count to ensure accuracy (only on create, not update)
        if kwargs.get('created', False):
            agent = instance.agent
            actual_count = agent.training_data.count()
            if agent.training_data_count != actual_count:
                logger.info(f"Syncing training_data_count for agent {agent.id}: {agent.training_data_count} -> {actual_count}")
                agent.training_data_count = actual_count
                agent.save(update_fields=['training_data_count'])
        
        from .cache_utils import invalidate_agent_cache
        invalidate_agent_cache(instance.agent.id)
        logger.debug(f"Invalidated cache for agent {instance.agent.id} due to training data update")
    except Exception as e:
        logger.warning(f"Error invalidating cache for training data update: {str(e)}")


@receiver(post_save, sender=Agent)
def invalidate_cache_on_agent_config_change(sender, instance, **kwargs):
    """
    Invalidate agent cache when agent configuration changes.
    
    This ensures cached responses are invalidated when:
    - System prompt changes
    - Model changes
    - Configuration changes
    """
    try:
        # Only invalidate if this is an update (not creation)
        if kwargs.get('created', False):
            return
        
        # Check if configuration or model changed
        if instance.pk:
            try:
                old_instance = Agent.objects.get(pk=instance.pk)
                # Check if model or configuration changed
                if (old_instance.model_id != instance.model_id or 
                    old_instance.configuration != instance.configuration):
                    from .cache_utils import invalidate_agent_cache
                    invalidate_agent_cache(instance.id)
                    logger.debug(f"Invalidated cache for agent {instance.id} due to configuration change")
            except Agent.DoesNotExist:
                pass
    except Exception as e:
        logger.warning(f"Error invalidating cache for agent config change: {str(e)}")


@receiver(user_logged_in)
def ensure_user_profile(sender, request, user, **kwargs):
    """
    Ensure UserProfile exists when a user logs in.
    This prevents users from being logged in without a profile.
    """
    try:
        UserProfile.objects.get_or_create(user=user)
        logger.debug(f"Ensured UserProfile exists for user {user.username}")
    except Exception as e:
        logger.error(f"Error ensuring UserProfile for user {user.username}: {str(e)}", exc_info=True)

