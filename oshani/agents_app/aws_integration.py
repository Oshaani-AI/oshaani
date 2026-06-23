"""AWS integration for Amazon Bedrock with Ollama fallback."""
import boto3
import json
import logging
from django.conf import settings
from botocore.exceptions import ClientError
from .ollama_integration import OllamaClient, is_ollama_available

logger = logging.getLogger(__name__)


class BedrockClient:
    """Client for interacting with Amazon Bedrock with Ollama fallback."""
    
    def __init__(self, use_ollama_fallback=True):
        """Initialize Bedrock client with optional Ollama fallback.
        
        Authentication priority:
        1. Explicit credentials (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
        2. IAM role (if running on EC2/ECS/Lambda)
        3. Environment variables
        4. AWS credentials file (~/.aws/credentials)
        5. Fall back to Ollama if all fail
        """
        self.use_ollama = False
        self.ollama_client = None
        
        # Try to initialize AWS session
        try:
            # Check if we're running on EC2 (IAM role available)
            # Prefer IAM role over explicit credentials when on EC2
            is_ec2 = False
            ec2_region = None
            try:
                import urllib.request
                # Check if EC2 metadata service is accessible
                response = urllib.request.urlopen('http://169.254.169.254/latest/meta-data/', timeout=2)
                if response.status == 200:
                    is_ec2 = True
                    # Try multiple methods to get the EC2 instance's region
                    # Method 1: Try placement/region endpoint
                    try:
                        region_response = urllib.request.urlopen('http://169.254.169.254/latest/meta-data/placement/region', timeout=2)
                        if region_response.status == 200:
                            ec2_region = region_response.read().decode('utf-8').strip()
                            logger.info(f"Detected EC2 region from metadata: {ec2_region}")
                    except Exception:
                        # Method 2: Try availability zone and extract region
                        try:
                            az_response = urllib.request.urlopen('http://169.254.169.254/latest/meta-data/placement/availability-zone', timeout=2)
                            if az_response.status == 200:
                                az = az_response.read().decode('utf-8').strip()
                                # Extract region from AZ (e.g., "us-east-1a" -> "us-east-1")
                                ec2_region = az[:-1] if len(az) > 1 else None
                                if ec2_region:
                                    logger.info(f"Detected EC2 region from availability zone: {ec2_region}")
                        except Exception:
                            pass
                    
                    # Method 3: Use instance identity document (requires token)
                    if not ec2_region:
                        try:
                            # Get instance identity document (requires IMDSv2 token)
                            import requests
                            # First get token for IMDSv2
                            token_response = requests.put(
                                'http://169.254.169.254/latest/api/token',
                                headers={'X-aws-ec2-metadata-token-ttl-seconds': '21600'},
                                timeout=2
                            )
                            if token_response.status_code == 200:
                                token = token_response.text
                                # Now get identity document with token
                                identity_response = requests.get(
                                    'http://169.254.169.254/latest/dynamic/instance-identity/document',
                                    headers={'X-aws-ec2-metadata-token': token},
                                    timeout=2
                                )
                                if identity_response.status_code == 200:
                                    identity_doc = identity_response.json()
                                    ec2_region = identity_doc.get('region')
                                    if ec2_region:
                                        logger.info(f"Detected EC2 region from instance identity: {ec2_region}")
                        except Exception as e:
                            logger.debug(f"Could not retrieve EC2 region from instance identity: {str(e)}")
                    
                    # Method 4: Use boto3's automatic region detection when IAM role is used
                    if not ec2_region:
                        try:
                            # Create a temporary session - boto3 will auto-detect region from EC2 metadata
                            temp_session = boto3.Session()
                            # Try to get region from the session's region_name
                            # If it's None, boto3 will use the region from the instance metadata
                            if temp_session.region_name:
                                ec2_region = temp_session.region_name
                                logger.info(f"Detected EC2 region from boto3 session: {ec2_region}")
                            else:
                                # Try to get region by making a call that will use instance metadata
                                try:
                                    sts = temp_session.client('sts')
                                    # This will use the instance's region automatically
                                    identity = sts.get_caller_identity()
                                    # The region is determined by the endpoint used
                                    # We can infer it from the response or use a default
                                    logger.debug("Using boto3's automatic region detection for IAM role")
                                except Exception:
                                    pass
                        except Exception as e:
                            logger.debug(f"Could not get region from boto3 session: {str(e)}")
            except Exception:
                pass
            
            # Determine which region to use
            # Priority: 1. Settings override, 2. EC2 instance region, 3. Default from settings
            region_to_use = None
            
            # First, check if region is explicitly set in settings (allows override)
            if hasattr(settings, 'AWS_REGION') and settings.AWS_REGION:
                configured_region = settings.AWS_REGION.strip()
                if configured_region:
                    region_to_use = configured_region
                    logger.info(f"Using configured region from settings: {region_to_use}")
            
            # If no region in settings, try EC2 instance region (if on EC2)
            if not region_to_use and is_ec2 and ec2_region:
                # Use EC2 instance's region when IAM role is in use
                region_to_use = ec2_region
                logger.info(f"Using EC2 instance region: {region_to_use} (IAM role detected)")
            
            # Final fallback: use default from settings (if not already set)
            if not region_to_use:
                region_to_use = getattr(settings, 'AWS_REGION', 'us-east-1')
                logger.info(f"Using default region: {region_to_use}")
            
            # Initialize boto3 session - boto3 will automatically use:
            # 1. IAM role if running on EC2/ECS/Lambda (preferred on EC2)
            # 2. Explicit credentials if provided (and not on EC2)
            # 3. Environment variables
            # 4. AWS credentials file
            if is_ec2:
                # On EC2, explicitly avoid using invalid credentials from env vars or settings
                # Force boto3 to use IAM role by temporarily clearing invalid credentials
                import os
                old_access_key = os.environ.pop('AWS_ACCESS_KEY_ID', None)
                old_secret_key = os.environ.pop('AWS_SECRET_ACCESS_KEY', None)
                old_session_token = os.environ.pop('AWS_SESSION_TOKEN', None)
                
                # Also check if the credentials in env are placeholders
                if old_access_key and old_access_key.startswith('your_'):
                    logger.info("Clearing placeholder credentials from environment to use IAM role")
                elif old_access_key:
                    # Restore if they look valid
                    os.environ['AWS_ACCESS_KEY_ID'] = old_access_key
                    if old_secret_key:
                        os.environ['AWS_SECRET_ACCESS_KEY'] = old_secret_key
                    if old_session_token:
                        os.environ['AWS_SESSION_TOKEN'] = old_session_token
                    old_access_key = old_secret_key = old_session_token = None
                
                try:
                    # Create session without credentials - will use IAM role
                    # Use EC2 instance's region
                    self.session = boto3.Session(region_name=region_to_use)
                    logger.info(f"Using IAM role (EC2 instance detected) with region: {region_to_use}")
                finally:
                    # Restore env vars only if they weren't placeholders
                    if old_access_key and not old_access_key.startswith('your_'):
                        os.environ['AWS_ACCESS_KEY_ID'] = old_access_key
                    if old_secret_key and not (old_secret_key and old_secret_key.startswith('your_')):
                        os.environ['AWS_SECRET_ACCESS_KEY'] = old_secret_key
                    if old_session_token:
                        os.environ['AWS_SESSION_TOKEN'] = old_session_token
            elif (settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY and
                  settings.AWS_ACCESS_KEY_ID.strip() and settings.AWS_SECRET_ACCESS_KEY.strip() and
                  not settings.AWS_ACCESS_KEY_ID.startswith('your_') and
                  not settings.AWS_SECRET_ACCESS_KEY.startswith('your_')):
                # Use explicit credentials from settings (when not on EC2 and not placeholders)
                self.session = boto3.Session(
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                    region_name=region_to_use
                )
                logger.info(f"Using explicit AWS credentials from settings with region: {region_to_use}")
            else:
                # Use default credential chain, but check if it found placeholder credentials
                # If so, force use of IAM role instead
                temp_session = boto3.Session(region_name=region_to_use)
                creds = temp_session.get_credentials()
                
                # Check if credentials are placeholders
                if creds and creds.access_key and creds.access_key.startswith('your_'):
                    logger.warning("Found placeholder credentials in credential chain, forcing IAM role usage")
                    # Force use of instance metadata (IAM role) by temporarily clearing env vars
                    # and creating a session that will use IAM role
                    import os
                    old_access_key = os.environ.pop('AWS_ACCESS_KEY_ID', None)
                    old_secret_key = os.environ.pop('AWS_SECRET_ACCESS_KEY', None)
                    old_session_token = os.environ.pop('AWS_SESSION_TOKEN', None)
                    old_profile = os.environ.pop('AWS_PROFILE', None)
                    
                    try:
                        # Create session without the placeholder credentials
                        # This will force boto3 to use IAM role from instance metadata
                        self.session = boto3.Session(region_name=region_to_use)
                        # Verify it's using IAM role by checking credentials
                        new_creds = self.session.get_credentials()
                        if new_creds and new_creds.access_key and not new_creds.access_key.startswith('your_'):
                            logger.info(f"Using IAM role (forced due to placeholder credentials) with region: {region_to_use}")
                        else:
                            logger.warning("Still using placeholder credentials, IAM role may not be available")
                            self.session = temp_session
                    finally:
                        # Don't restore placeholder credentials
                        if old_access_key and not old_access_key.startswith('your_'):
                            os.environ['AWS_ACCESS_KEY_ID'] = old_access_key
                        if old_secret_key and not (old_secret_key and old_secret_key.startswith('your_')):
                            os.environ['AWS_SECRET_ACCESS_KEY'] = old_secret_key
                        if old_session_token:
                            os.environ['AWS_SESSION_TOKEN'] = old_session_token
                        if old_profile:
                            os.environ['AWS_PROFILE'] = old_profile
                else:
                    # Use default credential chain (includes IAM roles, env vars, credentials file)
                    # This will automatically use IAM role if available
                    self.session = temp_session
                    logger.info("Using default AWS credential chain (will use IAM role if available)")
            
            # Store the region being used for this session
            self.region = region_to_use
            
            # Try to verify credentials (optional - don't fail if this doesn't work)
            # Some IAM roles may have Bedrock permissions but not STS permissions
            # Also, temporary credentials from IAM roles might have timing issues
            self.aws_account_id = None
            try:
                # Try STS in the same region first
                sts_client = self.session.client('sts', region_name=region_to_use)
                identity = sts_client.get_caller_identity()
                self.aws_account_id = identity.get('Account')
                logger.info(f"AWS identity verified: {identity.get('Arn', 'Unknown')}")
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                # If it's a credential issue, try without specifying region (uses default)
                if error_code in ('InvalidClientTokenId', 'InvalidUserID.NotFound', 'AccessDenied'):
                    try:
                        sts_client = self.session.client('sts')  # Use default region
                        identity = sts_client.get_caller_identity()
                        self.aws_account_id = identity.get('Account')
                        logger.info(f"AWS identity verified (using default region): {identity.get('Arn', 'Unknown')}")
                    except Exception:
                        # Credential verification failed, but continue anyway
                        # Bedrock might still work even if STS doesn't
                        logger.debug(f"Could not verify AWS identity via STS (non-blocking): {error_code}")
                        logger.info("Continuing with Bedrock initialization - credentials will be verified with actual Bedrock calls")
                else:
                    logger.debug(f"STS verification failed (non-blocking): {error_code}")
                    logger.info("Continuing with Bedrock initialization")
            except Exception as e:
                # Any other exception - log at debug level and continue
                logger.debug(f"Could not verify AWS identity (non-blocking): {str(e)}")
                logger.info("Continuing with Bedrock initialization - will verify with actual Bedrock calls")
            
            # Initialize Bedrock client for AI agent functionality
            # This is the real test - if this fails, credentials are definitely not working
            try:
                self.bedrock_client = self.session.client('bedrock-runtime', region_name=region_to_use)
                # Also initialize bedrock client for agent management
                self.bedrock_agent_client = self.session.client('bedrock', region_name=region_to_use)
                logger.info(f"Bedrock clients initialized successfully with region: {region_to_use}")
            except Exception as e:
                logger.error(f"Failed to initialize Bedrock clients: {str(e)}")
                # If client creation fails, fall back to Ollama only when configured and reachable
                if use_ollama_fallback and is_ollama_available():
                    logger.info("Falling back to Ollama due to Bedrock initialization failure")
                    self.use_ollama = True
                    self.ollama_client = OllamaClient()
                    return
                raise
            
            # Get default Bedrock model from settings
            self.default_bedrock_model = getattr(settings, 'BEDROCK_DEFAULT_MODEL', 'anthropic.claude-v2')
            
            # Track credential refresh attempts to prevent infinite loops
            self._credential_refresh_attempts = 0
            self._max_refresh_attempts = 3
            
        except Exception as e:
            # If any initialization fails, fall back to Ollama only when configured and reachable
            logger.error(f"Bedrock initialization failed: {str(e)}")
            if use_ollama_fallback and is_ollama_available():
                logger.info("Falling back to Ollama due to initialization error")
                self.use_ollama = True
                self.ollama_client = OllamaClient()
            else:
                raise
    
    def _refresh_credentials(self):
        """Refresh AWS credentials by creating a new session and clients.
        
        This is useful when IAM role credentials expire (they expire after 6 hours).
        """
        if not hasattr(self, '_credential_refresh_attempts'):
            self._credential_refresh_attempts = 0
        
        if self._credential_refresh_attempts >= self._max_refresh_attempts:
            logger.error(f"Maximum credential refresh attempts ({self._max_refresh_attempts}) reached. Stopping refresh attempts.")
            return False
        
        self._credential_refresh_attempts += 1
        logger.info(f"Refreshing AWS credentials (attempt {self._credential_refresh_attempts}/{self._max_refresh_attempts})")
        
        try:
            # Get current region
            region_to_use = getattr(self, 'region', getattr(settings, 'AWS_REGION', 'us-east-1'))
            
            # Check if we're on EC2
            from .aws_utils import is_ec2_instance
            is_ec2 = is_ec2_instance()
            
            # Create a new session - this will automatically refresh credentials
            # For IAM roles, boto3 will fetch new temporary credentials from instance metadata
            if is_ec2:
                # On EC2, create session without explicit credentials to use IAM role
                import os
                old_access_key = os.environ.pop('AWS_ACCESS_KEY_ID', None)
                old_secret_key = os.environ.pop('AWS_SECRET_ACCESS_KEY', None)
                old_session_token = os.environ.pop('AWS_SESSION_TOKEN', None)
                
                try:
                    new_session = boto3.Session(region_name=region_to_use)
                    logger.info("Created new session for credential refresh (using IAM role)")
                finally:
                    # Restore env vars if they weren't placeholders
                    if old_access_key and not old_access_key.startswith('your_'):
                        os.environ['AWS_ACCESS_KEY_ID'] = old_access_key
                    if old_secret_key and not (old_secret_key and old_secret_key.startswith('your_')):
                        os.environ['AWS_SECRET_ACCESS_KEY'] = old_secret_key
                    if old_session_token:
                        os.environ['AWS_SESSION_TOKEN'] = old_session_token
            else:
                # Not on EC2, use default credential chain
                new_session = boto3.Session(region_name=region_to_use)
                logger.info("Created new session for credential refresh (using default credential chain)")
            
            # Verify new credentials work
            try:
                sts_client = new_session.client('sts', region_name=region_to_use)
                identity = sts_client.get_caller_identity()
                logger.info(f"New credentials verified: {identity.get('Arn', 'Unknown')}")
            except Exception as e:
                logger.warning(f"Could not verify new credentials: {str(e)}")
                # Continue anyway - the actual Bedrock call will fail if credentials are invalid
            
            # Update session and recreate Bedrock clients
            self.session = new_session
            self.bedrock_client = self.session.client('bedrock-runtime', region_name=region_to_use)
            self.bedrock_agent_client = self.session.client('bedrock', region_name=region_to_use)
            
            # Reset refresh attempts counter on success
            self._credential_refresh_attempts = 0
            
            logger.info("Credentials refreshed successfully. Bedrock clients recreated.")
            return True
            
        except Exception as e:
            logger.error(f"Failed to refresh credentials: {str(e)}", exc_info=True)
            return False
    
    def _get_account_id(self):
        """Get AWS account ID from STS or return cached value."""
        # Return cached value if available
        if hasattr(self, 'aws_account_id') and self.aws_account_id:
            return self.aws_account_id
        
        # Try to get from STS
        try:
            if not hasattr(self, 'session'):
                return None
            # Use the region from the session (which may be EC2 region or configured region)
            region = getattr(self, 'region', settings.AWS_REGION)
            sts_client = self.session.client('sts', region_name=region)
            identity = sts_client.get_caller_identity()
            account_id = identity.get('Account')
            self.aws_account_id = account_id  # Cache it
            logger.info(f"Retrieved AWS Account ID: {account_id}")
            return account_id
        except Exception as e:
            logger.warning(f"Unable to get AWS account ID: {str(e)}")
            return None
    
    def create_agent(self, agent_name, agent_config):
        """Create an agent using Bedrock or Ollama."""
        # Use Ollama if AWS is not configured
        if self.use_ollama and self.ollama_client:
            return self.ollama_client.create_agent(agent_name, agent_config)
        
        try:
            # Bedrock agents are created through the Bedrock console or API
            # For now, we'll create a logical agent representation
            agent_id = f"bedrock_{agent_name}".replace(' ', '-').lower()
            
            # Get model from config or use default
            model = agent_config.get('model', self.default_bedrock_model)
            
            response = {
                'agent_id': agent_id,
                'status': 'created',
                'name': agent_name,
                'model': model,
                'provider': 'bedrock'
            }
            return response
        except (ClientError, Exception) as e:
            # Fall back to Ollama on error
            if self.ollama_client:
                try:
                    return self.ollama_client.create_agent(agent_name, agent_config)
                except Exception:
                    pass
            
            error_code = 'Unknown'
            error_message = str(e)
            if isinstance(e, ClientError):
                error_code = e.response.get('Error', {}).get('Code', 'Unknown')
                error_message = e.response.get('Error', {}).get('Message', str(e))
            raise Exception(f"Failed to create agent: {error_code} - {error_message}")
    
    def upload_training_data(self, agent_id, training_data):
        """Upload training data to an agent."""
        # Use Ollama if AWS is not configured
        if self.use_ollama and self.ollama_client:
            return self.ollama_client.upload_training_data(agent_id, training_data)
        
        try:
            # Placeholder for training data upload
            # In actual implementation, this would upload to Quick Suite knowledge base
            response = {
                'status': 'uploaded',
                'data_count': len(training_data) if isinstance(training_data, list) else 1,
                'provider': 'aws'
            }
            return response
        except (ClientError, Exception) as e:
            # Fall back to Ollama on error
            if self.ollama_client:
                try:
                    return self.ollama_client.upload_training_data(agent_id, training_data)
                except Exception:
                    pass
            raise Exception(f"Failed to upload training data: {str(e)}")
    
    def train_agent(self, agent_id):
        """Initiate agent training process."""
        # Use Ollama if AWS is not configured
        if self.use_ollama and self.ollama_client:
            return self.ollama_client.train_agent(agent_id)
        
        try:
            # Placeholder for training initiation
            response = {
                'status': 'training',
                'agent_id': agent_id,
                'provider': 'aws'
            }
            return response
        except (ClientError, Exception) as e:
            # Fall back to Ollama on error
            if self.ollama_client:
                try:
                    return self.ollama_client.train_agent(agent_id)
                except Exception:
                    pass
            raise Exception(f"Failed to start training: {str(e)}")
    
    def get_training_status(self, agent_id):
        """Get training status for an agent."""
        # Use Ollama if AWS is not configured
        if self.use_ollama and self.ollama_client:
            return self.ollama_client.get_training_status(agent_id)
        
        try:
            # Placeholder for training status check
            response = {
                'status': 'completed',
                'progress': 100,
                'provider': 'aws'
            }
            return response
        except (ClientError, Exception) as e:
            # Fall back to Ollama on error
            if self.ollama_client:
                try:
                    return self.ollama_client.get_training_status(agent_id)
                except Exception:
                    pass
            raise Exception(f"Failed to get training status: {str(e)}")
    
    def test_agent(self, agent_id, query, model=None, training_data=None, system_prompt=None, inference_profile_arn=None):
        """Send a test query to an agent using Bedrock.
        
        Args:
            agent_id: Agent identifier
            query: Test query string
            model: Model ID to use (required for Bedrock)
                   If None, will attempt to get from agent configuration
            training_data: Optional list of training data to include
            system_prompt: Optional system prompt to use (overrides agent configuration)
            inference_profile_arn: Optional ARN of inference profile for models requiring provisioning
        """
        # Validate model is provided - check for None, empty string, or whitespace
        if not model or not str(model).strip():
            raise ValueError(
                f"Model is required for agent testing. Agent must be configured with a specific LLM model. "
                f"Received model: {repr(model)}"
            )
        
        # Ensure model is a clean string
        model = str(model).strip()
        
        # Use Ollama if AWS is not configured
        if self.use_ollama and self.ollama_client:
            return self.ollama_client.test_agent(agent_id, query, model, training_data, system_prompt)
        
        try:
            # Use Bedrock for testing - pass model_provider='bedrock' to ensure correct client usage
            return self.invoke_agent(agent_id, query, model=model, model_provider='bedrock', training_data=training_data, system_prompt=system_prompt, inference_profile_arn=inference_profile_arn)
        except (ClientError, Exception) as e:
            # Fall back to Ollama on error
            if self.ollama_client:
                try:
                    return self.ollama_client.test_agent(agent_id, query, model, training_data, system_prompt)
                except Exception:
                    pass
            raise Exception(f"Failed to test agent: {str(e)}")
    
    def invoke_agent(self, agent_id, query, context=None, model=None, system_prompt=None, model_provider=None, training_data=None, tools_enabled=True, inference_profile_arn=None, user=None, agent_db_id=None, max_tokens=None):
        """
        Invoke Bedrock agent with optimized prompt handling.
        
        Note: Prompt size optimizations are applied in agent_loop.py before calling this method.
        
        Args:
            agent_id: Agent ID (may be quick_suite_agent_id or string representation)
            agent_db_id: Optional actual database ID for reliable agent lookup
            user: User instance for subscription checks (will use agent owner if different)
        """
        """Invoke a published agent using Amazon Bedrock.
        
        Uses Bedrock for AI responses, falls back to Ollama if AWS is not available.
        model_provider: 'bedrock' or 'ollama' - determines which client to use
        training_data: Optional list of training data to include in context
        tools_enabled: Whether to enable tool calling capabilities
        inference_profile_arn: Optional ARN of inference profile for models requiring provisioning
        user: User instance for subscription tier checks
        """
        # Check model provider restrictions if user is provided
        # For shared agents, use agent owner's subscription instead of current user's
        if user:
            try:
                from agents_app.platform_utils import can_use_bedrock, enforce_model_provider
                from .models import Agent
                
                # Determine which user's subscription to check
                # For shared agents, use agent owner's subscription instead of current user's
                subscription_user = user
                if agent_db_id:
                    # Use the actual DB ID if provided (most reliable)
                    # Always use agent owner's subscription for billing/access checks
                    try:
                        agent = Agent.objects.get(id=agent_db_id)
                        subscription_user = agent.user  # Always use agent owner for subscription checks
                        if agent.user != user:
                            logger.info(f"[Shared Agent] Using agent owner's ({agent.user.username}) subscription for shared agent access. Current user: {user.username}, Agent ID: {agent_db_id}")
                        else:
                            logger.debug(f"[Agent Owner] User is agent owner, using their subscription. Agent ID: {agent_db_id}")
                    except Agent.DoesNotExist:
                        logger.warning(f"Agent with ID {agent_db_id} not found, using passed user's subscription: {user.username}")
                elif agent_id:
                    try:
                        # agent_id might be a string (quick_suite_agent_id) or integer
                        # Try to find agent by id first, then by quick_suite_agent_id
                        agent = None
                        try:
                            agent_id_int = int(agent_id)
                            agent = Agent.objects.filter(id=agent_id_int).first()
                        except (ValueError, TypeError):
                            pass
                        
                        # If not found by id, try quick_suite_agent_id (not unique, so use first)
                        if not agent:
                            agent = Agent.objects.filter(quick_suite_agent_id=agent_id).first()
                        
                        # If agent found and user is not the owner, use agent owner's subscription
                        if agent and agent.user != user:
                            subscription_user = agent.user
                            logger.debug(f"Using agent owner's ({agent.user.username}) subscription for shared agent access. Current user: {user.username}")
                    except Exception as e:
                        # Agent not found or error, use original user
                        logger.debug(f"Could not find agent for ID {agent_id}: {str(e)}, using user's subscription")
                
                # If trying to use Bedrock, check if subscription_user has access
                if model_provider == 'bedrock' or (model_provider is None and not self.use_ollama):
                    can_use, reason = can_use_bedrock(subscription_user)
                    if not can_use:
                        # Log for debugging
                        logger.warning(f"Bedrock access check: user={subscription_user.username}, agent_db_id={agent_db_id}, can_use={can_use}, reason={reason}")
                        # Fall back to Ollama if available and reachable
                        if is_ollama_available():
                            logger.info(f"User {subscription_user.username} cannot use Bedrock: {reason}. Falling back to Ollama.")
                            if not self.ollama_client:
                                self.ollama_client = OllamaClient()
                            return self.ollama_client.invoke_agent(agent_id, query, context, model, system_prompt, training_data)
                        raise ValueError(f"Bedrock access denied: {reason}")
                
                # Enforce model provider restrictions
                if model_provider:
                    allowed, reason, fallback = enforce_model_provider(subscription_user, model_provider)
                    if not allowed:
                        if fallback:
                            logger.info(f"User {subscription_user.username} cannot use {model_provider}: {reason}. Using {fallback} instead.")
                            model_provider = fallback
                        else:
                            raise ValueError(f"Model provider access denied: {reason}")
            except ImportError:
                # billing_app not available, skip restrictions
                pass
        
        # If model_provider is specified, use the appropriate client
        if model_provider == 'ollama':
            if not is_ollama_available():
                raise ValueError(
                    "Ollama is not configured or not reachable. Set OLLAMA_ENABLED and OLLAMA_BASE_URL and ensure the server is running."
                )
            if self.ollama_client:
                return self.ollama_client.invoke_agent(agent_id, query, context, model, system_prompt, training_data)
            self.ollama_client = OllamaClient()
            return self.ollama_client.invoke_agent(agent_id, query, context, model, system_prompt, training_data)
        
        # Use Ollama if AWS is not configured
        if self.use_ollama and self.ollama_client:
            return self.ollama_client.invoke_agent(agent_id, query, context, model, system_prompt, training_data)
        
        # Set optimized defaults for fast responses
        # Lower max_tokens = faster responses (1024 is good balance between speed and completeness)
        if max_tokens is None:
            max_tokens = 1024  # Optimized for fast responses (was 4096)
        
        # Slightly lower temperature for faster, more deterministic responses
        temperature = 0.6  # Optimized for speed (was 0.7)
        
        try:
            # Use Bedrock for AI agent invocation
            if not hasattr(self, 'bedrock_client'):
                # Fall back to Ollama if Bedrock client is not available
                if self.ollama_client:
                    return self.ollama_client.invoke_agent(agent_id, query, context, model, system_prompt, training_data)
                raise Exception("Bedrock client is not available.")
            
            # Validate model is provided for Bedrock operations
            # Model is required when using Bedrock (not optional)
            if not model:
                raise ValueError("Model is required for agent invocation. Agent must be configured with a specific LLM model.")
            
            # Convert to string and strip whitespace
            bedrock_model = str(model).strip()
            logger.info(f"[Llama Debug] Initial model: {model}, bedrock_model: {bedrock_model}")
            if not bedrock_model:
                raise ValueError("Model cannot be empty. Agent must be configured with a valid LLM model.")
            
            # Normalize model ID - remove context window suffixes that Bedrock doesn't support
            # Model IDs like "anthropic.claude-v2:0:18k" or "anthropic.claude-3-haiku-20240307-v1:0:200k" 
            # should become "anthropic.claude-v2:0" or "anthropic.claude-3-haiku-20240307-v1:0"
            original_model = bedrock_model
            if bedrock_model.count(':') > 1:
                # Has multiple colons, likely has context window suffix (e.g., :200k, :18k)
                # Extract base model ID with version (remove the suffix)
                parts = bedrock_model.split(':')
                if len(parts) >= 2:
                    # Use base model with version (e.g., "anthropic.claude-3-haiku-20240307-v1:0")
                    # This is the correct format for Bedrock - version number after first colon
                    bedrock_model = ':'.join(parts[:2])
                    logger.info(f"Normalized model ID: {original_model} -> {bedrock_model} (removed context window suffix)")
                elif len(parts) > 0:
                    # Fallback to just base model if no version found
                    bedrock_model = parts[0]
                    logger.info(f"Normalized model ID: {original_model} -> {bedrock_model} (removed all suffixes)")
            
            # Prepare the prompt
            if system_prompt:
                full_prompt = f"{system_prompt}\n\nHuman: {query}\n\nAssistant:"
            else:
                full_prompt = f"Human: {query}\n\nAssistant:"
            
            # Add training data to context if provided
            training_context = ""
            if training_data:
                training_context = "\n\nTraining Data/Knowledge Base:\n"
                for idx, data_item in enumerate(training_data, 1):
                    if isinstance(data_item, dict):
                        # Extract content from training data
                        content = data_item.get('content', {})
                        if isinstance(content, dict):
                            # Try to get text content
                            text_content = content.get('text', content.get('content', str(content)))
                        else:
                            text_content = str(content)
                        training_context += f"\n[{idx}] {text_content}\n"
                    else:
                        training_context += f"\n[{idx}] {str(data_item)}\n"
            
            # Add context if provided
            context_parts = []
            if training_context:
                context_parts.append(training_context)
            if context:
                context_str = json.dumps(context, indent=2)
                context_parts.append(f"Additional Context: {context_str}")
            
            if context_parts:
                full_prompt = "\n".join(context_parts) + "\n\n" + full_prompt
            
            # Prepare request body based on model provider and version
            # Different models use different API formats
            
            # Check if this is an embedding model (not for text generation)
            if bedrock_model.startswith('amazon.titan-embed'):
                raise ValueError(
                    f"Model '{bedrock_model}' is an embedding model and cannot be used for text generation. "
                    f"Please select a text generation model for agent use."
                )
            
            if bedrock_model.startswith('anthropic.claude-3') or 'claude-3' in bedrock_model.lower():
                # Claude 3+ models use Messages API format
                messages = []
                
                # Add user query
                user_content = query
                if context:
                    context_str = json.dumps(context, indent=2)
                    user_content = f"Context: {context_str}\n\n{user_content}"
                
                messages.append({
                    "role": "user",
                    "content": [{"type": "text", "text": user_content}]
                })
                
                body_dict = {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": max_tokens,
                    "messages": messages,
                    "temperature": temperature,
                    "top_p": 0.9,
                }
                
                # Add system message if provided (some Claude 3 models support this)
                if system_prompt:
                    body_dict["system"] = [{"type": "text", "text": system_prompt}]
                
                body = json.dumps(body_dict)
            elif bedrock_model.startswith('anthropic.claude'):
                # Claude 2 models use prompt format
                body = json.dumps({
                    "prompt": full_prompt,
                    "max_tokens_to_sample": max_tokens,
                    "temperature": temperature,
                    "top_p": 0.9,
                })
            elif bedrock_model.startswith('meta.llama'):
                # Llama models - both Llama 2 and Llama 3 use prompt format for invoke_model API
                is_llama3 = 'llama3' in bedrock_model.lower() or 'llama-3' in bedrock_model.lower()
                logger.info(f"[Llama Debug] bedrock_model={bedrock_model}, is_llama3={is_llama3}")
                
                if is_llama3:
                    # Llama 3 on Bedrock - use simple prompt format
                    # Bedrock handles the special tokens internally for Llama 3 models
                    user_content = query
                    if context:
                        context_str = json.dumps(context, indent=2)
                        user_content = f"Context: {context_str}\n\n{user_content}"
                    
                    # Use simple Human/Assistant format - Bedrock applies Llama 3 formatting internally
                    if system_prompt:
                        llama3_prompt = f"{system_prompt}\n\nHuman: {user_content}\n\nAssistant:"
                    else:
                        llama3_prompt = f"Human: {user_content}\n\nAssistant:"
                    
                    body = json.dumps({
                        "prompt": llama3_prompt,
                        "max_gen_len": max_tokens,
                        "temperature": temperature,
                        "top_p": 0.9,
                    })
                    logger.info(f"[Llama Debug] Using Llama 3 simple format, prompt starts with: {llama3_prompt[:100]}...")
                else:
                    # Llama 2 uses standard prompt format
                    body = json.dumps({
                        "prompt": full_prompt,
                        "max_gen_len": max_tokens,
                        "temperature": temperature,
                        "top_p": 0.9,
                    })
            elif bedrock_model.startswith('ai21.j2'):
                # AI21 models use different format
                body = json.dumps({
                    "prompt": full_prompt,
                    "maxTokens": max_tokens,
                    "temperature": temperature,
                    "topP": 0.9,
                })
            elif bedrock_model.startswith('amazon.nova'):
                # Amazon Nova models use Messages API format with minimal parameters
                # Nova Lite has very strict parameter requirements:
                # - Only user messages (no system role)
                # - Content must be JSONArray format: [{"type": "text", "text": "..."}]
                # - Only messages array (no other parameters)
                messages = []
                
                # Build user content with system prompt and context if provided
                user_content = query
                if system_prompt:
                    user_content = f"{system_prompt}\n\n{user_content}"
                if context:
                    context_str = json.dumps(context, indent=2)
                    user_content = f"Context: {context_str}\n\n{user_content}"
                
                # Nova Lite only supports user role with content as JSONArray of objects
                if 'nova-lite' in bedrock_model.lower():
                    # Only user messages, content must be array of objects with "text" field
                    messages.append({
                        "role": "user",
                        "content": [{"text": user_content}]
                    })
                    body = json.dumps({
                        "messages": messages,
                    })
                else:
                    # Other Nova models may support system messages and temperature
                    if system_prompt:
                        messages.append({
                            "role": "system",
                            "content": system_prompt
                        })
                    messages.append({
                        "role": "user",
                        "content": user_content if not system_prompt else query
                    })
                    body = json.dumps({
                        "messages": messages,
                        "temperature": temperature,
                    })
            elif bedrock_model.startswith('deepseek.') or 'deepseek' in bedrock_model.lower():
                # DeepSeek models use Messages API format (similar to Claude 3)
                messages = []
                
                # Add system message if provided
                if system_prompt:
                    messages.append({
                        "role": "system",
                        "content": system_prompt
                    })
                
                # Add user query
                user_content = query
                if context:
                    context_str = json.dumps(context, indent=2)
                    user_content = f"Context: {context_str}\n\n{user_content}"
                
                messages.append({
                    "role": "user",
                    "content": user_content
                })
                
                body = json.dumps({
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "top_p": 0.9,
                })
            elif bedrock_model.startswith('openai.gpt-oss'):
                # OpenAI GPT-OSS models use Messages API format
                messages = []
                
                # Add system message if provided
                if system_prompt:
                    messages.append({
                        "role": "system",
                        "content": system_prompt
                    })
                
                # Add user query
                user_content = query
                if context:
                    context_str = json.dumps(context, indent=2)
                    user_content = f"Context: {context_str}\n\n{user_content}"
                
                messages.append({
                    "role": "user",
                    "content": user_content
                })
                
                body = json.dumps({
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                })
            elif bedrock_model.startswith('qwen.'):
                # Qwen models use Messages API format
                messages = []
                
                # Add system message if provided
                if system_prompt:
                    messages.append({
                        "role": "system",
                        "content": system_prompt
                    })
                
                # Add user query
                user_content = query
                if context:
                    context_str = json.dumps(context, indent=2)
                    user_content = f"Context: {context_str}\n\n{user_content}"
                
                messages.append({
                    "role": "user",
                    "content": user_content
                })
                
                body = json.dumps({
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                })
            else:
                # For unknown models, try Messages API format first (most modern models use this)
                # This is a fallback for models we don't recognize
                messages = []
                
                if system_prompt:
                    messages.append({
                        "role": "system",
                        "content": system_prompt
                    })
                
                user_content = query
                if context:
                    context_str = json.dumps(context, indent=2)
                    user_content = f"Context: {context_str}\n\n{user_content}"
                
                messages.append({
                    "role": "user",
                    "content": user_content
                })
                
                body = json.dumps({
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "top_p": 0.9,
                })
            
            # Invoke the model - use inference profile if provided, otherwise use model ID
            logger.info(f"[Llama Debug] Final bedrock_model: {bedrock_model}, body starts with: {body[:200]}...")
            try:
                invoke_params = {
                    'body': body,
                    'contentType': 'application/json',
                    'accept': 'application/json'
                }
                
                if inference_profile_arn:
                    # Use inference profile ARN as modelId for models requiring provisioning
                    # The invoke_model API accepts inference profile ARNs in the modelId parameter
                    invoke_params['modelId'] = inference_profile_arn
                    logger.info(f"Using inference profile ARN as modelId: {inference_profile_arn} for model {bedrock_model}")
                else:
                    # Use model ID directly (for ON_DEMAND models)
                    invoke_params['modelId'] = bedrock_model
                
                response = self.bedrock_client.invoke_model(**invoke_params)
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                error_message = e.response.get('Error', {}).get('Message', '')
                
                # Check if this is a credential/token expiration error
                credential_errors = ('UnrecognizedClientException', 'InvalidClientTokenId', 'InvalidUserID.NotFound', 
                                   'ExpiredTokenException', 'TokenRefreshRequired')
                is_credential_error = error_code in credential_errors or 'security token' in error_message.lower() or 'invalid token' in error_message.lower()
                
                # Try to refresh credentials if it's a credential error
                if is_credential_error:
                    logger.warning(f"Credential error detected ({error_code}): {error_message}")
                    logger.info("Attempting to refresh credentials and retry...")
                    
                    if self._refresh_credentials():
                        # Retry the invocation with refreshed credentials
                        try:
                            logger.info("Retrying Bedrock invocation with refreshed credentials...")
                            response = self.bedrock_client.invoke_model(**invoke_params)
                            logger.info("Successfully invoked Bedrock model after credential refresh")
                        except ClientError as retry_e:
                            # If retry also fails, fall through to original error handling
                            error_code = retry_e.response.get('Error', {}).get('Code', '')
                            error_message = retry_e.response.get('Error', {}).get('Message', '')
                            logger.error(f"Retry after credential refresh also failed: {error_code} - {error_message}")
                            # Continue with original error handling below
                        except Exception as retry_e:
                            logger.error(f"Unexpected error during retry: {str(retry_e)}")
                            raise
                    else:
                        logger.error("Failed to refresh credentials. Cannot retry request.")
                
                # If model not found/not supported and we have a model ID with context window suffix, try base model
                # Handle both ResourceNotFoundException and ValidationException (model not supported)
                is_model_error = (error_code == 'ResourceNotFoundException' or 
                                 (error_code == 'ValidationException' and 
                                  ('not supported' in error_message.lower() or 'model id' in error_message.lower())))
                
                if is_model_error and bedrock_model.count(':') > 1:
                    parts = bedrock_model.split(':')
                    # Try base model with version (e.g., "anthropic.claude-v2:0")
                    if len(parts) >= 2:
                        base_model_with_version = ':'.join(parts[:2])
                        logger.info(f"Model {bedrock_model} not found, trying base model: {base_model_with_version}")
                        try:
                            response = self.bedrock_client.invoke_model(
                                modelId=base_model_with_version,
                                body=body,
                                contentType='application/json',
                                accept='application/json'
                            )
                            bedrock_model = base_model_with_version  # Update for response parsing
                        except ClientError:
                            # Try just base model (e.g., "anthropic.claude-v2")
                            base_model = parts[0]
                            logger.info(f"Model {base_model_with_version} not found, trying base model: {base_model}")
                            try:
                                response = self.bedrock_client.invoke_model(
                                    modelId=base_model,
                                    body=body,
                                    contentType='application/json',
                                    accept='application/json'
                                )
                                bedrock_model = base_model  # Update for response parsing
                            except ClientError as e2:
                                # Re-raise with helpful error message
                                error_code2 = e2.response.get('Error', {}).get('Code', '')
                                error_message2 = e2.response.get('Error', {}).get('Message', str(e2))
                                raise Exception(
                                    f"Model '{bedrock_model}' not found or not supported in Bedrock. "
                                    f"Tried: {bedrock_model}, {base_model_with_version}, {base_model}. "
                                    f"Please check if the model is enabled in your AWS Bedrock console for region {getattr(self, 'region', 'unknown')}. "
                                    f"Error: {error_code2} - {error_message2}"
                                )
                    else:
                        raise
                else:
                    # Re-raise with helpful error message
                    error_message = e.response.get('Error', {}).get('Message', str(e))
                    
                    # Handle token limit errors specifically
                    is_token_limit_error = (
                        error_code == 'ValidationException' and 
                        ('maximum context length' in error_message.lower() or 
                         '8192 tokens' in error_message.lower() or
                         'context length' in error_message.lower() or
                         'exceeded' in error_message.lower() and 'token' in error_message.lower())
                    )
                    
                    if is_token_limit_error:
                        logger.error(f"Token limit exceeded for model {bedrock_model}: {error_message}")
                        # Try to log prompt size
                        try:
                            body_dict = json.loads(body)
                            if 'messages' in body_dict:
                                total_chars = sum(len(str(msg.get('content', ''))) for msg in body_dict['messages'])
                                if 'system' in body_dict:
                                    system_content = body_dict['system']
                                    if isinstance(system_content, list):
                                        total_chars += sum(len(str(item.get('text', ''))) for item in system_content)
                                    else:
                                        total_chars += len(str(system_content))
                                logger.error(f"  Prompt size was: {total_chars} chars (~{total_chars // 4} tokens)")
                        except Exception:
                            pass
                        raise Exception(
                            f"Failed to invoke Bedrock agent: Token limit exceeded. "
                            f"This model's maximum context length is 8192 tokens. "
                            f"Please reduce the length of the prompt by: "
                            f"1. Shortening conversation history "
                            f"2. Reducing RAG context "
                            f"3. Truncating system prompt "
                            f"4. Using a model with larger context window. "
                            f"Error: {error_code} - {error_message}"
                        )
                    
                    if error_code == 'ValidationException' and 'model id' in error_message.lower():
                        raise Exception(
                            f"Model '{bedrock_model}' is not supported in Bedrock. "
                            f"Please check if the model is enabled in your AWS Bedrock console for region {getattr(self, 'region', 'unknown')}. "
                            f"Error: {error_code} - {error_message}"
                        )
                    # For ModelErrorException, provide more helpful error message
                    if error_code == 'ModelErrorException':
                        logger.error(f"Bedrock ModelErrorException for model {bedrock_model}. This often indicates:")
                        logger.error("  - Prompt too large or contains invalid characters")
                        logger.error("  - Model-specific format issues")
                        logger.error("  - Temporary model unavailability")
                        # Try to log prompt size if possible
                        try:
                            body_dict = json.loads(body)
                            if 'messages' in body_dict:
                                total_chars = sum(len(str(msg.get('content', ''))) for msg in body_dict['messages'])
                                logger.error(f"  Prompt size was: {total_chars} chars (~{total_chars // 4} tokens)")
                        except Exception:
                            pass
                        raise Exception(
                            f"Bedrock model error for {bedrock_model}: {error_message}. "
                            f"This may be due to prompt size, format, or temporary model issues. "
                            f"Try reducing prompt size or retrying the request."
                        )
                    raise Exception(f"Failed to invoke Bedrock agent: {error_code} - {error_message}")
            
            # Parse the response
            response_body = json.loads(response['body'].read())
            
            # Extract the generated text based on model type and version
            if bedrock_model.startswith('anthropic.claude-3') or 'claude-3' in bedrock_model.lower():
                # Claude 3+ uses Messages API format
                # Response format: {"content": [{"type": "text", "text": "..."}], "stop_reason": "..."}
                if 'content' in response_body:
                    # New format with content array
                    content_items = response_body.get('content', [])
                    if content_items and len(content_items) > 0:
                        generated_text = content_items[0].get('text', '')
                    else:
                        generated_text = ''
                elif 'text' in response_body:
                    # Alternative format
                    generated_text = response_body.get('text', '')
                else:
                    generated_text = str(response_body)
            elif bedrock_model.startswith('amazon.nova'):
                # Amazon Nova models use Messages API format
                # Response format: {"output": {"message": {"content": [{"text": "..."}]}}}
                if 'output' in response_body:
                    output = response_body.get('output', {})
                    message = output.get('message', {})
                    content = message.get('content', [])
                    if content and len(content) > 0:
                        generated_text = content[0].get('text', '')
                    else:
                        generated_text = ''
                elif 'choices' in response_body and len(response_body.get('choices', [])) > 0:
                    generated_text = response_body['choices'][0].get('message', {}).get('content', '')
                elif 'content' in response_body:
                    # Try content array format
                    content_items = response_body.get('content', [])
                    if isinstance(content_items, list) and len(content_items) > 0:
                        if isinstance(content_items[0], dict):
                            generated_text = content_items[0].get('text', '') or content_items[0].get('content', '')
                        else:
                            generated_text = str(content_items[0])
                    else:
                        generated_text = str(response_body.get('content', ''))
                elif 'text' in response_body:
                    generated_text = response_body.get('text', '')
                else:
                    generated_text = str(response_body)
            elif bedrock_model.startswith('deepseek.') or 'deepseek' in bedrock_model.lower():
                # DeepSeek models use Messages API format
                # Response format: {"choices": [{"message": {"content": "..."}}]} or {"content": "..."}
                if 'choices' in response_body and len(response_body.get('choices', [])) > 0:
                    generated_text = response_body['choices'][0].get('message', {}).get('content', '')
                elif 'content' in response_body:
                    generated_text = response_body.get('content', '')
                elif 'text' in response_body:
                    generated_text = response_body.get('text', '')
                else:
                    generated_text = str(response_body)
            elif bedrock_model.startswith('openai.gpt-oss'):
                # OpenAI GPT-OSS models use Messages API format
                # Response format: {"choices": [{"message": {"content": "..."}}]} or {"content": "..."}
                if 'choices' in response_body and len(response_body.get('choices', [])) > 0:
                    choice = response_body['choices'][0]
                    if 'message' in choice:
                        generated_text = choice['message'].get('content', '')
                    elif 'text' in choice:
                        generated_text = choice.get('text', '')
                    else:
                        generated_text = str(choice)
                elif 'content' in response_body:
                    generated_text = response_body.get('content', '')
                elif 'text' in response_body:
                    generated_text = response_body.get('text', '')
                else:
                    generated_text = str(response_body)
            elif bedrock_model.startswith('qwen.'):
                # Qwen models use Messages API format
                # Response format: {"output": {"choices": [{"message": {"content": "..."}}]}} or {"choices": [...]}
                if 'output' in response_body:
                    output = response_body.get('output', {})
                    if 'choices' in output and len(output.get('choices', [])) > 0:
                        choice = output['choices'][0]
                        if 'message' in choice:
                            generated_text = choice['message'].get('content', '')
                        elif 'text' in choice:
                            generated_text = choice.get('text', '')
                        else:
                            generated_text = str(choice)
                    elif 'text' in output:
                        generated_text = output.get('text', '')
                    else:
                        generated_text = str(output)
                elif 'choices' in response_body and len(response_body.get('choices', [])) > 0:
                    choice = response_body['choices'][0]
                    if 'message' in choice:
                        generated_text = choice['message'].get('content', '')
                    elif 'text' in choice:
                        generated_text = choice.get('text', '')
                    else:
                        generated_text = str(choice)
                elif 'content' in response_body:
                    generated_text = response_body.get('content', '')
                elif 'text' in response_body:
                    generated_text = response_body.get('text', '')
                else:
                    generated_text = str(response_body)
            elif bedrock_model.startswith('anthropic.claude'):
                # Claude 2 uses completion format
                generated_text = response_body.get('completion', '')
            elif bedrock_model.startswith('meta.llama'):
                generated_text = response_body.get('generation', '')
            elif bedrock_model.startswith('ai21.j2'):
                generated_text = response_body.get('completions', [{}])[0].get('data', {}).get('text', '')
            else:
                generated_text = response_body.get('results', [{}])[0].get('outputText', '')
            
            return {
                'response': generated_text.strip(),
                'agent_id': agent_id,
                'query': query,
                'model': bedrock_model,
                'provider': 'bedrock',
                'context': context or {}
            }
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            
            # Fall back to Ollama on Bedrock error
            if self.ollama_client:
                try:
                    return self.ollama_client.invoke_agent(agent_id, query, context, model, system_prompt)
                except Exception:
                    pass
            
            raise Exception(f"Failed to invoke Bedrock agent: {error_code} - {error_message}")
        except Exception as e:
            # Fall back to Ollama on any error
            if self.ollama_client:
                try:
                    return self.ollama_client.invoke_agent(agent_id, query, context, model, system_prompt)
                except Exception:
                    pass
            raise Exception(f"Failed to invoke agent: {str(e)}")
    
    def get_agent_status(self, agent_id):
        """Get agent status from Bedrock or Ollama."""
        # Use Ollama if AWS is not configured
        if self.use_ollama and self.ollama_client:
            return self.ollama_client.get_agent_status(agent_id)
        
        try:
            # Check if Bedrock client is available
            bedrock_available = hasattr(self, 'bedrock_client') and self.bedrock_client is not None
            
            response = {
                'status': 'active' if bedrock_available else 'unavailable',
                'agent_id': agent_id,
                'provider': 'bedrock' if bedrock_available else 'unknown',
                'bedrock_available': bedrock_available,
                'default_model': getattr(self, 'default_bedrock_model', 'anthropic.claude-v2')
            }
            return response
        except (ClientError, Exception) as e:
            # Fall back to Ollama on error
            if self.ollama_client:
                try:
                    return self.ollama_client.get_agent_status(agent_id)
                except Exception:
                    pass
            raise Exception(f"Failed to get agent status: {str(e)}")
    
    def check_model_compatibility(self, model_id):
        """Check if a model is compatible with the agent API.
        
        Returns:
            dict with 'compatible' (bool) and 'error' (str if not compatible)
        """
        try:
            # Skip embedding models - they're not for text generation
            if model_id.startswith('amazon.titan-embed'):
                return {
                    'compatible': False,
                    'error': 'Embedding model - not for text generation'
                }
            
            # Test with a simple query
            test_query = "Say hello in one sentence."
            result = self.invoke_agent(
                agent_id='compatibility_test',
                query=test_query,
                model=model_id,
                system_prompt="You are a helpful assistant.",
                model_provider='bedrock'
            )
            
            if result and 'response' in result:
                response_text = result.get('response', '').strip()
                if response_text:
                    return {
                        'compatible': True,
                        'error': None
                    }
                else:
                    return {
                        'compatible': False,
                        'error': 'Empty response received'
                    }
            else:
                return {
                    'compatible': False,
                    'error': 'Invalid response format'
                }
                
        except ValueError as e:
            # Expected errors (like embedding models)
            error_msg = str(e)
            if 'embedding model' in error_msg.lower():
                return {
                    'compatible': False,
                    'error': error_msg
                }
            return {
                'compatible': False,
                'error': error_msg
            }
        except Exception as e:
            error_msg = str(e)
            # Check for specific error types
            if 'not supported' in error_msg.lower() or 'not enabled' in error_msg.lower():
                return {
                    'compatible': False,
                    'error': f'Model not enabled or not supported: {error_msg}'
                }
            elif 'doesn\'t support' in error_msg.lower() or 'not a valid' in error_msg.lower():
                return {
                    'compatible': False,
                    'error': f'Model not compatible: {error_msg}'
                }
            else:
                return {
                    'compatible': False,
                    'error': f'Compatibility check failed: {error_msg}'
                }
    
    def list_available_models(self):
        """List available Bedrock foundation models."""
        try:
            if not hasattr(self, 'bedrock_agent_client'):
                return []
            
            response = self.bedrock_agent_client.list_foundation_models()
            models = []
            for model_summary in response.get('modelSummaries', []):
                models.append({
                    'model_id': model_summary.get('modelId'),
                    'model_name': model_summary.get('modelName'),
                    'provider': model_summary.get('providerName'),
                    'input_modalities': model_summary.get('inputModalities', []),
                    'output_modalities': model_summary.get('outputModalities', []),
                })
            return models
        except Exception:
            # Return empty list on error
            return []
    
    def create_inference_profile(self, profile_name, model_id, regions, tags=None):
        """Create an inference profile for a model.
        
        Args:
            profile_name: Unique name for the inference profile
            model_id: Model ID to use for inference
            regions: List of AWS regions (e.g., ['us-east-1', 'us-west-2'])
            tags: Optional dict of tags
            
        Returns:
            dict with profile_arn and status
        """
        if self.use_ollama:
            raise Exception("Inference profiles are only available for Bedrock models")
        
        try:
            if not hasattr(self, 'bedrock_agent_client'):
                raise Exception("Bedrock client not available")
            
            # Create inference profile
            profile_config = {
                'name': profile_name,
                'model': model_id,
                'regions': regions
            }
            
            if tags:
                profile_config['tags'] = tags
            
            response = self.bedrock_agent_client.create_inference_profile(**profile_config)
            
            return {
                'profile_arn': response.get('inferenceProfileArn'),
                'status': 'creating',
                'profile_name': profile_name
            }
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            raise Exception(f"Failed to create inference profile: {error_code} - {error_message}")
        except Exception as e:
            raise Exception(f"Failed to create inference profile: {str(e)}")
    
    def get_inference_profile(self, profile_arn):
        """Get inference profile details.
        
        Args:
            profile_arn: ARN of the inference profile
            
        Returns:
            dict with profile details
        """
        if self.use_ollama:
            raise Exception("Inference profiles are only available for Bedrock models")
        
        try:
            if not hasattr(self, 'bedrock_agent_client'):
                raise Exception("Bedrock client not available")
            
            response = self.bedrock_agent_client.get_inference_profile(inferenceProfileArn=profile_arn)
            
            return {
                'profile_arn': response.get('inferenceProfileArn'),
                'profile_name': response.get('name'),
                'model_id': response.get('model'),
                'regions': response.get('regions', []),
                'status': response.get('status', 'unknown'),
                'created_at': response.get('createdAt'),
                'updated_at': response.get('updatedAt'),
                'metadata': response
            }
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            raise Exception(f"Failed to get inference profile: {error_code} - {error_message}")
        except Exception as e:
            raise Exception(f"Failed to get inference profile: {str(e)}")
    
    def list_inference_profiles(self):
        """List all inference profiles.
        
        Returns:
            list of inference profile dicts
        """
        if self.use_ollama:
            return []
        
        try:
            if not hasattr(self, 'bedrock_agent_client'):
                return []
            
            response = self.bedrock_agent_client.list_inference_profiles()
            profiles = []
            
            for profile_summary in response.get('inferenceProfileSummaries', []):
                profiles.append({
                    'profile_arn': profile_summary.get('inferenceProfileArn'),
                    'profile_name': profile_summary.get('name'),
                    'model_id': profile_summary.get('model'),
                    'status': profile_summary.get('status', 'unknown'),
                    'created_at': profile_summary.get('createdAt'),
                })
            
            return profiles
        except Exception as e:
            logger.error(f"Error listing inference profiles: {str(e)}")
            return []
    
    def delete_inference_profile(self, profile_arn):
        """Delete an inference profile.
        
        Args:
            profile_arn: ARN of the inference profile to delete
        """
        if self.use_ollama:
            raise Exception("Inference profiles are only available for Bedrock models")
        
        try:
            if not hasattr(self, 'bedrock_agent_client'):
                raise Exception("Bedrock client not available")
            
            self.bedrock_agent_client.delete_inference_profile(inferenceProfileArn=profile_arn)
            return {'success': True}
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            raise Exception(f"Failed to delete inference profile: {error_code} - {error_message}")
        except Exception as e:
            raise Exception(f"Failed to delete inference profile: {str(e)}")


    def invoke_agent_stream(self, agent_id, query, context=None, model=None, system_prompt=None, 
                             model_provider=None, training_data=None, inference_profile_arn=None, 
                             user=None, agent_db_id=None, max_tokens=None):
        """
        Invoke Bedrock agent with streaming response.
        
        Yields chunks of the response as they are generated.
        """
        # Validate model
        if not model or not str(model).strip():
            raise ValueError("Model is required for agent invocation")
        
        # Use Ollama if AWS is not configured
        if self.use_ollama and self.ollama_client:
            for chunk in self.ollama_client.invoke_agent_stream(agent_id, query, context, model, system_prompt, training_data):
                yield chunk
            return
        
        bedrock_model = str(model).strip()
        
        # Normalize model ID - remove context window suffixes
        if bedrock_model.count(':') > 1:
            parts = bedrock_model.split(':')
            if len(parts) >= 2:
                bedrock_model = ':'.join(parts[:2])
                logger.info(f"Normalized model ID for streaming: {model} -> {bedrock_model}")
        
        # Set optimized defaults
        if max_tokens is None:
            max_tokens = 1024
        temperature = 0.6
        
        try:
            # Check for embedding models
            if bedrock_model.startswith('amazon.titan-embed'):
                raise ValueError(f"Model '{bedrock_model}' is an embedding model and cannot be used for text generation.")
            
            # Prepare request body based on model type
            if bedrock_model.startswith('anthropic.claude-3') or 'claude-3' in bedrock_model.lower():
                # Claude 3+ models use Messages API format
                messages = []
                user_content = query
                if context:
                    context_str = json.dumps(context, indent=2)
                    user_content = f"Context: {context_str}\n\n{user_content}"
                
                messages.append({
                    "role": "user",
                    "content": [{"type": "text", "text": user_content}]
                })
                
                body_dict = {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": max_tokens,
                    "messages": messages,
                    "temperature": temperature,
                    "top_p": 0.9,
                }
                
                if system_prompt:
                    body_dict["system"] = [{"type": "text", "text": system_prompt}]
                
                body = json.dumps(body_dict)
            elif bedrock_model.startswith('amazon.nova'):
                # Amazon Nova models
                messages = []
                user_content = query
                if system_prompt:
                    user_content = f"{system_prompt}\n\n{user_content}"
                if context:
                    context_str = json.dumps(context, indent=2)
                    user_content = f"Context: {context_str}\n\n{user_content}"
                
                if 'nova-lite' in bedrock_model.lower():
                    messages.append({
                        "role": "user",
                        "content": [{"text": user_content}]
                    })
                    body = json.dumps({"messages": messages})
                else:
                    if system_prompt:
                        messages.append({"role": "system", "content": system_prompt})
                    messages.append({"role": "user", "content": user_content if not system_prompt else query})
                    body = json.dumps({"messages": messages, "temperature": temperature})
            elif bedrock_model.startswith('deepseek.') or 'deepseek' in bedrock_model.lower():
                # DeepSeek models
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                user_content = query
                if context:
                    context_str = json.dumps(context, indent=2)
                    user_content = f"Context: {context_str}\n\n{user_content}"
                messages.append({"role": "user", "content": user_content})
                body = json.dumps({
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "top_p": 0.9,
                })
            else:
                # Default Messages API format for other models
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                user_content = query
                if context:
                    context_str = json.dumps(context, indent=2)
                    user_content = f"Context: {context_str}\n\n{user_content}"
                messages.append({"role": "user", "content": user_content})
                body = json.dumps({
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "top_p": 0.9,
                })
            
            # Prepare invoke parameters
            invoke_params = {
                'body': body,
                'contentType': 'application/json',
                'accept': 'application/json'
            }
            
            if inference_profile_arn:
                invoke_params['modelId'] = inference_profile_arn
                logger.info(f"Using inference profile for streaming: {inference_profile_arn}")
            else:
                invoke_params['modelId'] = bedrock_model
            
            # Call invoke_model_with_response_stream
            logger.info(f"Starting Bedrock streaming for model: {bedrock_model}")
            response = self.bedrock_client.invoke_model_with_response_stream(**invoke_params)
            
            # Process streaming response
            full_response = ""
            for event in response.get('body', []):
                if 'chunk' in event:
                    chunk_data = json.loads(event['chunk']['bytes'].decode('utf-8'))
                    
                    # Extract text based on model type
                    text_chunk = ""
                    if bedrock_model.startswith('anthropic.claude-3') or 'claude-3' in bedrock_model.lower():
                        # Claude 3 streaming format
                        if chunk_data.get('type') == 'content_block_delta':
                            delta = chunk_data.get('delta', {})
                            text_chunk = delta.get('text', '')
                        elif chunk_data.get('type') == 'message_delta':
                            # End of message
                            pass
                    elif bedrock_model.startswith('amazon.nova'):
                        # Nova streaming format
                        if 'contentBlockDelta' in chunk_data:
                            delta = chunk_data.get('contentBlockDelta', {}).get('delta', {})
                            text_chunk = delta.get('text', '')
                        elif 'outputMessage' in chunk_data.get('output', {}):
                            content = chunk_data['output']['outputMessage'].get('content', [])
                            if content:
                                text_chunk = content[0].get('text', '')
                    elif bedrock_model.startswith('deepseek.') or 'deepseek' in bedrock_model.lower():
                        # DeepSeek streaming format
                        if 'choices' in chunk_data:
                            for choice in chunk_data.get('choices', []):
                                delta = choice.get('delta', {})
                                text_chunk = delta.get('content', '')
                        elif 'delta' in chunk_data:
                            text_chunk = chunk_data.get('delta', {}).get('text', '')
                    else:
                        # Generic format - try multiple keys
                        if 'delta' in chunk_data:
                            text_chunk = chunk_data.get('delta', {}).get('text', '')
                        elif 'text' in chunk_data:
                            text_chunk = chunk_data.get('text', '')
                        elif 'generation' in chunk_data:
                            text_chunk = chunk_data.get('generation', '')
                    
                    if text_chunk:
                        full_response += text_chunk
                        yield {
                            'type': 'chunk',
                            'content': text_chunk,
                            'done': False
                        }
            
            # Yield completion
            yield {
                'type': 'done',
                'content': '',
                'done': True,
                'full_response': full_response
            }
            
            logger.info(f"Bedrock streaming completed. Total length: {len(full_response)}")
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"Bedrock streaming error: {error_code} - {error_message}")
            
            # Fall back to Ollama if available
            if self.ollama_client:
                logger.info("Falling back to Ollama for streaming")
                for chunk in self.ollama_client.invoke_agent_stream(agent_id, query, context, model, system_prompt, training_data):
                    yield chunk
            else:
                yield {
                    'type': 'error',
                    'error': f"Bedrock streaming failed: {error_code} - {error_message}",
                    'done': True
                }
        except Exception as e:
            logger.error(f"Error in Bedrock streaming: {str(e)}", exc_info=True)
            yield {
                'type': 'error',
                'error': f"Streaming error: {str(e)}",
                'done': True
            }


def get_bedrock_client(user=None):
    """Get a Bedrock client instance."""
    # Disable Ollama fallback to see actual Bedrock errors
    client = BedrockClient(use_ollama_fallback=False)
    # Store user for model restriction checks
    if user:
        client.user = user
    return client


# Backward compatibility alias
def get_quick_suite_client():
    """Get a Bedrock client instance (backward compatibility)."""
    return BedrockClient(use_ollama_fallback=False)

