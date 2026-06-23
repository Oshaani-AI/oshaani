"""Ollama integration for AI agents."""
import requests
import json
import urllib3
import logging
import time
from django.conf import settings
from typing import Optional, Dict, Any

# Suppress SSL warnings when verify_ssl is False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Cache for availability check to avoid repeated connection attempts (TTL 60s)
_ollama_available_cache = None
_ollama_available_cache_time = 0.0
_OLLAMA_CACHE_TTL = 60.0


def is_ollama_available() -> bool:
    """Return True only if Ollama is configured and reachable. No connection attempts if disabled."""
    global _ollama_available_cache, _ollama_available_cache_time
    if not getattr(settings, 'OLLAMA_ENABLED', True):
        return False
    base_url = (getattr(settings, 'OLLAMA_BASE_URL', None) or '').strip()
    if not base_url:
        return False
    now = time.time()
    if _ollama_available_cache is not None and (now - _ollama_available_cache_time) < _OLLAMA_CACHE_TTL:
        return _ollama_available_cache
    try:
        verify_ssl = getattr(settings, 'OLLAMA_VERIFY_SSL', False)
        r = requests.get(f"{base_url}/api/tags", timeout=5, verify=verify_ssl)
        _ollama_available_cache = r.status_code == 200
    except Exception as e:
        logger.debug("Ollama availability check failed: %s", e)
        _ollama_available_cache = False
    _ollama_available_cache_time = now
    return _ollama_available_cache


class OllamaClient:
    """Client for interacting with Ollama API."""
    
    def __init__(self):
        """Initialize Ollama client."""
        self.base_url = getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')
        self.default_model = getattr(settings, 'OLLAMA_DEFAULT_MODEL', 'llama2')
        self.timeout = getattr(settings, 'OLLAMA_TIMEOUT', 600)
        self.max_retries = getattr(settings, 'OLLAMA_MAX_RETRIES', 2)
        self.verify_ssl = getattr(settings, 'OLLAMA_VERIFY_SSL', False)

    def _get_keep_alive(self) -> str:
        return getattr(settings, 'OLLAMA_KEEP_ALIVE', '30m')

    def _get_model_options(self, model_name: str) -> Dict[str, Any]:
        """Build Ollama runtime options tuned for production inference."""
        options: Dict[str, Any] = {
            'temperature': getattr(settings, 'OLLAMA_TEMPERATURE', 0.7),
            'num_ctx': getattr(settings, 'OLLAMA_NUM_CTX', 4096),
            'num_predict': getattr(settings, 'OLLAMA_NUM_PREDICT', 2048),
            'top_p': getattr(settings, 'OLLAMA_TOP_P', 0.9),
            'top_k': getattr(settings, 'OLLAMA_TOP_K', 40),
            'repeat_penalty': getattr(settings, 'OLLAMA_REPEAT_PENALTY', 1.1),
        }
        num_thread = getattr(settings, 'OLLAMA_NUM_THREAD', 0)
        if num_thread > 0:
            options['num_thread'] = num_thread
        num_batch = getattr(settings, 'OLLAMA_NUM_BATCH', 0)
        if num_batch > 0:
            options['num_batch'] = num_batch

        model_overrides = getattr(settings, 'OLLAMA_MODEL_OPTIONS', {}) or {}
        if model_name in model_overrides:
            options.update(model_overrides[model_name])

        model_lower = (model_name or '').lower()
        if '70b' in model_lower:
            options.setdefault('num_ctx', 4096)
            options.setdefault('num_predict', 2048)
            options.setdefault('num_batch', 512)
        elif ':3b' in model_lower or '3.2' in model_lower:
            options.setdefault('num_ctx', 8192)
            options.setdefault('num_predict', 4096)
            options.setdefault('num_batch', 1024)

        return options

    def _apply_runtime(self, payload: Dict[str, Any], model_name: str) -> Dict[str, Any]:
        """Attach keep_alive and inference options to an Ollama API payload."""
        enriched = dict(payload)
        enriched['options'] = self._get_model_options(model_name)
        enriched['keep_alive'] = self._get_keep_alive()
        return enriched

    def warmup_model(self, model: Optional[str] = None) -> bool:
        """Preload a model into Ollama memory to avoid cold-start latency."""
        model_name = model or self.default_model
        if not self._check_connection():
            return False
        try:
            payload = self._apply_runtime(
                {'model': model_name, 'prompt': ' ', 'stream': False},
                model_name,
            )
            payload['options'] = dict(payload.get('options', {}))
            payload['options']['num_predict'] = 1
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
            logger.info("Ollama model warmed up: %s", model_name)
            return True
        except Exception as exc:
            logger.warning("Ollama warmup failed for %s: %s", model_name, exc)
            return False
    
    def _check_connection(self) -> bool:
        """Check if Ollama server is available."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5, verify=self.verify_ssl)
            return response.status_code == 200
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Ollama connection check failed: {str(e)} (URL: {self.base_url})")
            return False
    
    def create_agent(self, agent_name: str, agent_config: Dict[str, Any]) -> Dict[str, Any]:
        """Create an agent (Ollama doesn't require explicit agent creation)."""
        # Ollama uses models directly, so we just return agent info
        model = agent_config.get('model', self.default_model)
        return {
            'agent_id': f"ollama_{agent_name}",
            'model': model,
            'status': 'created',
            'provider': 'ollama'
        }
    
    def upload_training_data(self, agent_id: str, training_data: list) -> Dict[str, Any]:
        """Upload training data (Ollama uses system prompts for context)."""
        # In Ollama, training data is typically provided as context in the prompt
        # or through system messages. We store it for use in queries.
        return {
            'status': 'uploaded',
            'data_count': len(training_data) if isinstance(training_data, list) else 1,
            'provider': 'ollama'
        }
    
    def train_agent(self, agent_id: str) -> Dict[str, Any]:
        """Train agent (Ollama models are pre-trained, this is a no-op)."""
        # Ollama models are already trained, but we can fine-tune if needed
        # For now, we'll just mark as ready
        return {
            'status': 'completed',
            'agent_id': agent_id,
            'message': 'Ollama models are pre-trained and ready to use',
            'provider': 'ollama'
        }
    
    def get_training_status(self, agent_id: str) -> Dict[str, Any]:
        """Get training status."""
        return {
            'status': 'completed',
            'progress': 100,
            'provider': 'ollama'
        }
    
    def invoke_agent_stream(self, agent_id: str, query: str, context: Optional[Dict] = None,
                           model: Optional[str] = None, system_prompt: Optional[str] = None,
                           training_data: Optional[list] = None):
        """Invoke Ollama agent with streaming response (generator).
        
        Yields:
            dict with 'content' (str) for each chunk and 'done' (bool) when complete
        """
        if not self._check_connection():
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Ollama connection failed. Base URL: {self.base_url}, Verify SSL: {self.verify_ssl}")
            raise Exception(f"Ollama server is not available at {self.base_url}. Make sure Ollama is running and accessible.")
        
        # Use model from parameter or default
        model_name = model or self.default_model
        
        # Check if model is available
        available_models = self.list_models()
        if model_name not in available_models and available_models:
            model_name = available_models[0]
        
        # Build user content with training data and context
        user_content = query
        context_parts = []
        
        # Add training data to context
        if training_data:
            training_context = "Training Data/Knowledge Base:\n"
            for idx, data_item in enumerate(training_data, 1):
                if isinstance(data_item, dict):
                    content = data_item.get('content', {})
                    if isinstance(content, dict):
                        text_content = content.get('text', content.get('content', str(content)))
                    else:
                        text_content = str(content)
                    training_context += f"\n[{idx}] {text_content}\n"
                else:
                    training_context += f"\n[{idx}] {str(data_item)}\n"
            context_parts.append(training_context)
        
        # Add additional context if provided
        if context:
            context_str = json.dumps(context, indent=2)
            context_parts.append(f"Additional Context: {context_str}")
        
        # Combine context parts with query
        if context_parts:
            user_content = "\n".join(context_parts) + "\n\n" + query
        
        if system_prompt:
            # Use chat API format with streaming
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_content}
            ]
            
            payload = self._apply_runtime({
                'model': model_name,
                'messages': messages,
                'stream': True,
            }, model_name)
            
            try:
                response = requests.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout,
                    stream=True,
                    verify=self.verify_ssl
                )
                response.raise_for_status()
                
                # Stream the response
                for line in response.iter_lines():
                    if line:
                        try:
                            chunk = json.loads(line)
                            content = chunk.get('message', {}).get('content', '')
                            done = chunk.get('done', False)
                            
                            if content:
                                yield {
                                    'content': content,
                                    'done': False
                                }
                            
                            if done:
                                yield {
                                    'content': '',
                                    'done': True
                                }
                                break
                        except json.JSONDecodeError:
                            continue
            except requests.exceptions.RequestException as e:
                raise Exception(f"Failed to stream from Ollama: {str(e)}")
        else:
            # Fallback to generate API with streaming
            prompt = user_content
            payload = self._apply_runtime({
                'model': model_name,
                'prompt': prompt,
                'stream': True,
            }, model_name)
            
            if context:
                payload['context'] = context
            
            try:
                response = requests.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    timeout=self.timeout,
                    stream=True,
                    verify=self.verify_ssl
                )
                response.raise_for_status()
                
                for line in response.iter_lines():
                    if line:
                        try:
                            chunk = json.loads(line)
                            content = chunk.get('response', '')
                            done = chunk.get('done', False)
                            
                            if content:
                                yield {
                                    'content': content,
                                    'done': False
                                }
                            
                            if done:
                                yield {
                                    'content': '',
                                    'done': True
                                }
                                break
                        except json.JSONDecodeError:
                            continue
            except requests.exceptions.RequestException as e:
                raise Exception(f"Failed to stream from Ollama: {str(e)}")
    
    def invoke_agent(self, agent_id: str, query: str, context: Optional[Dict] = None, 
                     model: Optional[str] = None, system_prompt: Optional[str] = None,
                     training_data: Optional[list] = None) -> Dict[str, Any]:
        """Invoke Ollama agent to get a response."""
        if not self._check_connection():
            raise Exception("Ollama server is not available. Make sure Ollama is running.")
        
        # Use model from parameter or default
        model_name = model or self.default_model
        
        # Check if model is available, if not try to use first available model
        available_models = self.list_models()
        if model_name not in available_models and available_models:
            # Use first available model if specified model not found
            model_name = available_models[0]
        
        # Prepare the prompt with system prompt and training data if provided
        # Ollama uses a chat-like format for better results
        
        # Build user content with training data and context
        user_content = query
        context_parts = []
        
        # Add training data to context
        if training_data:
            training_context = "Training Data/Knowledge Base:\n"
            for idx, data_item in enumerate(training_data, 1):
                if isinstance(data_item, dict):
                    content = data_item.get('content', {})
                    if isinstance(content, dict):
                        text_content = content.get('text', content.get('content', str(content)))
                    else:
                        text_content = str(content)
                    training_context += f"\n[{idx}] {text_content}\n"
                else:
                    training_context += f"\n[{idx}] {str(data_item)}\n"
            context_parts.append(training_context)
        
        # Add additional context if provided
        if context:
            import json
            context_str = json.dumps(context, indent=2)
            context_parts.append(f"Additional Context: {context_str}")
        
        # Combine context parts with query
        if context_parts:
            user_content = "\n".join(context_parts) + "\n\n" + query
        
        if system_prompt:
            # OPTIMIZATION: Limit system prompt and user content length
            # Most models work best with concise prompts
            max_system_prompt_length = 2000  # characters
            max_user_content_length = 6000  # characters
            
            if len(system_prompt) > max_system_prompt_length:
                logger.debug(f"[LLM Optimization] Truncating system prompt from {len(system_prompt)} to {max_system_prompt_length} chars")
                system_prompt = system_prompt[:max_system_prompt_length] + "... [truncated]"
            
            if len(user_content) > max_user_content_length:
                logger.debug(f"[LLM Optimization] Truncating user content from {len(user_content)} to {max_user_content_length} chars")
                # Try to keep the actual query at the end
                if "\n\n" in user_content:
                    parts = user_content.rsplit("\n\n", 1)
                    if len(parts) == 2:
                        query_part = parts[1]
                        context_part = parts[0]
                        # Truncate context but keep query
                        available_for_context = max_user_content_length - len(query_part) - 10
                        if len(context_part) > available_for_context:
                            context_part = context_part[:available_for_context] + "... [truncated]"
                        user_content = f"{context_part}\n\n{query_part}"
                    else:
                        user_content = user_content[:max_user_content_length] + "... [truncated]"
                else:
                    user_content = user_content[:max_user_content_length] + "... [truncated]"
            
            # Use chat API format for better results with system prompts
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_content}
            ]
            
            # Log prompt size for debugging
            total_prompt_size = len(system_prompt) + len(user_content)
            logger.debug(f"[LLM Performance] Sending prompt to Ollama: {total_prompt_size} chars (~{total_prompt_size // 4} tokens)")
            
            # Try chat API first (better for models that support it)
            # Retry logic for transient failures
            last_exception = None
            for attempt in range(self.max_retries + 1):
                try:
                    payload = self._apply_runtime({
                        'model': model_name,
                        'messages': messages,
                        'stream': False,
                    }, model_name)
                    
                    response = requests.post(
                        f"{self.base_url}/api/chat",
                        json=payload,
                        timeout=self.timeout,
                        verify=self.verify_ssl
                    )
                    response.raise_for_status()
                    
                    result = response.json()
                    return {
                        'response': result.get('message', {}).get('content', ''),
                        'agent_id': agent_id,
                        'model': model_name,
                        'provider': 'ollama',
                        'done': result.get('done', True)
                    }
                except requests.exceptions.Timeout as e:
                    last_exception = e
                    if attempt < self.max_retries:
                        continue  # Retry on timeout
                    raise Exception(
                        f"Ollama request timed out after {self.timeout} seconds. "
                        f"The model may be processing a long response. "
                        f"Try increasing OLLAMA_TIMEOUT in settings (current: {self.timeout}s) or use a faster model."
                    )
                except requests.exceptions.ConnectionError as e:
                    last_exception = e
                    if attempt < self.max_retries:
                        continue  # Retry on connection error
                    raise Exception(
                        f"Cannot connect to Ollama server at {self.base_url}. "
                        f"Make sure Ollama is running: 'ollama serve' or check if the service is accessible."
                    )
                except requests.exceptions.RequestException as e:
                    # For other errors, don't retry, fall back to generate API
                    last_exception = e
                    break
            # If chat API failed with non-retryable error, fall through to generate API
        
        # Fallback to generate API format
        prompt = user_content  # Use the user_content that includes training data and context
        if system_prompt:
            prompt = f"{system_prompt}\n\nUser: {user_content}\nAssistant:"
        
        # Prepare request payload for generate API
        payload = self._apply_runtime({
            'model': model_name,
            'prompt': prompt,
            'stream': False,
        }, model_name)
        
        if context:
            payload['context'] = context
        
        # Retry logic for generate API
        last_exception = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    timeout=self.timeout,
                    verify=self.verify_ssl
                )
                response.raise_for_status()
                
                result = response.json()
                return {
                    'response': result.get('response', ''),
                    'agent_id': agent_id,
                    'model': model_name,
                    'provider': 'ollama',
                    'done': result.get('done', True)
                }
            except requests.exceptions.Timeout as e:
                last_exception = e
                if attempt < self.max_retries:
                    continue  # Retry on timeout
                raise Exception(
                    f"Ollama request timed out after {self.timeout} seconds. "
                    f"The model may be processing a long response. "
                    f"Try increasing OLLAMA_TIMEOUT in settings (current: {self.timeout}s) or use a faster model."
                )
            except requests.exceptions.ConnectionError as e:
                last_exception = e
                if attempt < self.max_retries:
                    continue  # Retry on connection error
                raise Exception(
                    f"Cannot connect to Ollama server at {self.base_url}. "
                    f"Make sure Ollama is running: 'ollama serve' or check if the service is accessible."
                )
            except requests.exceptions.RequestException as e:
                last_exception = e
                # Don't retry for other HTTP errors (4xx, 5xx)
                error_detail = ""
                try:
                    if hasattr(e, 'response') and e.response is not None:
                        try:
                            error_response = e.response.json()
                            error_detail = f" - {error_response.get('error', '')}"
                        except:
                            error_detail = f" - {e.response.text[:200]}"
                except:
                    pass
                raise Exception(f"Failed to invoke Ollama agent: {str(e)}{error_detail}")
        
        # If we exhausted retries, raise the last exception
        if last_exception:
            raise Exception(f"Failed to invoke Ollama agent after {self.max_retries + 1} attempts: {str(last_exception)}")
    
    def test_agent(self, agent_id: str, query: str, model: Optional[str] = None, training_data: Optional[list] = None, system_prompt: Optional[str] = None) -> Dict[str, Any]:
        """Test agent with a query."""
        return self.invoke_agent(agent_id, query, model=model, training_data=training_data, system_prompt=system_prompt)
    
    def get_agent_status(self, agent_id: str) -> Dict[str, Any]:
        """Get agent status."""
        is_available = self._check_connection()
        return {
            'status': 'active' if is_available else 'unavailable',
            'agent_id': agent_id,
            'provider': 'ollama',
            'ollama_available': is_available
        }
    
    def list_models(self) -> list:
        """List available Ollama models."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5, verify=self.verify_ssl)
            response.raise_for_status()
            data = response.json()
            return [model['name'] for model in data.get('models', [])]
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error listing Ollama models: {str(e)}")
            return []


def get_ollama_client():
    """Get an Ollama client instance, or None if Ollama is not configured or not reachable."""
    if not is_ollama_available():
        return None
    return OllamaClient()

