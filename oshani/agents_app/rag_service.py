"""RAG (Retrieval-Augmented Generation) service for agents.

This module provides RAG functionality with strict data isolation:
- Each agent's training data is stored in separate vector collections
- Vector collections are named 'agent_{agent_id}' to ensure isolation
- All retrieval operations are scoped to a specific agent_id
- Cross-agent data access is prevented through validation
- When an agent is deleted, all associated vectors are automatically cleaned up

Data Isolation Guarantees:
1. Training data is tied to agents via ForeignKey with CASCADE delete
2. Vector stores use agent-specific collections (agent_{agent_id})
3. All search operations require agent_id and only search within that agent's collection
4. Metadata includes agent_id for additional validation
5. Signal handlers ensure cleanup when agents or training data are deleted
"""
import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple
from django.conf import settings
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


class DocumentChunker:
    """Chunks documents for RAG."""
    
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        """
        Initialize document chunker.
        
        Args:
            chunk_size: Maximum size of each chunk in characters
            chunk_overlap: Number of characters to overlap between chunks
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
    
    def chunk_text(self, text: str, metadata: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """
        Chunk text into smaller pieces.
        
        Args:
            text: Text to chunk
            metadata: Optional metadata to attach to each chunk
        
        Returns:
            List of chunk dictionaries with 'text' and 'metadata' keys
        """
        if not text or not text.strip():
            return []
        
        chunks = []
        text = text.strip()
        
        # Try to split by paragraphs first
        paragraphs = re.split(r'\n\s*\n', text)
        
        current_chunk = ""
        current_length = 0
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            para_length = len(para)
            
            # If paragraph fits in current chunk
            if current_length + para_length <= self.chunk_size:
                if current_chunk:
                    current_chunk += "\n\n" + para
                else:
                    current_chunk = para
                current_length += para_length + 2  # +2 for \n\n
            else:
                # Save current chunk if it exists
                if current_chunk:
                    chunks.append({
                        'text': current_chunk,
                        'metadata': metadata or {}
                    })
                
                # If paragraph is larger than chunk_size, split it by sentences
                if para_length > self.chunk_size:
                    sentences = re.split(r'(?<=[.!?])\s+', para)
                    current_chunk = ""
                    current_length = 0
                    
                    for sentence in sentences:
                        sentence = sentence.strip()
                        if not sentence:
                            continue
                        
                        sentence_length = len(sentence)
                        
                        if current_length + sentence_length <= self.chunk_size:
                            if current_chunk:
                                current_chunk += " " + sentence
                            else:
                                current_chunk = sentence
                            current_length += sentence_length + 1
                        else:
                            if current_chunk:
                                chunks.append({
                                    'text': current_chunk,
                                    'metadata': metadata or {}
                                })
                            
                            # Start new chunk with overlap
                            if self.chunk_overlap > 0 and current_chunk:
                                overlap_text = current_chunk[-self.chunk_overlap:]
                                current_chunk = overlap_text + " " + sentence
                                current_length = len(current_chunk)
                            else:
                                current_chunk = sentence
                                current_length = sentence_length
                else:
                    # Paragraph fits in one chunk
                    current_chunk = para
                    current_length = para_length
        
        # Add final chunk
        if current_chunk:
            chunks.append({
                'text': current_chunk,
                'metadata': metadata or {}
            })
        
        return chunks
    
    def chunk_document(self, content: Any, data_type: str, metadata: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """
        Chunk a document based on its type.
        
        Args:
            content: Document content (dict, str, or file path)
            data_type: Type of data ('text', 'knowledge_base', 'structured', 'file')
            metadata: Optional metadata
        
        Returns:
            List of chunks
        """
        if metadata is None:
            metadata = {}
        
        metadata['data_type'] = data_type
        
        # Extract text content
        if isinstance(content, dict):
            # Try to get text from various keys
            text = content.get('text') or content.get('content') or content.get('body')
            
            # If no direct text field, try to construct text from structured data (connector data)
            if not text:
                # Combine relevant fields for connector data (JIRA, GitLab, etc.)
                text_parts = []
                if content.get('title'):
                    text_parts.append(f"Title: {content['title']}")
                if content.get('description'):
                    text_parts.append(f"Description: {content['description']}")
                if content.get('summary'):
                    text_parts.append(f"Summary: {content['summary']}")
                if content.get('body'):
                    text_parts.append(content['body'])
                if content.get('content'):
                    text_parts.append(str(content['content']))
                
                # If we have structured parts, join them; otherwise fall back to string representation
                if text_parts:
                    text = '\n\n'.join(text_parts)
                else:
                    # Fall back to string representation of the dict
                    text = str(content)
        elif isinstance(content, str):
            text = content
        else:
            text = str(content)
        
        return self.chunk_text(text, metadata)


class EmbeddingGenerator:
    """Generates embeddings for text chunks."""
    
    def __init__(self, provider: str = 'bedrock'):
        """
        Initialize embedding generator.
        
        Args:
            provider: 'bedrock' or 'ollama'
        """
        self.provider = provider
        self._bedrock_failed = False  # Track if Bedrock has failed due to credentials
        self._last_embedding_error = None  # Reason for last failure (for logging)
    
    def _get_cache_key(self, text: str) -> str:
        """Generate cache key for text embedding."""
        import hashlib
        # Normalize text (lowercase, strip whitespace) for better cache hits
        normalized = text.lower().strip()[:500]  # Use first 500 chars for key
        text_hash = hashlib.md5(normalized.encode('utf-8')).hexdigest()
        return f"rag_embedding_{self.provider}_{text_hash}"
    
    def generate_embedding(self, text: str, use_cache: bool = True) -> Optional[List[float]]:
        """
        Generate embedding for text.
        
        Args:
            text: Text to embed
            use_cache: Whether to use cache (default: True)
        
        Returns:
            Embedding vector or None if failed
        """
        # Check cache first
        if use_cache:
            try:
                from django.core.cache import cache
                cache_key = self._get_cache_key(text)
                cached_embedding = cache.get(cache_key)
                if cached_embedding is not None:
                    logger.debug(f"[RAG Cache] Cache hit for embedding (key: {cache_key[:20]}...)")
                    return cached_embedding
            except Exception as e:
                logger.debug(f"[RAG Cache] Cache check failed: {str(e)}")
        
        try:
            embedding = None
            if self.provider == 'bedrock' and not self._bedrock_failed:
                embedding = self._generate_bedrock_embedding(text)
                # If Bedrock failed with credential error, try Ollama fallback only when configured/reachable
                if embedding is None and self._bedrock_failed:
                    from .ollama_integration import is_ollama_available
                    if is_ollama_available():
                        logger.warning("Bedrock failed, attempting Ollama fallback for embeddings")
                        embedding = self._generate_ollama_embedding(text)
            elif self.provider == 'ollama' or self._bedrock_failed:
                from .ollama_integration import is_ollama_available
                if is_ollama_available():
                    embedding = self._generate_ollama_embedding(text)
                # When Ollama is not available or failed, use Amazon Bedrock for RAG embeddings
                if embedding is None:
                    logger.info("Ollama not available or embedding failed, using Amazon Bedrock for RAG embeddings")
                    embedding = self._generate_bedrock_embedding(text)
            else:
                self._last_embedding_error = f"Unknown embedding provider: {self.provider}"
                logger.error(f"Unknown embedding provider: {self.provider}")
                return None
            
            self._last_embedding_error = None
            # Cache the result if successful
            if embedding and use_cache:
                try:
                    from django.core.cache import cache
                    cache_key = self._get_cache_key(text)
                    # Cache for 24 hours (86400 seconds)
                    cache.set(cache_key, embedding, 86400)
                    logger.debug(f"[RAG Cache] Cached embedding (key: {cache_key[:20]}...)")
                except Exception as e:
                    logger.debug(f"[RAG Cache] Cache set failed: {str(e)}")
            
            return embedding
        except Exception as e:
            self._last_embedding_error = str(e)
            logger.error(f"Error generating embedding: {str(e)}", exc_info=True)
            # Try Ollama fallback if Bedrock was being used and Ollama is available
            if self.provider == 'bedrock' and not self._bedrock_failed:
                from .ollama_integration import is_ollama_available
                if is_ollama_available():
                    logger.warning("Attempting Ollama fallback after exception")
                    self._bedrock_failed = True
                    return self._generate_ollama_embedding(text)
            # Try Bedrock fallback if Ollama was being used (e.g. Ollama not available)
            if self.provider == 'ollama':
                logger.info("Attempting Amazon Bedrock fallback for RAG embeddings after exception")
                return self._generate_bedrock_embedding(text)
            return None
    
    def _generate_bedrock_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding using AWS Bedrock."""
        try:
            import boto3
            from botocore.exceptions import ClientError
            
            # Use Titan Embeddings model with IAM role support
            from .aws_utils import create_boto3_client, get_aws_region
            region = get_aws_region()
            logger.debug(f"Using region: {region} for Bedrock embedding")
            bedrock_runtime = create_boto3_client('bedrock-runtime', region_name=region)
            
            # Optimize: Limit text length for faster embedding generation (same as Ollama)
            # Most embedding models work well with 200-500 characters
            # This reduces token usage and improves speed significantly
            optimized_text = text[:500] if len(text) > 500 else text
            
            # Prepare request for Titan Embeddings
            body = json.dumps({
                "inputText": optimized_text
            })
            
            response = bedrock_runtime.invoke_model(
                modelId="amazon.titan-embed-text-v1",
                body=body,
                contentType="application/json",
                accept="application/json"
            )
            
            result = json.loads(response['body'].read())
            embedding = result.get('embedding')
            
            if embedding:
                self._last_embedding_error = None
                return embedding
            else:
                self._last_embedding_error = "No embedding returned from Bedrock"
                logger.warning("No embedding returned from Bedrock")
                return None
                
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            
            # Check if this is a credential expiration error
            credential_errors = ('UnrecognizedClientException', 'InvalidClientTokenId', 'InvalidUserID.NotFound', 
                               'ExpiredTokenException', 'TokenRefreshRequired')
            is_credential_error = error_code in credential_errors or 'security token' in error_message.lower() or 'invalid token' in error_message.lower()
            
            if is_credential_error:
                logger.warning(f"Credential error detected in RAG embedding ({error_code}): {error_message}")
                logger.info("Attempting to refresh credentials and retry embedding generation...")
                
                # Try to refresh credentials by creating a new client
                try:
                    # Create a new client - this will get fresh credentials
                    bedrock_runtime_new = create_boto3_client('bedrock-runtime', region_name=region)
                    
                    # Retry the embedding generation with optimized text
                    retry_body = json.dumps({
                        "inputText": optimized_text
                    })
                    response = bedrock_runtime_new.invoke_model(
                        modelId="amazon.titan-embed-text-v1",
                        body=retry_body,
                        contentType="application/json",
                        accept="application/json"
                    )
                    
                    result = json.loads(response['body'].read())
                    embedding = result.get('embedding')
                    
                    if embedding:
                        logger.info("Successfully generated embedding after credential refresh")
                        return embedding
                    else:
                        self._last_embedding_error = "No embedding returned from Bedrock after credential refresh"
                        logger.warning("No embedding returned from Bedrock after credential refresh")
                except Exception as refresh_err:
                    self._last_embedding_error = str(refresh_err)
                    logger.error(f"Failed to refresh credentials and retry: {str(refresh_err)}")
            
            if error_code == 'UnrecognizedClientException':
                from .aws_utils import get_aws_region, is_ec2_instance
                import boto3
                current_region = get_aws_region()
                on_ec2 = is_ec2_instance()
                
                logger.error(f"AWS Bedrock authentication error: {error_message}")
                logger.error("The security token included in the request is invalid.")
                logger.error(f"Region: {current_region}, EC2 Instance: {on_ec2}")
                
                # Log credential information to help diagnose the issue
                try:
                    session = boto3.Session(region_name=current_region)
                    creds = session.get_credentials()
                    if creds:
                        logger.error(f"Credential method: {getattr(creds, 'method', 'unknown')}")
                        if hasattr(creds, 'access_key') and creds.access_key:
                            logger.error(f"Access key starts with: {creds.access_key[:10]}...")
                        # Try to get identity to check if role is being used
                        try:
                            sts_client = session.client('sts', region_name=current_region)
                            identity = sts_client.get_caller_identity()
                            arn = identity.get('Arn', '')
                            logger.error(f"AWS Identity ARN: {arn}")
                            if ':role/' in arn or '/assumed-role/' in arn:
                                logger.error("✓ IAM role IS being used (role ARN detected)")
                            else:
                                logger.error("✗ IAM role NOT detected (using user/access key credentials)")
                        except Exception as sts_err:
                            logger.error(f"Could not verify identity: {str(sts_err)}")
                except Exception as cred_err:
                    logger.error(f"Could not check credentials: {str(cred_err)}")
                
                if on_ec2:
                    logger.info("Running on EC2 with IAM role - checking configuration:")
                    logger.info("1. Verify IAM role 'Bedrock-admin' has bedrock:InvokeModel permission")
                    logger.info("2. Check if Bedrock is enabled in region: " + current_region)
                    logger.info("3. Some Bedrock models may not be available in eu-north-1")
                    logger.info("4. Try using us-east-1 or us-west-2 if model is not available")
                else:
                    logger.info("Possible causes:")
                    logger.info("1. AWS credentials are expired or invalid")
                    logger.info("2. AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are not set correctly")
                    logger.info("3. The IAM role/user doesn't have permissions for Bedrock")
                
                logger.info("Solutions:")
                logger.info("- Verify IAM role has bedrock:InvokeModel permission")
                logger.info("- Check if Bedrock service is enabled in AWS console for region: " + current_region)
                logger.info("- Some models may require enabling in Bedrock console first")
                logger.info("- Consider using us-east-1 or us-west-2 if eu-north-1 doesn't support the model")
                logger.info("- Run: python manage.py check_aws_credentials")
                # Mark Bedrock as failed so we can fall back to Ollama
                self._bedrock_failed = True
            else:
                logger.error(f"AWS Bedrock error ({error_code}): {error_message}")
            self._last_embedding_error = f"{error_code}: {error_message}"
            return None
        except Exception as e:
            self._last_embedding_error = str(e)
            logger.error(f"Error generating Bedrock embedding: {str(e)}", exc_info=True)
            return None
    
    def _generate_ollama_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding using Ollama."""
        try:
            import requests
            import urllib3
            
            # Suppress SSL warnings when verify_ssl is False
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            ollama_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
            model = getattr(settings, 'OLLAMA_EMBEDDING_MODEL', 'nomic-embed-text')
            verify_ssl = getattr(settings, 'OLLAMA_VERIFY_SSL', False)
            
            # First, check if Ollama server is running
            try:
                health_check = requests.get(f"{ollama_url}/api/tags", timeout=5, verify=verify_ssl)
                if health_check.status_code != 200:
                    self._last_embedding_error = f"Ollama not responding (status: {health_check.status_code})"
                    logger.warning(f"Ollama server at {ollama_url} is not responding correctly (status: {health_check.status_code})")
                    return None
            except requests.exceptions.ConnectionError:
                self._last_embedding_error = f"Cannot connect to Ollama at {ollama_url}"
                logger.warning(f"Cannot connect to Ollama server at {ollama_url}. Make sure Ollama is running: 'ollama serve'")
                return None
            except Exception as e:
                self._last_embedding_error = str(e)
                logger.warning(f"Error checking Ollama server: {str(e)}")
                return None
            
            # Optimize: Limit text length for faster embedding generation
            # Most embedding models work well with 200-500 characters
            optimized_text = text[:500] if len(text) > 500 else text
            
            # Try the embeddings endpoint
            response = requests.post(
                f"{ollama_url}/api/embeddings",
                json={
                    "model": model,
                    "prompt": optimized_text
                },
                timeout=15,  # Reduced timeout from 30 to 15 seconds
                verify=verify_ssl
            )
            
            # Handle 404 specifically - model might not be available
            if response.status_code == 404:
                logger.error(
                    f"Ollama embedding model '{model}' not found (404). "
                    f"Please pull the model first: 'ollama pull {model}'. "
                    f"Common embedding models: nomic-embed-text, all-minilm"
                )
                return None
            
            response.raise_for_status()
            result = response.json()
            
            embedding = result.get('embedding')
            if embedding:
                self._last_embedding_error = None
                return embedding
            else:
                self._last_embedding_error = "No embedding returned from Ollama"
                logger.warning("No embedding returned from Ollama")
                return None
                
        except requests.exceptions.HTTPError as e:
            if e.response and e.response.status_code == 404:
                self._last_embedding_error = f"Ollama model '{model}' not found (404)"
                logger.error(
                    f"Ollama embedding endpoint not found (404). "
                    f"This might indicate the Ollama version doesn't support embeddings, "
                    f"or the model '{model}' is not available. "
                    f"Try: 'ollama pull {model}'"
                )
            else:
                self._last_embedding_error = str(e)
                logger.error(f"HTTP error generating Ollama embedding: {str(e)}")
            return None
        except requests.exceptions.ConnectionError:
            self._last_embedding_error = f"Cannot connect to Ollama at {ollama_url}"
            logger.warning(f"Cannot connect to Ollama server at {ollama_url}. Make sure Ollama is running: 'ollama serve'")
            return None
        except Exception as e:
            self._last_embedding_error = str(e)
            logger.error(f"Error generating Ollama embedding: {str(e)}", exc_info=True)
            return None
    
    def generate_embeddings_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
        
        Returns:
            List of embeddings (None for failed ones)
        """
        import time
        
        total_texts = len(texts)
        logger.info(f"[RAG Embedding] Generating embeddings for {total_texts} text(s) using {self.provider}")
        
        embeddings = []
        success_count = 0
        failed_count = 0
        batch_start = time.time()
        
        for idx, text in enumerate(texts, 1):
            embedding = self.generate_embedding(text)
            embeddings.append(embedding)
            
            if embedding:
                success_count += 1
            else:
                failed_count += 1
                if idx <= 10 or idx % 100 == 0:  # Log first 10 failures and every 100th failure
                    logger.warning(f"[RAG Embedding] Failed to generate embedding for text {idx}/{total_texts}")
            
            # Log progress for large batches
            if total_texts > 50 and (idx % 50 == 0 or idx == total_texts):
                elapsed = time.time() - batch_start
                rate = idx / elapsed if elapsed > 0 else 0
                logger.info(f"[RAG Embedding] Progress: {idx}/{total_texts} ({success_count} success, {failed_count} failed, {rate:.1f} texts/sec)")
        
        batch_duration = time.time() - batch_start
        logger.info(f"[RAG Embedding] Batch embedding completed: {success_count} success, {failed_count} failed in {batch_duration:.2f} seconds ({total_texts/batch_duration:.1f} texts/sec)")
        
        return embeddings


class VectorStore:
    """Vector store for RAG (supports in-memory and Qdrant backends)."""
    
    def __init__(self, backend: str = 'qdrant', qdrant_url: Optional[str] = None, qdrant_api_key: Optional[str] = None):
        """
        Initialize vector store.
        
        Args:
            backend: 'memory' or 'qdrant' (default: 'qdrant')
            qdrant_url: Qdrant server URL (required for qdrant backend)
            qdrant_api_key: Qdrant API key (optional, for cloud Qdrant)
        """
        self.backend = backend
        self.vectors = {}  # For in-memory backend
        self.qdrant_client = None
        self.qdrant_collections = {}  # Track created collections
        
        if backend == 'qdrant':
            self._init_qdrant(qdrant_url, qdrant_api_key)
    
    def _init_qdrant(self, url: Optional[str], api_key: Optional[str]):
        """Initialize Qdrant client."""
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
            
            if not url:
                try:
                    url = getattr(settings, 'QDRANT_URL', 'http://localhost:6333')
                except:
                    url = 'http://localhost:6333'
            
            if api_key:
                self.qdrant_client = QdrantClient(url=url, api_key=api_key)
            else:
                self.qdrant_client = QdrantClient(url=url)
            
            # Test connection
            try:
                self.qdrant_client.get_collections()
                logger.info(f"Initialized Qdrant client: {url}")
            except Exception as e:
                logger.warning(f"Qdrant client initialized but connection test failed: {str(e)}")
                logger.warning("Make sure Qdrant server is running. Continuing anyway...")
                
        except ImportError:
            logger.error("qdrant-client not installed. Install with: pip install qdrant-client")
            raise
        except Exception as e:
            logger.error(f"Error initializing Qdrant client: {str(e)}")
            raise
    
    def add_vectors(self, agent_id: int, chunks: List[Dict[str, Any]], embeddings: List[List[float]]):
        """
        Add vectors to store.
        
        Args:
            agent_id: Agent ID
            chunks: List of chunk dictionaries
            embeddings: List of embedding vectors
        """
        if self.backend == 'qdrant':
            self._add_vectors_qdrant(agent_id, chunks, embeddings)
        else:
            self._add_vectors_memory(agent_id, chunks, embeddings)
    
    def _add_vectors_memory(self, agent_id: int, chunks: List[Dict[str, Any]], embeddings: List[List[float]]):
        """Add vectors to in-memory store."""
        import time
        
        storage_start = time.time()
        logger.info(f"[RAG Vector Store] Storing {len(chunks)} vectors in memory for agent {agent_id}")
        
        if agent_id not in self.vectors:
            self.vectors[agent_id] = {}
            logger.debug(f"[RAG Vector Store] Created new memory store for agent {agent_id}")
        
        stored_count = 0
        for chunk, embedding in zip(chunks, embeddings):
            if embedding:  # Only add if embedding was generated
                chunk_id = f"{len(self.vectors[agent_id])}"
                self.vectors[agent_id][chunk_id] = {
                    'vector': embedding,
                    'text': chunk.get('text', ''),
                    'metadata': chunk.get('metadata', {})
                }
                stored_count += 1
        
        storage_duration = time.time() - storage_start
        logger.info(f"[RAG Vector Store] Stored {stored_count} vectors in memory for agent {agent_id} in {storage_duration:.2f} seconds (total vectors: {len(self.vectors[agent_id])})")
    
    def _add_vectors_qdrant(self, agent_id: int, chunks: List[Dict[str, Any]], embeddings: List[List[float]]):
        """Add vectors to Qdrant."""
        try:
            from qdrant_client.models import Distance, VectorParams, PointStruct
            
            collection_name = f"agent_{agent_id}"
            
            # Create collection if it doesn't exist
            if collection_name not in self.qdrant_collections:
                try:
                    # Check if collection exists
                    collections = self.qdrant_client.get_collections().collections
                    collection_exists = any(c.name == collection_name for c in collections)
                    
                    if not collection_exists:
                        # Get vector size from first embedding
                        if embeddings and embeddings[0]:
                            vector_size = len(embeddings[0])
                            self.qdrant_client.create_collection(
                                collection_name=collection_name,
                                vectors_config=VectorParams(
                                    size=vector_size,
                                    distance=Distance.COSINE
                                )
                            )
                            logger.info(f"Created Qdrant collection: {collection_name}")
                    
                    self.qdrant_collections[collection_name] = True
                except Exception as e:
                    logger.error(f"Error creating Qdrant collection: {str(e)}")
                    raise
            
            # Get current collection count to generate unique IDs
            try:
                collection_info = self.qdrant_client.get_collection(collection_name)
                current_count = collection_info.points_count
                logger.debug(f"[RAG Vector Store] Qdrant collection {collection_name} currently has {current_count} points")
            except:
                current_count = 0
                logger.debug(f"[RAG Vector Store] Starting with 0 points for new collection {collection_name}")
            
            # Prepare points for batch upload
            import time
            prep_start = time.time()
            points = []
            for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                if embedding:
                    # Generate unique point ID
                    point_id = current_count + idx
                    metadata = chunk.get('metadata', {}).copy()
                    metadata['text'] = chunk.get('text', '')
                    metadata['agent_id'] = agent_id
                    metadata['data_type'] = chunk.get('metadata', {}).get('data_type', 'text')
                    
                    points.append(
                        PointStruct(
                            id=point_id,
                            vector=embedding,
                            payload=metadata
                        )
                    )
            
            prep_duration = time.time() - prep_start
            logger.info(f"[RAG Vector Store] Prepared {len(points)} points for Qdrant upload in {prep_duration:.2f} seconds")
            
            # Upload points in batches
            if points:
                upload_start = time.time()
                batch_size = 100
                total_batches = (len(points) + batch_size - 1) // batch_size
                logger.info(f"[RAG Vector Store] Uploading {len(points)} vectors to Qdrant in {total_batches} batch(es) (batch size: {batch_size})")
                
                for batch_idx, i in enumerate(range(0, len(points), batch_size), 1):
                    batch = points[i:i + batch_size]
                    batch_start = time.time()
                    self.qdrant_client.upsert(
                        collection_name=collection_name,
                        points=batch
                    )
                    batch_duration = time.time() - batch_start
                    logger.debug(f"[RAG Vector Store] Uploaded batch {batch_idx}/{total_batches} ({len(batch)} points) in {batch_duration:.2f} seconds")
                
                upload_duration = time.time() - upload_start
                logger.info(f"[RAG Vector Store] Added {len(points)} vectors to Qdrant collection {collection_name} in {upload_duration:.2f} seconds ({len(points)/upload_duration:.1f} vectors/sec)")
            else:
                logger.warning(f"[RAG Vector Store] No points to upload to Qdrant collection {collection_name}")
                
        except Exception as e:
            logger.error(f"Error adding vectors to Qdrant: {str(e)}", exc_info=True)
            raise
    
    def search(self, agent_id: int, query_embedding: List[float], top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Search for similar vectors.
        
        Args:
            agent_id: Agent ID
            query_embedding: Query embedding vector
            top_k: Number of results to return
        
        Returns:
            List of similar chunks with scores
        """
        if self.backend == 'qdrant':
            return self._search_qdrant(agent_id, query_embedding, top_k)
        else:
            return self._search_memory(agent_id, query_embedding, top_k)
    
    def _search_memory(self, agent_id: int, query_embedding: List[float], top_k: int) -> List[Dict[str, Any]]:
        """Search in-memory vectors with agent isolation."""
        if agent_id not in self.vectors:
            return []
        
        results = []
        
        for chunk_id, chunk_data in self.vectors[agent_id].items():
            # Security: Verify agent_id in metadata matches requested agent_id
            metadata = chunk_data.get('metadata', {})
            metadata_agent_id = metadata.get('agent_id')
            if metadata_agent_id is not None and metadata_agent_id != agent_id:
                logger.warning(f"Security: Found chunk with mismatched agent_id {metadata_agent_id} for agent {agent_id}, skipping")
                continue
            
            vector = chunk_data['vector']
            similarity = self._cosine_similarity(query_embedding, vector)
            
            results.append({
                'chunk_id': chunk_id,
                'text': chunk_data['text'],
                'metadata': metadata,
                'score': similarity
            })
        
        # Sort by similarity (descending) and return top_k
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:top_k]
    
    def _search_qdrant(self, agent_id: int, query_embedding: List[float], top_k: int) -> List[Dict[str, Any]]:
        """Search Qdrant vectors."""
        try:
            collection_name = f"agent_{agent_id}"
            
            # Check if collection exists
            collections = self.qdrant_client.get_collections().collections
            collection_exists = any(c.name == collection_name for c in collections)
            
            if not collection_exists:
                # This is expected if the agent hasn't been indexed yet - use debug level
                logger.debug(f"Qdrant collection {collection_name} does not exist (agent may not have indexed training data yet)")
                return []
            
            # Search in Qdrant using query_points (new API)
            # The query_points method accepts a query parameter which can be a vector directly
            query_response = self.qdrant_client.query_points(
                collection_name=collection_name,
                query=query_embedding,  # Pass vector directly
                limit=top_k,
                with_payload=True,
                with_vectors=False
            )
            
            # Format results and validate agent_id isolation
            results = []
            for point in query_response.points:
                payload = point.payload or {}
                # Security: Verify agent_id in metadata matches requested agent_id
                payload_agent_id = payload.get('agent_id')
                if payload_agent_id is not None and payload_agent_id != agent_id:
                    logger.warning(f"Security: Found chunk with mismatched agent_id {payload_agent_id} in collection for agent {agent_id}, skipping")
                    continue
                
                # Get score from the point (query_points returns points with scores)
                score = point.score if hasattr(point, 'score') else 0.0
                
                results.append({
                    'chunk_id': str(point.id),
                    'text': payload.get('text', ''),
                    'metadata': {k: v for k, v in payload.items() if k != 'text'},
                    'score': score
                })
            
            return results
            
        except Exception as e:
            logger.error(f"Error searching Qdrant: {str(e)}", exc_info=True)
            return []
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        try:
            import math
            
            dot_product = sum(a * b for a, b in zip(vec1, vec2))
            magnitude1 = math.sqrt(sum(a * a for a in vec1))
            magnitude2 = math.sqrt(sum(a * a for a in vec2))
            
            if magnitude1 == 0 or magnitude2 == 0:
                return 0.0
            
            return dot_product / (magnitude1 * magnitude2)
        except Exception as e:
            logger.error(f"Error calculating cosine similarity: {str(e)}")
            return 0.0
    
    def clear_agent_vectors(self, agent_id: int):
        """Clear all vectors for an agent."""
        if self.backend == 'qdrant':
            self._clear_agent_vectors_qdrant(agent_id)
        else:
            self._clear_agent_vectors_memory(agent_id)
    
    def _clear_agent_vectors_memory(self, agent_id: int):
        """Clear in-memory vectors for an agent."""
        if agent_id in self.vectors:
            vector_count = len(self.vectors[agent_id])
            del self.vectors[agent_id]
            logger.info(f"[RAG Vector Store] Cleared {vector_count} vectors from memory for agent {agent_id}")
        else:
            logger.debug(f"[RAG Vector Store] No vectors to clear in memory for agent {agent_id}")
    
    def _clear_agent_vectors_qdrant(self, agent_id: int):
        """Clear Qdrant vectors for an agent."""
        try:
            collection_name = f"agent_{agent_id}"
            
            # Check if collection exists and get count
            collections = self.qdrant_client.get_collections().collections
            collection_exists = any(c.name == collection_name for c in collections)
            
            if collection_exists:
                # Get count before deletion
                try:
                    collection_info = self.qdrant_client.get_collection(collection_name)
                    vector_count = collection_info.points_count
                except:
                    vector_count = 0
                
                # Delete collection
                self.qdrant_client.delete_collection(collection_name)
                if collection_name in self.qdrant_collections:
                    del self.qdrant_collections[collection_name]
                logger.info(f"[RAG Vector Store] Deleted Qdrant collection {collection_name} ({vector_count} vectors cleared)")
            else:
                logger.debug(f"[RAG Vector Store] Qdrant collection {collection_name} does not exist, nothing to clear")
        except Exception as e:
            logger.error(f"[RAG Vector Store] Error clearing Qdrant vectors for agent {agent_id}: {str(e)}", exc_info=True)
    
    def get_agent_vector_count(self, agent_id: int) -> int:
        """Get number of vectors stored for an agent."""
        if self.backend == 'qdrant':
            return self._get_agent_vector_count_qdrant(agent_id)
        else:
            return self._get_agent_vector_count_memory(agent_id)
    
    def _get_agent_vector_count_memory(self, agent_id: int) -> int:
        """Get count from in-memory store."""
        if agent_id not in self.vectors:
            return 0
        return len(self.vectors[agent_id])
    
    def _get_agent_vector_count_qdrant(self, agent_id: int) -> int:
        """Get count from Qdrant."""
        try:
            collection_name = f"agent_{agent_id}"
            
            # Check if collection exists
            collections = self.qdrant_client.get_collections().collections
            collection_exists = any(c.name == collection_name for c in collections)
            
            if not collection_exists:
                return 0
            
            # Get collection info
            collection_info = self.qdrant_client.get_collection(collection_name)
            return collection_info.points_count
            
        except Exception as e:
            logger.error(f"Error getting Qdrant vector count: {str(e)}")
            return 0


class RAGService:
    """Main RAG service that orchestrates chunking, embedding, and retrieval."""
    
    def __init__(self, embedding_provider: str = 'bedrock', chunk_size: int = 1000, chunk_overlap: int = 200,
                 vector_store_backend: str = None, qdrant_url: Optional[str] = None, qdrant_api_key: Optional[str] = None):
        """
        Initialize RAG service.
        
        Args:
            embedding_provider: 'bedrock' or 'ollama'
            chunk_size: Size of text chunks
            chunk_overlap: Overlap between chunks
            vector_store_backend: 'memory' or 'qdrant' (defaults to settings or 'qdrant')
            qdrant_url: Qdrant server URL (required for qdrant backend)
            qdrant_api_key: Qdrant API key (optional, for cloud Qdrant)
        """
        self.chunker = DocumentChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        self.embedding_generator = EmbeddingGenerator(provider=embedding_provider)
        
        # Determine vector store backend
        if vector_store_backend is None:
            try:
                vector_store_backend = getattr(settings, 'RAG_VECTOR_STORE_BACKEND', 'qdrant')
            except:
                vector_store_backend = 'qdrant'
        
        if qdrant_url is None:
            try:
                qdrant_url = getattr(settings, 'QDRANT_URL', None)
            except:
                qdrant_url = None
        
        if qdrant_api_key is None:
            try:
                qdrant_api_key = getattr(settings, 'QDRANT_API_KEY', None)
            except:
                qdrant_api_key = None
        
        self.vector_store = VectorStore(
            backend=vector_store_backend,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key
        )
    
    def index_training_data(self, agent_id: int, training_data_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Index training data for an agent.
        
        This method ensures strict data isolation - training data is only indexed
        for the specified agent and cannot be accessed by other agents.
        
        Args:
            agent_id: Agent ID (required for isolation)
            training_data_list: List of training data items
        
        Returns:
            Dictionary with indexing results
        """
        import time
        
        if not agent_id:
            raise ValueError("agent_id is required for data isolation")
        
        try:
            logger.info(f"[RAG Service] Starting indexing for agent {agent_id} with {len(training_data_list)} training data item(s)")
            
            # Clear existing vectors for this agent (ensures clean re-indexing)
            clear_start = time.time()
            logger.info(f"[RAG Service] Clearing existing vectors for agent {agent_id}")
            self.vector_store.clear_agent_vectors(agent_id)
            clear_duration = time.time() - clear_start
            logger.info(f"[RAG Service] Cleared existing vectors in {clear_duration:.2f} seconds")
            
            all_chunks = []
            chunking_start = time.time()
            
            # Chunk all training data
            logger.info(f"[RAG Service] Starting chunking process for {len(training_data_list)} training data item(s)")
            for idx, data_item in enumerate(training_data_list, 1):
                content = data_item.get('content', {})
                data_type = data_item.get('data_type', 'text')
                
                # Calculate content size for logging
                if isinstance(content, dict):
                    content_size = len(str(content.get('text', '')))
                else:
                    content_size = len(str(content))
                
                logger.debug(f"[RAG Service] Chunking item {idx}/{len(training_data_list)} (type: {data_type}, size: {content_size:,} chars)")
                
                chunks = self.chunker.chunk_document(content, data_type)
                all_chunks.extend(chunks)
                
                logger.debug(f"[RAG Service] Item {idx} chunked into {len(chunks)} chunk(s)")
            
            chunking_duration = time.time() - chunking_start
            logger.info(f"[RAG Service] Chunking completed: {len(all_chunks)} total chunks created in {chunking_duration:.2f} seconds")
            
            if not all_chunks:
                logger.warning(f"[RAG Service] No chunks created from training data for agent {agent_id}")
                return {
                    'success': False,
                    'message': 'No chunks created from training data',
                    'chunks_count': 0
                }
            
            # Generate embeddings for all chunks
            embedding_start = time.time()
            logger.info(f"[RAG Service] Generating embeddings for {len(all_chunks)} chunks using {self.embedding_generator.provider}")
            texts = [chunk['text'] for chunk in all_chunks]
            total_text_length = sum(len(text) for text in texts)
            logger.debug(f"[RAG Service] Total text length to embed: {total_text_length:,} characters")
            
            embeddings = self.embedding_generator.generate_embeddings_batch(texts)
            embedding_duration = time.time() - embedding_start
            logger.info(f"[RAG Service] Embedding generation completed in {embedding_duration:.2f} seconds")
            
            # Filter out chunks without embeddings
            valid_chunks = []
            valid_embeddings = []
            for chunk, embedding in zip(all_chunks, embeddings):
                if embedding:
                    valid_chunks.append(chunk)
                    valid_embeddings.append(embedding)
            
            failed_count = len(all_chunks) - len(valid_chunks)
            if failed_count > 0:
                logger.warning(f"[RAG Service] {failed_count} chunk(s) failed to generate embeddings (out of {len(all_chunks)} total)")
            else:
                logger.info(f"[RAG Service] All {len(valid_chunks)} chunks successfully generated embeddings")
            
            # Store vectors
            storage_start = time.time()
            if valid_chunks and valid_embeddings:
                logger.info(f"[RAG Service] Storing {len(valid_chunks)} vectors in vector store for agent {agent_id}")
                self.vector_store.add_vectors(agent_id, valid_chunks, valid_embeddings)
                storage_duration = time.time() - storage_start
                logger.info(f"[RAG Service] Vector storage completed in {storage_duration:.2f} seconds")
            else:
                logger.warning(f"[RAG Service] No valid vectors to store (valid_chunks: {len(valid_chunks)}, valid_embeddings: {len(valid_embeddings)})")
            
            result = {
                'success': True,
                'message': f'Indexed {len(valid_chunks)} chunks for agent {agent_id}',
                'chunks_count': len(valid_chunks),
                'total_chunks': len(all_chunks),
                'failed_embeddings': failed_count
            }
            
            logger.info(f"[RAG Service] Indexing completed successfully for agent {agent_id}: {result['chunks_count']} chunks indexed, {result['failed_embeddings']} failed")
            return result
            
        except Exception as e:
            logger.error(f"[RAG Service] Error indexing training data for agent {agent_id}: {str(e)}", exc_info=True)
            return {
                'success': False,
                'message': f'Error indexing: {str(e)}',
                'chunks_count': 0
            }
    
    def retrieve(self, agent_id: int, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Retrieve relevant chunks for a query.
        
        This method ensures strict data isolation - only returns chunks
        that belong to the specified agent. Cross-agent data access is prevented.
        
        Args:
            agent_id: Agent ID (required for isolation)
            query: Query text
            top_k: Number of chunks to retrieve
        
        Returns:
            List of relevant chunks with scores (all belonging to the specified agent)
        """
        if not agent_id:
            raise ValueError("agent_id is required for data isolation")
        
        try:
            import time
            # Generate query embedding
            embedding_start = time.time()
            query_embedding = self.embedding_generator.generate_embedding(query)
            embedding_duration = time.time() - embedding_start
            
            if not query_embedding:
                query_preview = query[:200] + "..." if len(query) > 200 else query
                reason = getattr(self.embedding_generator, '_last_embedding_error', None) or "unknown"
                logger.warning(
                    f"Failed to generate embedding for query (length: {len(query)} chars): {query_preview}. "
                    f"Reason: {reason}. RAG retrieval skipped for this query."
                )
                return []
            
            # Search vector store
            search_start = time.time()
            results = self.vector_store.search(agent_id, query_embedding, top_k=top_k)
            search_duration = time.time() - search_start
            
            logger.debug(f"[RAG Performance] Embedding: {embedding_duration:.3f}s, Vector search: {search_duration:.3f}s")
            
            return results
            
        except Exception as e:
            logger.error(f"Error retrieving chunks: {str(e)}", exc_info=True)
            return []
    
    def get_retrieval_context(self, agent_id: int, query: str, top_k: int = 5) -> str:
        """
        Get formatted context string from retrieval.
        
        Args:
            agent_id: Agent ID
            query: Query text
            top_k: Number of chunks to retrieve
        
        Returns:
            Formatted context string
        """
        import time
        retrieval_start = time.time()
        results = self.retrieve(agent_id, query, top_k=top_k)
        retrieval_duration = time.time() - retrieval_start
        logger.info(f"[RAG Performance] Retrieval completed in {retrieval_duration:.3f}s (agent_id={agent_id}, top_k={top_k}, results={len(results) if results else 0})")
        
        if not results:
            return ""
        
        context_parts = ["Retrieved Context from Knowledge Base:"]
        for i, result in enumerate(results, 1):
            score = result.get('score', 0)
            text = result.get('text', '')
            metadata = result.get('metadata', {})
            data_type = metadata.get('data_type', 'unknown')
            
            context_parts.append(f"\n[{i}] (Relevance: {score:.3f}, Type: {data_type})")
            context_parts.append(text)
        
        return "\n".join(context_parts)


# Global RAG service instances (per embedding provider)
_rag_services = {}

def get_rag_service(embedding_provider: str = 'bedrock', vector_store_backend: Optional[str] = None) -> RAGService:
    """
    Get or create RAG service instance.
    
    Args:
        embedding_provider: 'bedrock' or 'ollama'
        vector_store_backend: 'memory' or 'qdrant' (defaults to settings)
    
    Returns:
        RAGService instance
    """
    global _rag_services
    
    # Create key from provider and backend
    if vector_store_backend is None:
        vector_store_backend = getattr(settings, 'RAG_VECTOR_STORE_BACKEND', 'qdrant')
    
    key = f"{embedding_provider}_{vector_store_backend}"
    
    if key not in _rag_services:
        _rag_services[key] = RAGService(
            embedding_provider=embedding_provider,
            vector_store_backend=vector_store_backend
        )
    
    return _rag_services[key]

