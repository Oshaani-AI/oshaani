"""Celery tasks for agents_app."""
from celery import shared_task
from django.utils import timezone
from django.db import transaction
from .models import AIModel, Agent, Conversation, AgentRequest, AgentShare
from .aws_integration import BedrockClient
from .ollama_integration import OllamaClient, is_ollama_available
from .model_use_cases import determine_model_use_cases
from .agent_loop import AgentLoop
from botocore.exceptions import ClientError
import logging
import uuid

logger = logging.getLogger(__name__)


def sync_bedrock_models():
    """Sync Bedrock models to database (can be called directly or via Celery).
    
    Only syncs models that support ON_DEMAND inference (no provisioning required).
    Models requiring inference provisioning are excluded.
    
    Will use IAM role if running on EC2/ECS/Lambda and no explicit credentials provided.
    """
    try:
        # Try to create Bedrock client - will use IAM role if available
        # Use fallback to avoid crashing Celery tasks
        client = BedrockClient(use_ollama_fallback=False)
        
        # Check if we fell back to Ollama
        if client.use_ollama:
            logger.warning("Bedrock not available, using Ollama fallback. Skipping Bedrock model sync.")
            logger.info("To fix Bedrock credentials, run: python manage.py check_aws_credentials")
            return
        
        if not hasattr(client, 'bedrock_agent_client') or not client.bedrock_agent_client:
            logger.warning("Bedrock client not available, skipping Bedrock model sync")
            logger.info("To fix Bedrock credentials, run: python manage.py check_aws_credentials")
            return
        
        # List foundation models from Bedrock
        try:
            response = client.bedrock_agent_client.list_foundation_models()
            models = response.get('modelSummaries', [])
            
            # Build a map of base model id -> cross-region inference profile id from
            # the system-defined profiles AWS actually exposes for this account/region.
            # Profile ids are the model id prefixed with a geo (global./us./eu./apac./
            # us-gov.); newer models (e.g. Opus 4.x) use the GLOBAL profile, so we
            # cannot reliably guess the prefix and must read it from AWS.
            inference_profile_map = {}
            try:
                geo_prefixes = ('global.', 'us-gov.', 'us.', 'eu.', 'apac.')
                profile_resp = client.bedrock_agent_client.list_inference_profiles(maxResults=1000)
                for profile in profile_resp.get('inferenceProfileSummaries', []):
                    if str(profile.get('status', '')).upper() != 'ACTIVE':
                        continue
                    profile_id = profile.get('inferenceProfileId', '')
                    base_id = profile_id
                    for prefix in geo_prefixes:
                        if profile_id.startswith(prefix):
                            base_id = profile_id[len(prefix):]
                            break
                    if base_id and base_id != profile_id:
                        # Prefer a regional profile over global only if none set yet.
                        inference_profile_map.setdefault(base_id, profile_id)
                logger.info(f"Discovered {len(inference_profile_map)} cross-region inference profiles")
            except Exception as profile_err:
                logger.warning(f"Could not list inference profiles: {profile_err}. Profile-only models may not be invocable.")
            
            # Filter out deprecated/legacy models and models requiring inference provisioning
            active_models = []
            deprecated_count = 0
            inference_required_count = 0
            
            for model_summary in models:
                model_id = model_summary.get('modelId')
                
                # Check lifecycle status - filter out deprecated/legacy models
                # Bedrock provides modelLifecycle field with values: ACTIVE, LEGACY, END_OF_LIFE
                # Handle both string and dict formats
                model_lifecycle_raw = model_summary.get('modelLifecycle', '')
                lifecycle_status_raw = model_summary.get('lifecycleStatus', '')
                
                # Extract string value if it's a dict, otherwise use as string
                if isinstance(model_lifecycle_raw, dict):
                    model_lifecycle = str(model_lifecycle_raw.get('status', '')).upper()
                else:
                    model_lifecycle = str(model_lifecycle_raw).upper()
                
                if isinstance(lifecycle_status_raw, dict):
                    lifecycle_status = str(lifecycle_status_raw.get('status', '')).upper()
                else:
                    lifecycle_status = str(lifecycle_status_raw).upper()
                
                # Use modelLifecycle if available, otherwise fall back to lifecycleStatus
                lifecycle = model_lifecycle if model_lifecycle else lifecycle_status
                
                # Skip models that are deprecated, legacy, or end-of-life
                if lifecycle in ['LEGACY', 'END_OF_LIFE', 'DEPRECATED', 'EOL']:
                    deprecated_count += 1
                    logger.debug(f"Skipping deprecated/legacy model: {model_id} (lifecycle: {lifecycle})")
                    continue
                
                # Check for deprecation warnings in model description or other fields
                model_description = model_summary.get('modelDescription', '').upper()
                if any(keyword in model_description for keyword in ['DEPRECAT', 'LEGACY', 'DISCONTINUED', 'SUNSET', 'END OF LIFE']):
                    deprecated_count += 1
                    logger.debug(f"Skipping model with deprecation warning in description: {model_id}")
                    continue
                
                # Check inference types - include ON_DEMAND, PROVISIONED, and
                # INFERENCE_PROFILE (cross-region) models.
                inference_types = model_summary.get('inferenceTypesSupported', [])
                if not inference_types:
                    # If inference types not specified, assume it might require provisioning - skip it
                    logger.debug(f"Skipping model without inference types: {model_id}")
                    continue
                
                # Include models reachable via ON_DEMAND, PROVISIONED, or a
                # cross-region INFERENCE_PROFILE. Newer Bedrock models (e.g.
                # Claude 3.5+/Opus 4.x) are INFERENCE_PROFILE-only.
                supported_types = {'ON_DEMAND', 'PROVISIONED', 'INFERENCE_PROFILE'}
                if not supported_types.intersection(inference_types):
                    inference_required_count += 1
                    logger.debug(f"Skipping model with unsupported inference types: {model_id} (inference types: {inference_types})")
                    continue
                
                # Track whether this model can only be invoked through an
                # inference profile (no direct ON_DEMAND access).
                requires_inference_profile = 'ON_DEMAND' not in inference_types and (
                    'PROVISIONED' in inference_types or 'INFERENCE_PROFILE' in inference_types
                )
                
                # Include active models (both ON_DEMAND and PROVISIONED)
                model_summary['_requires_inference_profile'] = requires_inference_profile
                active_models.append(model_summary)
            
            logger.info(
                f"Filtered {deprecated_count} deprecated/legacy models and {inference_required_count} models requiring inference provisioning. "
                f"Syncing {len(active_models)} active on-demand models"
            )
            
            with transaction.atomic():
                compatible_count = 0
                incompatible_count = 0
                
                for model_summary in active_models:
                    model_id = model_summary.get('modelId')
                    model_name = model_summary.get('modelName', model_id)
                    
                    # Store lifecycle status in metadata for reference
                    # Handle both string and dict formats
                    model_lifecycle_raw = model_summary.get('modelLifecycle', 'ACTIVE')
                    lifecycle_status_raw = model_summary.get('lifecycleStatus', 'ACTIVE')
                    
                    if isinstance(model_lifecycle_raw, dict):
                        model_lifecycle = model_lifecycle_raw.get('status', 'ACTIVE')
                    else:
                        model_lifecycle = str(model_lifecycle_raw)
                    
                    if isinstance(lifecycle_status_raw, dict):
                        lifecycle_status = lifecycle_status_raw.get('status', 'ACTIVE')
                    else:
                        lifecycle_status = str(lifecycle_status_raw)
                    
                    # Check model compatibility with agent API
                    is_compatible = True  # Default to compatible for backward compatibility
                    compatibility_error = None
                    
                    try:
                        logger.info(f"Checking compatibility for model: {model_id}")
                        compatibility_result = client.check_model_compatibility(model_id)
                        is_compatible = compatibility_result.get('compatible', False)
                        compatibility_error = compatibility_result.get('error')
                        
                        if is_compatible:
                            compatible_count += 1
                            logger.info(f"Model {model_id} is compatible with agent API")
                        else:
                            incompatible_count += 1
                            logger.warning(f"Model {model_id} is NOT compatible: {compatibility_error}")
                    except Exception as compat_error:
                        # If compatibility check fails, log but don't fail the sync
                        # Default to compatible to avoid breaking existing functionality
                        logger.warning(f"Compatibility check failed for {model_id}: {str(compat_error)}. Assuming compatible.")
                        is_compatible = True  # Assume compatible if check fails
                        compatibility_error = f"Compatibility check error: {str(compat_error)}"
                    
                    # Check if model requires inference profile
                    requires_inference_profile = model_summary.get('_requires_inference_profile', False)
                    inference_types = model_summary.get('inferenceTypesSupported', [])
                    
                    # Resolve the actual cross-region inference profile id (if any)
                    # so invocation can use it directly instead of guessing a prefix.
                    inference_profile_id = inference_profile_map.get(model_id)
                    if requires_inference_profile and not inference_profile_id:
                        logger.warning(
                            f"Model {model_id} requires an inference profile but none was found in this region"
                        )
                    
                    # Determine best use cases for this model
                    use_cases = determine_model_use_cases(
                        model_id=model_id,
                        model_name=model_name,
                        description=model_summary.get('modelDescription', ''),
                        provider_name=model_summary.get('providerName', ''),
                        input_modalities=model_summary.get('inputModalities', []),
                        output_modalities=model_summary.get('outputModalities', []),
                    )
                    
                    # Create or update model in database
                    ai_model, created = AIModel.objects.update_or_create(
                        model_id=model_id,
                        provider='bedrock',
                        defaults={
                            'model_name': model_name,
                            'description': model_summary.get('modelDescription', ''),
                            'input_modalities': model_summary.get('inputModalities', []),
                            'output_modalities': model_summary.get('outputModalities', []),
                            'use_cases': use_cases,
                            'is_available': True,
                            'metadata': {
                                'provider_name': model_summary.get('providerName', ''),
                                'model_arn': model_summary.get('modelArn', ''),
                                'inference_types_supported': inference_types,
                                'requires_inference_profile': requires_inference_profile,
                                'inference_profile_id': inference_profile_id,
                                'model_lifecycle': model_lifecycle,
                                'lifecycle_status': lifecycle_status,
                                'is_compatible': is_compatible,
                                'compatibility_error': compatibility_error,
                                'compatibility_checked_at': timezone.now().isoformat(),
                            },
                            'last_checked': timezone.now(),
                        }
                    )
                    
                    if created:
                        logger.info(f"Created Bedrock model: {model_id} (compatible: {is_compatible})")
                    else:
                        logger.info(f"Updated Bedrock model: {model_id} (compatible: {is_compatible})")
                
                logger.info(
                    f"Compatibility check complete: {compatible_count} compatible, {incompatible_count} incompatible"
                )
                
                # Mark models as unavailable if they're no longer in the active list
                # Also mark previously synced deprecated models as unavailable
                existing_model_ids = {m.get('modelId') for m in active_models}
                AIModel.objects.filter(provider='bedrock').exclude(
                    model_id__in=existing_model_ids
                ).update(is_available=False)
            
            logger.info(
                f"Synced {len(active_models)} active on-demand Bedrock models "
                f"(excluded {deprecated_count} deprecated/legacy models and {inference_required_count} models requiring inference provisioning)"
            )
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"Error listing Bedrock models: {error_code} - {error_message}")
            
            if error_code == 'UnrecognizedClientException':
                logger.error("AWS Bedrock credentials are invalid or expired")
                logger.info("Run diagnostic: python manage.py check_aws_credentials")
                logger.info("Or see: AWS_BEDROCK_CREDENTIALS_TROUBLESHOOTING.md")
            
            # Mark all Bedrock models as unavailable if we can't connect
            AIModel.objects.filter(provider='bedrock').update(is_available=False)
        except Exception as e:
            logger.error(f"Error listing Bedrock models: {str(e)}")
            # Mark all Bedrock models as unavailable if we can't connect
            AIModel.objects.filter(provider='bedrock').update(is_available=False)
            
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', '')
        error_message = e.response.get('Error', {}).get('Message', str(e))
        logger.error(f"Error initializing Bedrock client: {error_code} - {error_message}")
        
        if error_code == 'UnrecognizedClientException':
            logger.error("AWS Bedrock credentials are invalid or expired")
            logger.info("Run diagnostic: python manage.py check_aws_credentials")
            logger.info("Or see: AWS_BEDROCK_CREDENTIALS_TROUBLESHOOTING.md")
    except Exception as e:
        logger.error(f"Error initializing Bedrock client: {str(e)}")


def sync_ollama_models():
    """Sync Ollama models to database (can be called directly or via Celery). Skips when Ollama is not configured or not reachable."""
    try:
        if not is_ollama_available():
            logger.info("Ollama is not configured or not reachable; skipping Ollama model sync and marking Ollama models unavailable.")
            AIModel.objects.filter(provider='ollama').update(is_available=False)
            return
        client = OllamaClient()
        
        if not client._check_connection():
            logger.warning("Ollama server not available, marking Ollama models as unavailable")
            AIModel.objects.filter(provider='ollama').update(is_available=False)
            return
        
        # List available models from Ollama
        models = client.list_models()
        
        with transaction.atomic():
            for model_name in models:
                # Determine best use cases for this Ollama model
                use_cases = determine_model_use_cases(
                    model_id=model_name,
                    model_name=model_name,
                    description=f'Ollama model: {model_name}',
                    provider_name='Ollama',
                    input_modalities=['text'],
                    output_modalities=['text'],
                )
                
                # Create or update model in database
                # Ollama models are assumed compatible (they work with our integration)
                ai_model, created = AIModel.objects.update_or_create(
                    model_id=model_name,
                    provider='ollama',
                    defaults={
                        'model_name': model_name,
                        'description': f'Ollama model: {model_name}',
                        'input_modalities': ['text'],
                        'output_modalities': ['text'],
                        'use_cases': use_cases,
                        'is_available': True,
                        'metadata': {
                            'ollama_base_url': client.base_url,
                            'is_compatible': True,  # Ollama models are compatible
                            'compatibility_checked_at': timezone.now().isoformat(),
                        },
                        'last_checked': timezone.now(),
                    }
                )
                
                if created:
                    logger.info(f"Created Ollama model: {model_name}")
                else:
                    logger.info(f"Updated Ollama model: {model_name}")
            
            # Mark models as unavailable if they're no longer in the list
            AIModel.objects.filter(provider='ollama').exclude(
                model_id__in=models
            ).update(is_available=False)
        
        logger.info(f"Synced {len(models)} Ollama models")
        
    except Exception as e:
        logger.error(f"Error syncing Ollama models: {str(e)}")
        # Mark all Ollama models as unavailable if we can't connect
        AIModel.objects.filter(provider='ollama').update(is_available=False)


@shared_task
def sync_available_models():
    """Sync available models from Bedrock and Ollama to database."""
    logger.info("Starting model sync task...")
    
    # Sync Bedrock models
    try:
        sync_bedrock_models()
    except Exception as e:
        logger.error(f"Error syncing Bedrock models: {str(e)}")
    
    # Sync Ollama models
    try:
        sync_ollama_models()
    except Exception as e:
        logger.error(f"Error syncing Ollama models: {str(e)}")
    
    logger.info("Model sync task completed")


@shared_task
def index_training_data_for_rag(agent_id, training_data_id=None):
    """Index training data for RAG (can be called for specific training data or all).
    
    This is a Celery task that runs asynchronously. It processes training data,
    chunks it, generates embeddings, and stores them in the vector database.
    """
    from .models import Agent, TrainingData, RAGIndexStatus, Notification
    from .rag_service import get_rag_service
    from django.conf import settings
    import time
    
    start_time = time.time()
    
    try:
        logger.info(f"[RAG Training] Starting RAG indexing for agent {agent_id}, training_data_id={training_data_id}")
        
        agent = Agent.objects.get(id=agent_id)
        logger.info(f"[RAG Training] Agent found: {agent.name} (ID: {agent.id})")
        
        # Prefer Ollama for embeddings when available; otherwise use Bedrock
        embedding_provider = 'ollama' if is_ollama_available() else 'bedrock'
        logger.info(f"[RAG Training] Using embedding provider: {embedding_provider}")
        
        # Get vector store backend from settings or agent configuration
        vector_store_backend = getattr(settings, 'RAG_VECTOR_STORE_BACKEND', 'qdrant')
        if agent.configuration and 'rag_vector_store_backend' in agent.configuration:
            vector_store_backend = agent.configuration['rag_vector_store_backend']
        logger.info(f"[RAG Training] Using vector store backend: {vector_store_backend}")
        
        rag_service = get_rag_service(
            embedding_provider=embedding_provider,
            vector_store_backend=vector_store_backend
        )
        logger.info(f"[RAG Training] RAG service initialized successfully")
        
        # Get training data to index
        if training_data_id:
            training_data_objs = TrainingData.objects.filter(id=training_data_id, agent=agent)
            logger.info(f"[RAG Training] Fetching specific training data item: {training_data_id}")
        else:
            training_data_objs = agent.training_data.all()
            logger.info(f"[RAG Training] Fetching all training data for agent {agent_id}")
        
        training_data_count = training_data_objs.count()
        logger.info(f"[RAG Training] Found {training_data_count} training data item(s) to process")
        
        # Prepare training data list
        training_data_list = []
        processed_count = 0
        file_extraction_count = 0
        for td in training_data_objs:
            # Extract actual content from training data (similar to agent_loop)
            content = td.content
            
            if td.data_type == 'file' and td.file_path:
                # Try to extract text content from file using AgentLoop's extraction method
                # This properly handles PDFs, Word docs, and other file types
                try:
                    from .agent_loop import AgentLoop
                    import os
                    from django.core.files.storage import default_storage
                    from django.conf import settings
                    
                    # Get file path
                    file_path = td.file_path.path if hasattr(td.file_path, 'path') else None
                    if not file_path and hasattr(td.file_path, 'name'):
                        # Try to get file from storage
                        media_root = getattr(settings, 'MEDIA_ROOT', '')
                        if media_root:
                            file_path = os.path.join(media_root, td.file_path.name)
                    
                    if file_path and os.path.exists(file_path):
                        # Use AgentLoop's _read_file_content method which handles PDFs, Word docs, etc.
                        # Create a temporary agent loop instance to use its extraction method
                        logger.debug(f"[RAG Training] Extracting content from file: {file_path} (training_data_id: {td.id})")
                        temp_agent = agent  # Use the agent we already have
                        temp_conversation = None  # We don't need a conversation for file reading
                        agent_loop = AgentLoop(temp_agent, temp_conversation)
                        file_content = agent_loop._read_file_content(file_path)
                        content = {'text': file_content}
                        file_extraction_count += 1
                        logger.debug(f"[RAG Training] Successfully extracted {len(file_content)} characters from file {td.id}")
                    elif hasattr(td.file_path, 'name'):
                        # File is in storage, try to read it
                        try:
                            # Try to get the file path from storage
                            if default_storage.exists(td.file_path.name):
                                # Download to temp location and read
                                import tempfile
                                with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                                    tmp_path = tmp_file.name
                                    with default_storage.open(td.file_path.name, 'rb') as storage_file:
                                        tmp_file.write(storage_file.read())
                                
                                # Use AgentLoop to read the file (handles PDFs, etc.)
                                logger.debug(f"[RAG Training] Extracting content from storage file: {td.file_path.name} (training_data_id: {td.id})")
                                temp_agent = agent
                                temp_conversation = None
                                agent_loop = AgentLoop(temp_agent, temp_conversation)
                                file_content = agent_loop._read_file_content(tmp_path)
                                content = {'text': file_content}
                                file_extraction_count += 1
                                logger.debug(f"[RAG Training] Successfully extracted {len(file_content)} characters from storage file {td.id}")
                                
                                # Clean up temp file
                                try:
                                    os.unlink(tmp_path)
                                except Exception:
                                    pass
                            else:
                                logger.warning(f"File not found in storage: {td.file_path.name}")
                        except Exception as e:
                            logger.warning(f"Could not read file from storage {td.file_path.name if td.file_path else 'unknown'}: {str(e)}")
                            # Fall back to stored content
                            content = td.content
                    else:
                        logger.warning(f"Could not determine file path for training data {td.id}")
                        content = td.content
                except Exception as e:
                    logger.warning(f"Could not extract content from file {td.file_path.name if td.file_path else 'unknown'}: {str(e)}", exc_info=True)
                    # Fall back to stored content
                    content = td.content
            
            training_data_list.append({
                'content': content,
                'data_type': td.data_type,
            })
            processed_count += 1
        
        logger.info(f"[RAG Training] Prepared {processed_count} training data item(s) for indexing (file extractions: {file_extraction_count})")
        
        if not training_data_list:
            logger.warning(f"[RAG Training] No training data to index for agent {agent_id}")
            return
        
        # Calculate total content size for logging
        total_content_size = sum(
            len(str(item.get('content', {}).get('text', ''))) 
            if isinstance(item.get('content'), dict) 
            else len(str(item.get('content', '')))
            for item in training_data_list
        )
        logger.info(f"[RAG Training] Total content size: {total_content_size:,} characters across {len(training_data_list)} items")
        
        # Index the training data
        indexing_start_time = time.time()
        logger.info(f"[RAG Training] Starting RAG indexing process (chunking, embedding, vector storage)")
        result = rag_service.index_training_data(agent_id, training_data_list)
        indexing_duration = time.time() - indexing_start_time
        logger.info(f"[RAG Training] RAG indexing completed in {indexing_duration:.2f} seconds")
        
        if result.get('success'):
            # Update or create RAG index status
            training_data_obj = None
            if training_data_id:
                try:
                    training_data_obj = TrainingData.objects.get(id=training_data_id, agent=agent)
                except TrainingData.DoesNotExist:
                    logger.warning(f"Training data {training_data_id} not found for agent {agent_id}, creating index status without specific training data reference")
            
            RAGIndexStatus.objects.update_or_create(
                agent=agent,
                training_data=training_data_obj,
                defaults={
                    'chunks_count': result.get('chunks_count', 0),
                    'embedding_provider': embedding_provider,
                    'is_active': True
                }
            )
            chunks_count = result.get('chunks_count', 0)
            total_chunks = result.get('total_chunks', 0)
            failed_embeddings = result.get('failed_embeddings', 0)
            logger.info(f"[RAG Training] Successfully indexed {chunks_count} chunks for agent {agent_id} (total chunks: {total_chunks}, failed embeddings: {failed_embeddings})")
            
            # Send notification to user
            try:
                from .models import Notification
                
                # chunks_count, total_chunks, failed_embeddings already retrieved above
                
                message = f"Successfully indexed {chunks_count} chunks for agent '{agent.name}'"
                if total_chunks > chunks_count:
                    message += f" ({failed_embeddings} chunks failed to generate embeddings)"
                
                Notification.objects.create(
                    user=agent.user,
                    agent=agent,
                    notification_type='rag_indexed',
                    title='RAG Indexing Complete',
                    message=message,
                    data={
                        'chunks_count': chunks_count,
                        'total_chunks': total_chunks,
                        'failed_embeddings': failed_embeddings,
                        'embedding_provider': embedding_provider,
                        'vector_store_backend': vector_store_backend,
                        'training_data_id': training_data_id
                    }
                )
                
                # Optionally send email notification with SMTP/SES fallback
                try:
                    from .email_utils import send_email_with_fallback
                    from django.conf import settings
                    
                    if hasattr(settings, 'SEND_RAG_NOTIFICATIONS') and settings.SEND_RAG_NOTIFICATIONS:
                        if agent.user.email:
                            send_email_with_fallback(
                                subject=f'RAG Indexing Complete - {agent.name}',
                                message=f"""
Hello {agent.user.username},

Your training data for agent '{agent.name}' has been successfully indexed for RAG.

Details:
- Chunks indexed: {chunks_count}
- Total chunks processed: {total_chunks}
- Embedding provider: {embedding_provider}
- Vector store: {vector_store_backend}

Your agent is now ready to use RAG for improved responses.

View your agent: {site_url}/dashboard/agents/{agent_id}/
                                """.format(
                                    site_url=getattr(settings, 'SITE_URL', 'https://oshaani.com'),
                                    agent_id=agent.id
                                ).strip(),
                                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'support@oshaani.com'),
                                recipient_list=[agent.user.email],
                                fail_silently=True
                            )
                except Exception as email_error:
                    logger.debug(f"Email notification not sent: {str(email_error)}")
                
            except Exception as notify_error:
                logger.warning(f"Failed to send notification: {str(notify_error)}")
        else:
            logger.error(f"Failed to index training data for agent {agent_id}: {result.get('message')}")
            
            # Send failure notification
            try:
                from .models import Notification
                
                Notification.objects.create(
                    user=agent.user,
                    agent=agent,
                    notification_type='rag_failed',
                    title='RAG Indexing Failed',
                    message=f"Failed to index training data for agent '{agent.name}': {result.get('message', 'Unknown error')}",
                    data={
                        'error': result.get('message', 'Unknown error'),
                        'embedding_provider': embedding_provider,
                        'vector_store_backend': vector_store_backend,
                        'training_data_id': training_data_id
                    }
                )
            except Exception as notify_error:
                logger.warning(f"[RAG Training] Failed to send failure notification: {str(notify_error)}")
        
        total_duration = time.time() - start_time
        logger.info(f"[RAG Training] RAG indexing task completed for agent {agent_id} in {total_duration:.2f} seconds")
    
    except Agent.DoesNotExist:
        logger.error(f"[RAG Training] Agent {agent_id} not found")
    except Exception as e:
        total_duration = time.time() - start_time
        logger.error(f"[RAG Training] Error indexing training data for RAG (duration: {total_duration:.2f}s): {str(e)}", exc_info=True)


@shared_task(bind=True, max_retries=3)
def process_conversation_request(self, request_id, agent_id, conversation_id, message):
    """Process a conversation request asynchronously via Celery.
    
    Args:
        request_id: Unique request ID for tracking
        agent_id: Agent ID
        conversation_id: Conversation ID (can be None for new conversations)
        message: User message to process
    
    Returns:
        dict: Result with response, tool_calls, iterations
    """
    logger.info(f"Processing conversation request {request_id} for agent {agent_id}")
    
    try:
        # Published agents (API/webhook); testing agents only receive tasks enqueued by dashboard/trusted paths
        agent = Agent.objects.get(id=agent_id, status__in=['published', 'testing'])
        
        # Get or create conversation
        if conversation_id:
            conversation = Conversation.objects.get(
                conversation_id=conversation_id,
                agent=agent
            )
        else:
            # Create new conversation
            conversation = Conversation.objects.create(
                agent=agent,
                user=agent.user,
                conversation_id=str(uuid.uuid4()),
                status='active'
            )
        
        # Get or create request record
        try:
            request_obj = AgentRequest.objects.get(request_id=request_id)
        except AgentRequest.DoesNotExist:
            request_obj = AgentRequest.objects.create(
                request_id=request_id,
                agent=agent,
                conversation=conversation,
                status='pending',
                message=message,
                celery_task_id=self.request.id
            )
        
        # Update status to processing
        request_obj.status = 'processing'
        request_obj.celery_task_id = self.request.id
        request_obj.save(update_fields=['status', 'celery_task_id'])
        
        # Process the conversation
        agent_loop = AgentLoop(agent, conversation)
        system_prompt = agent.configuration.get('instruction') or agent.configuration.get('system_prompt', '')
        
        # Execute agent loop
        result = agent_loop.execute(message, system_prompt)
        
        # Update request with results
        request_obj.status = 'completed'
        request_obj.response = result.get('response', '')
        request_obj.tool_calls = result.get('tool_calls', [])
        request_obj.iterations = result.get('iterations', 1)
        request_obj.completed_at = timezone.now()
        request_obj.save()
        
        logger.info(f"Completed processing request {request_id}")
        
        return {
            'request_id': request_id,
            'conversation_id': conversation.conversation_id,
            'response': result.get('response', ''),
            'tool_calls': result.get('tool_calls', []),
            'iterations': result.get('iterations', 1),
            'status': 'completed'
        }
        
    except Agent.DoesNotExist:
        error_msg = f"Agent {agent_id} not found or not in published/testing status"
        logger.error(error_msg)
        try:
            request_obj = AgentRequest.objects.get(request_id=request_id)
            request_obj.status = 'failed'
            request_obj.error_message = error_msg
            request_obj.completed_at = timezone.now()
            request_obj.save()
        except AgentRequest.DoesNotExist:
            pass
        raise Exception(error_msg)
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error processing request {request_id}: {error_msg}", exc_info=True)
        
        # Update request with error
        try:
            request_obj = AgentRequest.objects.get(request_id=request_id)
            request_obj.status = 'failed'
            request_obj.error_message = error_msg
            request_obj.completed_at = timezone.now()
            request_obj.save()
        except AgentRequest.DoesNotExist:
            pass
        
        # Retry if we haven't exceeded max retries
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=60 * (self.request.retries + 1))
        else:
            raise


@shared_task(bind=True)
def send_agent_share_email(self, share_id):
    """Send email notification for agent share via Celery.
    
    Args:
        share_id: ID of the AgentShare instance
    
    Returns:
        dict: Result with success status and message
    """
    logger.info(f"[Agent Share Email Task] ===== TASK RECEIVED =====")
    logger.info(f"[Agent Share Email Task] Share ID: {share_id}")
    logger.info(f"[Agent Share Email Task] Task name: {self.name}")
    logger.info(f"[Agent Share Email Task] Task request ID: {self.request.id}")
    logger.info(f"[Agent Share Email Task] Task retries: {self.request.retries}")
    
    try:
        # Get the share instance
        share = AgentShare.objects.select_related('agent', 'shared_by').get(id=share_id)
        
        # Import here to avoid circular imports
        from .email_utils import send_email_with_fallback
        from django.conf import settings
        from django.urls import reverse
        
        # Build the share URL
        # Use SITE_URL from settings (defaults to https://oshaani.com)
        # Check if agent has site_url attribute, otherwise use settings
        site_url = None
        if hasattr(share.agent, 'site_url') and share.agent.site_url:
            site_url = share.agent.site_url
        else:
            site_url = getattr(settings, 'SITE_URL', 'https://oshaani.com')
        
        # Ensure site_url doesn't end with slash for URL construction
        site_url = site_url.rstrip('/')
        
        # Use reverse to get the URL path, then construct absolute URL
        try:
            share_path = reverse('accept_agent_share', kwargs={'token': share.token})
            # Remove leading slash if present and construct absolute URL
            share_path = share_path.lstrip('/')
            share_url = f"{site_url}/{share_path}"
        except Exception as reverse_error:
            logger.warning(f"Could not reverse URL for share acceptance, using fallback: {reverse_error}")
            # Fallback if reverse fails - use the correct path structure
            share_url = f"{site_url}/dashboard/share/accept/{share.token}/"
        
        logger.info(f"Generated share URL: {share_url} for share ID {share_id}")
        
        # Email subject and body
        subject = f'{share.shared_by.username} shared an agent with you: {share.agent.name}'
        
        # Plain text version (fallback)
        message_body = f"""
Hello,

{share.shared_by.username} ({share.shared_by.email}) has shared an AI agent with you.

Agent Name: {share.agent.name}
Agent Type: {share.agent.get_agent_type_display()}
Description: {share.agent.description or 'No description provided'}

"""
        
        if share.message:
            message_body += f"Message from {share.shared_by.username}:\n{share.message}\n\n"
        
        message_body += f"""
To access this agent, click on the following link:
{share_url}

"""
        
        if share.expires_at:
            message_body += f"This share will expire on {share.expires_at.strftime('%Y-%m-%d %H:%M:%S')}.\n\n"
        
        message_body += """
If you don't have an account, you'll be prompted to create one. Once you accept the share, you'll be able to access and use this agent.

Best regards,
AI Agents Platform
"""
        
        # HTML version (beautiful and mobile-friendly)
        html_message = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <title>Agent Shared with You</title>
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background-color: #f5f5f5; line-height: 1.6;">
    <!-- Email Container -->
    <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f5f5f5;">
        <tr>
            <td align="center" style="padding: 20px 10px;">
                <!-- Main Content Card -->
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 600px; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); overflow: hidden;">
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px 20px; text-align: center;">
                            <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 600;">🤖 Agent Shared with You</h1>
                        </td>
                    </tr>
                    
                    <!-- Content -->
                    <tr>
                        <td style="padding: 30px 20px;">
                            <!-- Greeting -->
                            <p style="margin: 0 0 20px 0; color: #333333; font-size: 16px;">
                                Hello,
                            </p>
                            
                            <p style="margin: 0 0 25px 0; color: #555555; font-size: 15px;">
                                <strong style="color: #333333;">{share.shared_by.username}</strong> ({share.shared_by.email}) has shared an AI agent with you.
                            </p>
                            
                            <!-- Agent Details Card -->
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa; border-radius: 6px; margin: 20px 0; border-left: 4px solid #667eea;">
                                <tr>
                                    <td style="padding: 20px;">
                                        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                                            <tr>
                                                <td style="padding: 8px 0;">
                                                    <strong style="color: #667eea; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Agent Name</strong>
                                                    <p style="margin: 4px 0 0 0; color: #333333; font-size: 18px; font-weight: 600;">{share.agent.name}</p>
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 8px 0;">
                                                    <strong style="color: #667eea; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Agent Type</strong>
                                                    <p style="margin: 4px 0 0 0; color: #555555; font-size: 15px;">{share.agent.get_agent_type_display()}</p>
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 8px 0;">
                                                    <strong style="color: #667eea; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Description</strong>
                                                    <p style="margin: 4px 0 0 0; color: #555555; font-size: 15px; line-height: 1.5;">{share.agent.description or 'No description provided'}</p>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                            </table>
                            
                            <!-- Personal Message (if provided) -->
"""
        
        if share.message:
            html_message += f"""
                            <div style="background-color: #fff3cd; border-left: 4px solid #ffc107; border-radius: 6px; padding: 15px 20px; margin: 20px 0;">
                                <p style="margin: 0 0 8px 0; color: #856404; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Message from {share.shared_by.username}</p>
                                <p style="margin: 0; color: #856404; font-size: 15px; line-height: 1.6;">{share.message}</p>
                            </div>
"""
        
        html_message += f"""
                            <!-- CTA Button -->
                            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin: 30px 0;">
                                <tr>
                                    <td align="center">
                                        <a href="{share_url}" style="display: inline-block; padding: 14px 32px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #ffffff; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 16px; text-align: center; box-shadow: 0 4px 6px rgba(102, 126, 234, 0.3);">
                                            Accept & Access Agent
                                        </a>
                                    </td>
                                </tr>
                            </table>
                            
                            <!-- Alternative Link -->
                            <p style="margin: 15px 0 0 0; color: #888888; font-size: 13px; text-align: center;">
                                Or copy and paste this link into your browser:<br>
                                <a href="{share_url}" style="color: #667eea; word-break: break-all; text-decoration: none;">{share_url}</a>
                            </p>
"""
        
        if share.expires_at:
            html_message += f"""
                            <!-- Expiration Notice -->
                            <div style="background-color: #e7f3ff; border-left: 4px solid #2196F3; border-radius: 6px; padding: 15px 20px; margin: 25px 0;">
                                <p style="margin: 0; color: #0d47a1; font-size: 14px;">
                                    <strong>⏰ Expiration:</strong> This share will expire on <strong>{share.expires_at.strftime('%B %d, %Y at %I:%M %p')}</strong>
                                </p>
                            </div>
"""
        
        html_message += """
                            <!-- Footer Info -->
                            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e0e0e0;">
                                <p style="margin: 0 0 10px 0; color: #888888; font-size: 14px; line-height: 1.6;">
                                    If you don't have an account, you'll be prompted to create one. Once you accept the share, you'll be able to access and use this agent.
                                </p>
                            </div>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8f9fa; padding: 20px; text-align: center; border-top: 1px solid #e0e0e0;">
                            <p style="margin: 0; color: #888888; font-size: 13px;">
                                Best regards,<br>
                                <strong style="color: #667eea;">AI Agents Platform</strong>
                            </p>
                        </td>
                    </tr>
                </table>
                
                <!-- Email Footer -->
                <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 600px; margin-top: 20px;">
                    <tr>
                        <td style="text-align: center; padding: 10px; color: #999999; font-size: 12px;">
                            <p style="margin: 0;">This email was sent to {share.email}</p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""
        
        # Send email
        send_email_with_fallback(
            subject=subject,
            message=message_body,
            html_message=html_message,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'support@oshaani.com'),
            recipient_list=[share.email],
            fail_silently=False,
        )
        
        logger.info(f"Successfully sent agent share email to {share.email} for share ID {share_id}")
        return {
            'success': True,
            'message': f'Email sent successfully to {share.email}',
            'share_id': share_id
        }
        
    except AgentShare.DoesNotExist:
        error_msg = f"AgentShare with ID {share_id} does not exist"
        logger.error(error_msg)
        return {
            'success': False,
            'error': error_msg,
            'share_id': share_id
        }
    except Exception as e:
        error_msg = f"Error sending agent share email: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {
            'success': False,
            'error': error_msg,
            'share_id': share_id
        }


@shared_task(bind=True)
def process_chat_message_background(self, agent_id, conversation_id, user_id, message, system_prompt=''):
    """
    Run the agent loop in the background for long-running chat requests.
    Caller should poll GET /api/chat/task-status/<task_id>/ for result.
    """
    from django.core.cache import cache
    from django.contrib.auth import get_user_model
    from .models import Conversation
    from .views_chat import user_has_agent_access

    User = get_user_model()
    task_id = self.request.id
    cache_key = f"chat_background_{task_id}"

    try:
        agent = Agent.objects.select_related('model').get(id=agent_id)
        conversation = Conversation.objects.get(conversation_id=conversation_id, agent=agent)
        user = User.objects.get(pk=user_id)
        if not user_has_agent_access(user, agent):
            out = {'success': False, 'error': 'Access denied'}
            cache.set(cache_key, out, timeout=3600)
            return out

        agent_loop = AgentLoop(agent, conversation, user=user)
        result = agent_loop.execute(message, system_prompt or '')

        # Build response shape expected by frontend (same as send_chat_message sync response)
        from django.utils import timezone
        from datetime import timedelta

        conversation.updated_at = timezone.now()
        conversation.save(update_fields=['updated_at'])

        agent_message_id = result.get('message_id')
        user_message_id = result.get('user_message_id')
        generated_files = []
        if agent_message_id and user_message_id:
            try:
                from .models import ConversationMessage, ConversationFile
                user_msg = ConversationMessage.objects.get(id=user_message_id)
                agent_msg = ConversationMessage.objects.get(id=agent_message_id)
                files = ConversationFile.objects.filter(
                    conversation=conversation,
                    agent=agent,
                    uploaded_at__gte=user_msg.created_at - timedelta(seconds=5),
                    uploaded_at__lte=agent_msg.created_at + timedelta(seconds=5)
                ).order_by('uploaded_at')
                for file_obj in files:
                    if file_obj.file_path:
                        file_path_str = file_obj.file_path.name if hasattr(file_obj.file_path, 'name') else str(file_obj.file_path)
                        file_url = f'/media/{file_path_str}'
                    else:
                        file_url = file_obj.download_url or ''
                    generated_files.append({
                        'file_id': file_obj.file_id,
                        'file_name': file_obj.file_name,
                        'file_url': file_url,
                        'file_type': file_obj.file_type,
                        'file_size': file_obj.file_size,
                    })
            except Exception as e:
                logger.warning(f"[Background chat] Generated files: {e}")

        # Usage tracking and revenue share (same as streaming task)
        try:
            from agents_app.platform_utils import track_usage
            from .models import AgentPublicShare, AgentShare

            billing_user = agent.user
            public_share = None
            email_share = None
            if user != agent.user:
                public_share = AgentPublicShare.objects.filter(agent=agent, is_active=True).first()
                email_share = AgentShare.objects.filter(
                    agent=agent, email=user.email, is_accepted=True, accepted_by=user
                ).first()
                if public_share and public_share.is_valid() and not email_share:
                    billing_user = user
                else:
                    billing_user = agent.user

            track_usage(billing_user, 'messages', count=1, is_daily=False)

            if user != agent.user:
                from .models import SharedAgentUsage
                from datetime import timedelta
                share = AgentShare.objects.filter(
                    agent=agent, email=user.email, is_accepted=True, accepted_by=user
                ).first()
                if share or (public_share and public_share.is_valid() and not email_share):
                    now = timezone.now()
                    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                    period_end = period_start.replace(year=period_start.year + 1, month=1) if period_start.month == 12 else period_start.replace(month=period_start.month + 1)
                    is_daily_usage = False
                    usage, created = SharedAgentUsage.objects.get_or_create(
                        agent=agent,
                        used_by=user,
                        shared_by=agent.user,
                        share=share,
                        period_start=period_start,
                        is_daily=is_daily_usage,
                        defaults={
                            'period_end': period_end,
                            'message_count': 1,
                            'conversation_count': 0,
                            'last_used_at': now,
                        }
                    )
                    if not created:
                        usage.message_count += 1
                        usage.last_used_at = now
                        usage.save()
        except Exception as e:
            logger.warning(f"[Background chat] Usage tracking: {e}")

        out = {
            'success': True,
            'response': result.get('response', ''),
            'conversation_id': conversation_id,
            'message_id': agent_message_id,
            'user_message_id': user_message_id,
            'tool_calls': result.get('tool_calls', []),
            'iterations': result.get('iterations', 1),
            'generated_files': generated_files,
        }
        cache.set(cache_key, out, timeout=3600)
        return out
    except Exception as e:
        logger.exception(f"[Background chat] Task {task_id} failed: {e}")
        out = {'success': False, 'error': str(e)}
        cache.set(cache_key, out, timeout=3600)
        return out


@shared_task(bind=True)
def process_chat_message_streaming_background(self, agent_id, conversation_id, user_id, message, system_prompt=''):
    """
    Run the agent loop with execute_stream() in the background. Consumes the stream,
    saves the final message, builds generated_files and runs usage tracking, then caches
    the result. Caller returns 202 immediately and client polls GET /api/chat/task-status/<task_id>/.
    """
    from django.core.cache import cache
    from django.contrib.auth import get_user_model
    from .models import Conversation, ConversationMessage, ConversationFile
    from .views_chat import user_has_agent_access

    User = get_user_model()
    task_id = self.request.id
    cache_key = f"chat_background_{task_id}"

    try:
        agent = Agent.objects.select_related('model').get(id=agent_id)
        conversation = Conversation.objects.get(conversation_id=conversation_id, agent=agent)
        user = User.objects.get(pk=user_id)
        if not user_has_agent_access(user, agent):
            out = {'success': False, 'error': 'Access denied'}
            cache.set(cache_key, out, timeout=3600)
            return out

        agent_loop = AgentLoop(agent, conversation, user=user)
        full_response = ""
        complete_data = None
        tool_calls = []

        for chunk in agent_loop.execute_stream(message, system_prompt or ''):
            if chunk.get('type') == 'chunk':
                full_response += chunk.get('content', '')
            elif chunk.get('type') == 'tool_call_start':
                tool_calls.append({
                    'tool': chunk.get('tool'),
                    'parameters': chunk.get('parameters', {}),
                    'result': {},
                    'state': 'executing',
                })
                cache.set(cache_key, {
                    'success': False,
                    'status': 'running',
                    'tool_calls': list(tool_calls),
                    'response': full_response,
                }, timeout=3600)
            elif chunk.get('type') == 'tool_call_result':
                if tool_calls and tool_calls[-1].get('state') == 'executing':
                    tool_calls[-1].update({
                        'result': chunk.get('result', {}),
                        'state': 'done',
                    })
                else:
                    tool_calls.append({
                        'tool': chunk.get('tool'),
                        'parameters': chunk.get('parameters', {}),
                        'result': chunk.get('result', {}),
                        'state': 'done',
                    })
                cache.set(cache_key, {
                    'success': False,
                    'status': 'running',
                    'tool_calls': list(tool_calls),
                    'response': full_response,
                }, timeout=3600)
            elif chunk.get('type') == 'complete':
                complete_data = chunk
                tool_calls = chunk.get('tool_calls', tool_calls)
                break

        if not complete_data:
            out = {'success': False, 'error': 'Stream did not complete'}
            cache.set(cache_key, out, timeout=3600)
            return out

        conversation.updated_at = timezone.now()
        conversation.save(update_fields=['updated_at'])

        agent_message_id = complete_data.get('message_id')
        user_message_id = complete_data.get('user_message_id')
        generated_files = []
        if agent_message_id and user_message_id:
            try:
                user_msg = ConversationMessage.objects.get(id=user_message_id)
                agent_msg = ConversationMessage.objects.get(id=agent_message_id)
                from datetime import timedelta
                files = ConversationFile.objects.filter(
                    conversation=conversation,
                    agent=agent,
                    uploaded_at__gte=user_msg.created_at - timedelta(seconds=5),
                    uploaded_at__lte=agent_msg.created_at + timedelta(seconds=5)
                ).order_by('uploaded_at')
                for file_obj in files:
                    if file_obj.file_path:
                        file_path_str = file_obj.file_path.name if hasattr(file_obj.file_path, 'name') else str(file_obj.file_path)
                        file_url = f'/media/{file_path_str}'
                    else:
                        file_url = file_obj.download_url or ''
                    generated_files.append({
                        'file_id': file_obj.file_id,
                        'file_name': file_obj.file_name,
                        'file_url': file_url,
                        'file_type': file_obj.file_type,
                        'file_size': file_obj.file_size,
                    })
            except Exception as e:
                logger.warning(f"[Background streaming chat] Generated files: {e}")

        # Usage tracking and revenue share (same logic as streaming view)
        try:
            from agents_app.platform_utils import track_usage
            from .models import AgentPublicShare, AgentShare

            billing_user = agent.user
            public_share = None
            email_share = None
            if user != agent.user:
                public_share = AgentPublicShare.objects.filter(agent=agent, is_active=True).first()
                email_share = AgentShare.objects.filter(
                    agent=agent, email=user.email, is_accepted=True, accepted_by=user
                ).first()
                if public_share and public_share.is_valid() and not email_share:
                    billing_user = user
                else:
                    billing_user = agent.user

            track_usage(billing_user, 'messages', count=1, is_daily=False)

            if user != agent.user:
                from .models import SharedAgentUsage
                from datetime import timedelta
                share = AgentShare.objects.filter(
                    agent=agent, email=user.email, is_accepted=True, accepted_by=user
                ).first()
                if share or (public_share and public_share.is_valid() and not email_share):
                    now = timezone.now()
                    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                    period_end = period_start.replace(year=period_start.year + 1, month=1) if period_start.month == 12 else period_start.replace(month=period_start.month + 1)
                    is_daily_usage = False
                    usage, created = SharedAgentUsage.objects.get_or_create(
                        agent=agent,
                        used_by=user,
                        shared_by=agent.user,
                        share=share,
                        period_start=period_start,
                        is_daily=is_daily_usage,
                        defaults={
                            'period_end': period_end,
                            'message_count': 1,
                            'conversation_count': 0,
                            'last_used_at': now,
                        }
                    )
                    if not created:
                        usage.message_count += 1
                        usage.last_used_at = now
                        usage.save()
        except Exception as e:
            logger.warning(f"[Background streaming chat] Usage tracking: {e}")

        out = {
            'success': True,
            'response': full_response,
            'conversation_id': conversation_id,
            'message_id': agent_message_id,
            'user_message_id': user_message_id,
            'tool_calls': tool_calls,
            'iterations': complete_data.get('iterations', 1),
            'generated_files': generated_files,
        }
        cache.set(cache_key, out, timeout=3600)
        return out
    except Exception as e:
        logger.exception(f"[Background streaming chat] Task {task_id} failed: {e}")
        out = {'success': False, 'error': str(e)}
        cache.set(cache_key, out, timeout=3600)
        return out


@shared_task
def cleanup_old_conversations():
    """
    Delete conversations older than retention period. Credits-only: same retention for all users.
    
    Retention period is configurable via SystemSettings (key: conversation_retention_days).
    Default: 30 days.
    """
    from .models import Conversation, ConversationMessage, SystemSettings
    from agents_app.platform_utils import get_conversation_retention_days
    from datetime import timedelta
    
    try:
        logger.info("[Conversation Cleanup] Starting cleanup of old conversations (credits-only retention)")
        
        default_retention_days = SystemSettings.get_setting('conversation_retention_days', default=30)
        try:
            default_retention_days = int(default_retention_days)
        except (ValueError, TypeError):
            default_retention_days = 30
        
        conversations_by_user = {}
        all_conversations = Conversation.objects.select_related('user').all()
        for conversation in all_conversations:
            user_id = conversation.user_id
            if user_id not in conversations_by_user:
                conversations_by_user[user_id] = []
            conversations_by_user[user_id].append(conversation)
        
        deleted_count = 0
        skipped_count = 0
        total_checked = 0
        
        logger.info(f"[Conversation Cleanup] Processing conversations for {len(conversations_by_user)} users")
        
        for user_id, conversations in conversations_by_user.items():
            try:
                user = conversations[0].user
                retention_days = get_conversation_retention_days(user, default_retention_days)
                cutoff_date = timezone.now() - timedelta(days=retention_days)
                
                for conversation in conversations:
                    total_checked += 1
                    
                    try:
                        # Check if conversation is older than retention period
                        if conversation.created_at >= cutoff_date:
                            # Conversation is still within retention period
                            skipped_count += 1
                            continue
                        
                        # Conversation is older than retention period - delete it
                        conversation_id = conversation.conversation_id
                        agent_name = conversation.agent.name if conversation.agent else 'Unknown'
                        created_at = conversation.created_at
                        age_days = (timezone.now() - created_at).days
                        
                        # Get message count before deletion for logging
                        message_count = ConversationMessage.objects.filter(conversation=conversation).count()
                        
                        conversation.delete()
                        deleted_count += 1
                        logger.info(
                            f"[Conversation Cleanup] Deleted conversation {conversation_id} "
                            f"(user: {user.username}, agent: {agent_name}, "
                            f"age: {age_days} days, messages: {message_count}, created: {created_at})"
                        )
                    except Exception as e:
                        logger.error(f"[Conversation Cleanup] Error processing conversation {conversation.conversation_id}: {str(e)}", exc_info=True)
                        skipped_count += 1
                        
            except Exception as e:
                logger.error(f"[Conversation Cleanup] Error processing user {user_id}: {str(e)}", exc_info=True)
        
        logger.info(
            f"[Conversation Cleanup] Cleanup completed: {deleted_count} deleted, "
            f"{skipped_count} skipped, {total_checked} total checked"
        )
        return {
            'success': True,
            'deleted_count': deleted_count,
            'skipped_count': skipped_count,
            'total_checked': total_checked,
            'retention_days': default_retention_days,
        }
        
    except Exception as e:
        logger.error(f"[Conversation Cleanup] Error in cleanup task: {str(e)}", exc_info=True)
        return {
            'success': False,
            'error': str(e)
        }
