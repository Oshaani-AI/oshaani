"""Tool execution engine for agents."""
import json
import logging
from typing import Dict, Any, List, Optional
from .tools import ToolManager, CustomTool
from .models import Agent, CustomTool as CustomToolModel, ToolCall, AgentTool
from .mcp_tools import MCPToolManager

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes tools for agents."""
    
    def __init__(self, agent: Agent):
        self.agent = agent
        self.tool_manager = ToolManager()
        self.mcp_tool_manager = MCPToolManager(agent)
        
        # Get enabled/disabled tools from database (AgentTool model)
        # Fallback to JSON configuration for backward compatibility
        agent_tools = AgentTool.objects.filter(agent=agent)
        enabled_tools_db = set(agent_tools.filter(is_enabled=True).values_list('tool_name', flat=True))
        disabled_tools_db = set(agent_tools.filter(is_enabled=False).values_list('tool_name', flat=True))
        
        # Check if we have any database records, otherwise fall back to JSON config
        if agent_tools.exists():
            enabled_tools = list(enabled_tools_db)
            disabled_tools = list(disabled_tools_db)
            logger.info(f"Using database tool configuration for agent {agent.id}")
        else:
            # Fallback to JSON configuration for backward compatibility
            enabled_tools = agent.configuration.get('enabled_tools', []) if agent.configuration else []
            disabled_tools = agent.configuration.get('disabled_tools', []) if agent.configuration else []
            logger.info(f"Using JSON tool configuration (legacy) for agent {agent.id}")
        
        # Load all available tools
        logger.info(f"Initializing ToolExecutor for agent {agent.id} ({agent.name})")
        logger.info(f"Default tools available: {len(self.tool_manager.tools)}")
        if enabled_tools:
            logger.info(f"Enabled tools: {enabled_tools}")
        if disabled_tools:
            logger.info(f"Disabled tools: {disabled_tools}")
        
        self._load_custom_tools()
        self._load_mcp_tools()
        
        # Filter tools based on enabled/disabled configuration
        # If enabled_tools list exists and is not empty, only keep enabled tools
        # Otherwise, if disabled_tools list exists, remove disabled tools
        # When video tools are enabled, always keep url_resolver so "video from URL" flow works
        if enabled_tools or disabled_tools:
            effective_enabled = set(enabled_tools) if enabled_tools else None
            if effective_enabled and ('text_to_video' in effective_enabled or 'image_to_video' in effective_enabled):
                if 'url_resolver' not in disabled_tools:
                    effective_enabled = set(effective_enabled) | {'url_resolver'}
                if 'text_to_speech' not in disabled_tools:
                    effective_enabled = set(effective_enabled) | {'text_to_speech'}
                if 'combine_video_audio' not in disabled_tools:
                    effective_enabled = set(effective_enabled) | {'combine_video_audio'}
            tools_to_remove = []
            for tool_name in list(self.tool_manager.tools.keys()):
                should_remove = False
                keep_set = effective_enabled if effective_enabled is not None else None
                tool_obj = self.tool_manager.tools.get(tool_name)
                is_mcp_tool = tool_obj is not None and getattr(tool_obj, 'mcp_client', None) is not None
                if keep_set is not None:
                    if tool_name not in keep_set:
                        # Keep MCP tools so the agent can call MCP server for data (unless explicitly disabled)
                        if not is_mcp_tool:
                            should_remove = True
                if tool_name in disabled_tools:
                    if keep_set is None or tool_name not in keep_set:
                        should_remove = True
                if should_remove:
                    tools_to_remove.append(tool_name)
            for tool_name in tools_to_remove:
                if tool_name in self.tool_manager.tools:
                    del self.tool_manager.tools[tool_name]
                    logger.info(f"Filtered out disabled tool: {tool_name}")
        
        # Log summary of all loaded tools
        total_tools = len(self.tool_manager.tools)
        default_tools = len([t for t in self.tool_manager.tools.values() if not isinstance(t, CustomTool) and not hasattr(t, 'mcp_client')])
        custom_tools = len([t for t in self.tool_manager.tools.values() if isinstance(t, CustomTool)])
        mcp_tools = len([t for t in self.tool_manager.tools.values() if hasattr(t, 'mcp_client')])
        
        logger.info(f"Tool loading complete for agent {agent.id}:")
        logger.info(f"  - Total tools: {total_tools}")
        logger.info(f"  - Default tools: {default_tools}")
        logger.info(f"  - Custom tools: {custom_tools}")
        logger.info(f"  - MCP tools: {mcp_tools}")
    
    def _load_custom_tools(self):
        """Load custom tools for this agent."""
        custom_tools = CustomToolModel.objects.filter(agent=self.agent, is_active=True)
        logger.info(f"Loading {custom_tools.count()} custom tool(s) for agent {self.agent.id}")
        
        for tool_config in custom_tools:
            try:
                tool = CustomTool({
                    'function_name': tool_config.function_name,
                    'description': tool_config.description,
                    'instructions': tool_config.instructions,
                    'url': tool_config.url,
                    'method': tool_config.method,
                    'headers': tool_config.headers,
                    'parameters': tool_config.parameters,
                })
                self.tool_manager.register_custom_tool(tool)
                logger.info(f"Registered custom tool: {tool.name} (function: {tool_config.function_name})")
            except Exception as e:
                logger.error(f"Error loading custom tool {tool_config.function_name}: {str(e)}", exc_info=True)
    
    def _load_mcp_tools(self):
        """Load MCP tools for this agent."""
        try:
            # Connect to MCP servers and load their tools
            connection_results = self.mcp_tool_manager.connect_all()
            
            # Log connection results
            for server_name, connected in connection_results.items():
                if connected:
                    logger.info(f"Connected to MCP server: {server_name}")
                else:
                    logger.warning(f"Failed to connect to MCP server: {server_name}")
            
            # Register MCP tools with the tool manager
            mcp_tools = self.mcp_tool_manager.get_all_tools()
            logger.info(f"Found {len(mcp_tools)} MCP tools to register")
            
            for tool_name, tool in mcp_tools.items():
                try:
                    self.tool_manager.tools[tool_name] = tool
                    logger.info(f"Registered MCP tool: {tool_name} (from server: {tool.server_name})")
                except Exception as e:
                    logger.error(f"Error registering MCP tool {tool_name}: {str(e)}", exc_info=True)
            
            if mcp_tools:
                logger.info(f"Successfully registered {len(mcp_tools)} MCP tool(s) for agent {self.agent.id}")
        except Exception as e:
            logger.error(f"Error loading MCP tools: {str(e)}", exc_info=True)
    
    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """Get schema for all available tools (only enabled tools)."""
        # Get all tools from tool_manager (includes default, custom, and MCP tools)
        # MCP tools are already registered in tool_manager.tools, so we don't need to add them separately
        schema = self.tool_manager.get_tools_schema()
        
        # Ensure we have all tools (double-check MCP tools are included)
        mcp_tools = self.mcp_tool_manager.get_all_tools()
        mcp_tool_names = {tool.name for tool in mcp_tools.values()}
        schema_tool_names = {tool['name'] for tool in schema}
        
        # Add any missing MCP tools
        missing_mcp_tools = mcp_tool_names - schema_tool_names
        if missing_mcp_tools:
            logger.warning(f"Found {len(missing_mcp_tools)} MCP tools not in schema, adding them")
            for tool_name in missing_mcp_tools:
                if tool_name in self.tool_manager.tools:
                    schema.append(self.tool_manager.tools[tool_name].get_schema())
        
        logger.debug(f"Returning schema for {len(schema)} tool(s)")
        return schema
    
    def get_all_tools_schema(self) -> List[Dict[str, Any]]:
        """Get schema for ALL tools including disabled ones (for display purposes)."""
        # Create a temporary ToolManager to get all default tools without filtering
        from .tools import ToolManager
        temp_tool_manager = ToolManager()
        
        # Get all default tools
        all_tools_schema = temp_tool_manager.get_tools_schema()
        
        # Load custom tools for this agent
        custom_tools = CustomToolModel.objects.filter(agent=self.agent, is_active=True)
        for tool_config in custom_tools:
            try:
                tool = CustomTool({
                    'function_name': tool_config.function_name,
                    'description': tool_config.description,
                    'instructions': tool_config.instructions,
                    'url': tool_config.url,
                    'method': tool_config.method,
                    'headers': tool_config.headers,
                    'parameters': tool_config.parameters,
                })
                all_tools_schema.append(tool.get_schema())
            except Exception as e:
                logger.error(f"Error loading custom tool {tool_config.function_name}: {str(e)}", exc_info=True)
        
        # Connect to MCP servers and load MCP tools
        try:
            self.mcp_tool_manager.connect_all()
            mcp_tools = self.mcp_tool_manager.get_all_tools()
            for tool_name, tool in mcp_tools.items():
                try:
                    all_tools_schema.append(tool.get_schema())
                except Exception as e:
                    logger.error(f"Error loading MCP tool {tool_name}: {str(e)}", exc_info=True)
        except Exception as e:
            logger.error(f"Error connecting to MCP servers: {str(e)}", exc_info=True)
        
        return all_tools_schema
    
    def _get_latest_video_and_audio_from_conversation(self, conversation) -> tuple:
        """Get latest video file name and latest audio file name from this conversation's tool calls.
        Returns (video_file_name or None, audio_file_name or None).
        """
        import re
        latest_video = None
        latest_audio = None
        try:
            # Latest video from text_to_video or image_to_video
            video_call = ToolCall.objects.filter(
                conversation=conversation,
                tool_name__in=('text_to_video', 'image_to_video'),
                state='done'
            ).order_by('-created_at').first()
            if video_call and (video_call.result_files or video_call.result_content):
                for f in (video_call.result_files or []):
                    if isinstance(f, dict) and f.get('file_name'):
                        latest_video = f.get('file_name')
                        break
                if not latest_video and video_call.result_content:
                    m = re.search(r'File:\s*(\S+)', video_call.result_content)
                    if m:
                        latest_video = m.group(1).strip()
            # Latest audio from text_to_speech
            audio_call = ToolCall.objects.filter(
                conversation=conversation,
                tool_name='text_to_speech',
                state='done'
            ).order_by('-created_at').first()
            if audio_call and (audio_call.result_files or audio_call.result_content):
                for f in (audio_call.result_files or []):
                    if isinstance(f, dict) and f.get('file_name'):
                        latest_audio = f.get('file_name')
                        break
                if not latest_audio and audio_call.result_content:
                    m = re.search(r'File:\s*(\S+)', audio_call.result_content)
                    if m:
                        latest_audio = m.group(1).strip()
        except Exception as e:
            logger.debug(f"Error getting latest video/audio from conversation: {e}")
        return (latest_video, latest_audio)
    
    def execute_tool(self, tool_name: str, parameters: Dict[str, Any], conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """Execute a tool and return result."""
        tool = self.tool_manager.get_tool(tool_name)
        if not tool:
            return {
                "error": f"Tool '{tool_name}' not found",
                "state": "error"
            }
        
        # Verify agent still exists before creating tool call
        try:
            from .models import Agent
            if not Agent.objects.filter(id=self.agent.id).exists():
                logger.error(f"Agent {self.agent.id} no longer exists")
                return {
                    "error": "Agent no longer exists",
                    "state": "error"
                }
        except Exception as e:
            logger.error(f"Error verifying agent exists: {str(e)}")
            return {
                "error": f"Error verifying agent: {str(e)}",
                "state": "error"
            }
        
        # Create tool call record
        tool_call = ToolCall.objects.create(
            agent=self.agent,
            tool_name=tool_name,
            parameters=parameters,
            state='executing'
        )
        
        conversation = None
        if conversation_id:
            from .models import Conversation
            try:
                conversation = Conversation.objects.get(conversation_id=conversation_id)
                tool_call.conversation = conversation
                tool_call.save()
            except Conversation.DoesNotExist:
                pass
        
        # Before combine_video_audio: check for video/audio; fill from conversation or ask to create
        if tool_name == 'combine_video_audio' and conversation:
            video_val = (parameters.get('video_path') or parameters.get('video_file') or parameters.get('video') or '').strip()
            audio_val = (parameters.get('audio_path') or parameters.get('audio_file') or parameters.get('audio') or '').strip()
            if not video_val or not audio_val:
                latest_video, latest_audio = self._get_latest_video_and_audio_from_conversation(conversation)
                if (not video_val and latest_video) or (not audio_val and latest_audio):
                    parameters = dict(parameters)
                    if not video_val and latest_video:
                        parameters['video_path'] = latest_video
                        logger.info(f"combine_video_audio: using latest video from conversation: {latest_video}")
                    if not audio_val and latest_audio:
                        parameters['audio_path'] = latest_audio
                        logger.info(f"combine_video_audio: using latest audio from conversation: {latest_audio}")
                # If still missing either, return clear instruction to create first (do not call tool)
                video_val = parameters.get('video_path') or parameters.get('video_file') or parameters.get('video') or ''
                audio_val = parameters.get('audio_path') or parameters.get('audio_file') or parameters.get('audio') or ''
                if not (str(video_val).strip()) or not (str(audio_val).strip()):
                    missing = []
                    if not (str(video_val).strip()):
                        missing.append("video (call text_to_video or image_to_video first)")
                    if not (str(audio_val).strip()):
                        missing.append("audio (call text_to_speech first)")
                    tool_call.state = 'error'
                    tool_call.error = "Missing: " + "; ".join(missing)
                    tool_call.save()
                    return {
                        "error": "Missing: " + "; ".join(missing) + ". Create them in this conversation, then call combine_video_audio with the returned file names.",
                        "state": "error"
                    }
        
        try:
            # Execute the tool
            result = tool.execute(parameters)
            
            # Extract result files if any
            result_files = result.get('result_files', [])
            if 'file_name' in result and result.get('file_name'):
                # Add file_name to result_files if not already there (image, file, or video URL)
                file_info = {
                    'file_name': result.get('file_name'),
                    'file_url': result.get('image_url') or result.get('file_url') or result.get('video_url', '') or result.get('audio_url', '')
                }
                if file_info not in result_files:
                    result_files.append(file_info)
            
            # Register generated files in ConversationFile model for download_file endpoint
            # This allows files generated by tools (e.g., text_to_image) to be accessible via download_file
            # Following REST API pattern for file downloads
            from .models import ConversationFile
            from django.core.files.storage import default_storage
            from django.conf import settings
            import uuid
            import os
            
            # Check if tool generated a file that needs to be registered
            # Support multiple formats: image_url, file_url, file_path, file_content, or created_files
            file_url = None
            file_path = None
            file_name = None
            file_content = None
            
            # Priority 1: Check for file_content (explicit content to save as file)
            # This allows tools and LLM models to provide content that should be saved as downloadable files
            if 'file_content' in result:
                file_content_value = result.get('file_content')
                if file_content_value:
                    file_content = file_content_value
                    file_name = result.get('file_name', f'generated_{tool_name}_{abs(hash(str(file_content)))}.txt')
                    # Normalize content to bytes for storage (bytes are left as-is)
                    if isinstance(file_content, str):
                        file_content = file_content.encode('utf-8')
                    elif not isinstance(file_content, bytes):
                        file_content = str(file_content).encode('utf-8')
            
            # Priority 2: Check for image_url (from text_to_image tool)
            elif 'image_url' in result:
                file_url = result.get('image_url')
                file_name = result.get('file_name', 'generated_file')
            
            # Priority 2b: Check for video_url (from text_to_video / image_to_video / combine_video_audio tools)
            elif 'video_url' in result:
                file_url = result.get('video_url')
                file_name = result.get('file_name', 'generated_file')
            
            # Priority 2c: Check for audio_url (from text_to_speech tool)
            elif 'audio_url' in result:
                file_url = result.get('audio_url')
                file_name = result.get('file_name', 'generated_file')
            
            # Priority 3: Check for file_url (generic file URL)
            elif 'file_url' in result:
                file_url = result.get('file_url')
                file_name = result.get('file_name', 'generated_file')
            
            # Priority 4: Check for file_path (filesystem path)
            elif 'file_path' in result:
                file_path_value = result.get('file_path')
                if file_path_value:
                    # Only register file_path if it's a newly created file (not a file that was read)
                    # Tools like code_executor return 'created_files' or the tool_name indicates file creation
                    # Tools like read_file return file_path for existing files (should not register)
                    tool_creates_files = (
                        tool_name in ['code_executor', 'write_file'] or 
                        'created_files' in result or
                        result.get('save_as_file', False) or  # Explicit flag to save as file
                        (result.get('success') and 'created' in str(result.get('stdout', '')).lower())
                    )
                    
                    if tool_creates_files:
                        file_path = file_path_value
                        file_name = os.path.basename(file_path) if file_path else result.get('file_name', 'generated_file')
            
            # Priority 5: Check for created_files array (from code_executor)
            if 'created_files' in result and result.get('created_files') and not file_path and not file_content:
                created_files_list = result.get('created_files', [])
                if created_files_list:
                    # Use first created file
                    file_path = created_files_list[0]
                    file_name = os.path.basename(file_path) if file_path else 'generated_file'
            
            # Priority 6: Check for save_as_file flag with content fields
            # This allows tools to indicate that certain content should be saved as a file
            if not file_url and not file_path and not file_content and result.get('save_as_file', False):
                # Check common content fields that might need to be saved
                content_fields = ['content', 'text', 'output', 'result', 'response', 'data']
                for field in content_fields:
                    if field in result and result.get(field):
                        content_value = result.get(field)
                        if isinstance(content_value, (str, bytes)):
                            file_content = content_value if isinstance(content_value, bytes) else content_value.encode('utf-8')
                            file_name = result.get('file_name', f'generated_{tool_name}_{field}.txt')
                            break
            
            # Register file if we found one
            if file_url or file_path or file_content:
                try:
                    media_path = None
                    source_file_path = None
                    final_file_content = None
                    final_file_size = None
                    
                    # Handle file_content (explicit content to save) - highest priority
                    if file_content is not None:
                        final_file_content = file_content if isinstance(file_content, bytes) else file_content.encode('utf-8')
                        final_file_size = len(final_file_content)
                    
                    # Handle file_path (filesystem path) - files created by tools
                    elif file_path and os.path.exists(file_path):
                        source_file_path = file_path
                        is_filesystem_file = True
                        original_file_path = source_file_path
                        final_file_size = os.path.getsize(source_file_path)
                        # Read from filesystem
                        with open(source_file_path, 'rb') as f:
                            final_file_content = f.read()
                    
                    # Handle file_url (storage URL) - files saved to Django storage
                    elif file_url and not file_url.startswith('http://') and not file_url.startswith('https://') and not file_url.startswith('#'):
                        # Extract file path from URL
                        # URL format: /media/generated_images/filename.png or full URL
                        # Remove /media/ prefix and any domain
                        if '/media/' in file_url:
                            media_path = file_url.split('/media/')[1]
                        else:
                            # Try to extract from full URL
                            media_path = file_url
                        
                        # Check if file exists in storage
                        if default_storage.exists(media_path):
                            source_file_path = media_path
                            final_file_size = default_storage.size(source_file_path)
                            # Read from storage
                            with default_storage.open(source_file_path, 'rb') as f:
                                final_file_content = f.read()
                    
                    # Proceed if we have file content to save
                    if final_file_content is not None:
                        # Track if file is on filesystem (needs to be deleted after copying)
                        is_filesystem_file = (file_path is not None and os.path.exists(file_path))
                        original_file_path = file_path if is_filesystem_file else None
                        
                        # Generate unique file_id
                        file_id = str(uuid.uuid4())
                        
                        # Get conversation if available
                        conversation = None
                        if conversation_id:
                            from .models import Conversation
                            try:
                                conversation = Conversation.objects.get(conversation_id=conversation_id)
                            except Conversation.DoesNotExist:
                                pass
                        elif hasattr(self, 'conversation') and self.conversation:
                            # If conversation was passed to ToolExecutor
                            conversation = self.conversation
                        
                        # Create ConversationFile record for generated file
                        # Note: file_path is a FileField with upload_to='conversation_files/'
                        # We'll copy the generated file to conversation_files/ directory for consistency
                        from django.core.files.base import ContentFile
                        
                        # Determine file extension from file_name or result
                        file_ext = os.path.splitext(file_name)[1] if file_name else ''
                        if not file_ext:
                            # Try to get extension from result or guess from content type
                            file_ext = result.get('file_extension', '')
                            if not file_ext:
                                # Guess from file_type if available
                                file_type_hint = result.get('file_type', '')
                                if 'text' in file_type_hint or 'json' in file_type_hint:
                                    file_ext = '.txt' if 'json' not in file_type_hint else '.json'
                                elif 'image' in file_type_hint:
                                    file_ext = '.png' if 'png' in file_type_hint else '.jpg'
                                else:
                                    file_ext = '.txt'  # Default to .txt
                        
                        # Ensure file_name has extension
                        if file_name and not os.path.splitext(file_name)[1]:
                            file_name = file_name + file_ext
                        elif not file_name:
                            file_name = f'generated_file{file_ext}'
                        
                        # Save to conversation_files/ directory (matching upload_file behavior)
                        conversation_file_path = f"conversation_files/{self.agent.id}/{file_id}{file_ext}"
                        saved_path = default_storage.save(conversation_file_path, ContentFile(final_file_content))
                        
                        # Ensure download_url is properly set
                        download_url = default_storage.url(saved_path)
                        
                        # Determine file type
                        file_type = result.get('file_type') or self._guess_file_type(file_name)
                        
                        # Create ConversationFile record
                        ConversationFile.objects.create(
                            agent=self.agent,
                            conversation=conversation,
                            file_name=file_name,
                            file_path=saved_path,  # FileField - Django will handle the path
                            file_type=file_type,
                            file_size=final_file_size,
                            file_id=file_id,
                            download_url=download_url  # Use the new path for download URL
                        )
                        
                        # Delete original file from filesystem after successful registration
                        if is_filesystem_file and original_file_path and os.path.exists(original_file_path):
                            try:
                                os.remove(original_file_path)
                            except Exception as e:
                                logger.warning(f"[ToolExecutor] Failed to delete original file {original_file_path}: {str(e)}")
                        
                        # Add file_id to result so it can be used with download_file endpoint
                        if 'file_id' not in result:
                            result['file_id'] = file_id
                        
                        # Add file information to result
                        result['file_name'] = file_name
                        result['download_url'] = download_url
                        # Generated images are first saved under generated_images/ then copied here.
                        # Keep image_url identical to download_url so tool JSON / LLM never mix two paths
                        # or invent a different conversation_files UUID from an earlier turn.
                        if tool_name == 'text_to_image' and result.get('image_url'):
                            result['image_url'] = download_url
                        
                        # Update result_files to include file_id
                        file_info_found = False
                        for file_info in result_files:
                            if file_info.get('file_name') == file_name:
                                file_info['file_id'] = file_id
                                file_info['file_url'] = download_url  # Use the download URL
                                file_info_found = True
                                break
                        
                        # If not found in result_files, add it
                        if not file_info_found:
                            result_files.append({
                                'file_id': file_id,
                                'file_name': file_name,
                                'file_url': download_url,
                                'file_type': file_type,
                                'file_size': final_file_size
                            })
                except Exception as e:
                    logger.error(f"[ToolExecutor] Failed to register generated file in ConversationFile: {str(e)}", exc_info=True)
                    # Continue even if registration fails - file is still accessible via direct URL
            
            # Format result content for LLM consumption
            result_content = ""
            if result.get('error'):
                result_content = f"Error: {result.get('error')}"
            elif result.get('success', True):
                # Format successful results based on tool type
                if tool_name == 'write_file':
                    # Format write_file tool results nicely
                    file_name = result.get('file_name', 'file')
                    message = result.get('message', f"File '{file_name}' created successfully.")
                    result_content = f"✅ {message}"
                    if result.get('file_id'):
                        result_content += f"\n\n📄 **File:** `{file_name}`"
                        result_content += f"\n🔗 **File ID:** `{result.get('file_id')}`"
                        if result.get('download_url'):
                            result_content += f"\n💾 File is available for download."
                elif 'transcription' in result:
                    result_content = f"Transcription: {result.get('transcription', '')}"
                elif 'text' in result:
                    result_content = f"Extracted text: {result.get('text', '')}"
                elif 'summary' in result:
                    result_content = f"Summary: {result.get('summary', '')}"
                elif 'answer' in result:
                    result_content = f"Answer: {result.get('answer', '')}"
                elif 'translated_text' in result:
                    result_content = f"Translated text: {result.get('translated_text', '')}"
                elif 'results' in result:
                    # For web search
                    results = result.get('results', [])
                    result_content = f"Found {len(results)} results:\n"
                    for i, item in enumerate(results[:5], 1):
                        result_content += f"{i}. {item.get('title', '')}: {item.get('snippet', '')}\n"
                elif 'stdout' in result:
                    # For code executor
                    result_content = f"Output: {result.get('stdout', '')}"
                    if result.get('stderr'):
                        result_content += f"\nErrors: {result.get('stderr', '')}"
                elif tool_name in ('text_to_video', 'image_to_video') and result.get('success'):
                    # Video tools: include full download link so the agent can provide it by default
                    file_name = result.get('file_name', 'generated_video.mp4')
                    download_url = result.get('download_url') or result.get('video_url', '')
                    result_content = f"Video generated successfully.\n\nFile: {file_name}"
                    if download_url:
                        result_content += f"\n\nDownload (full link): {download_url}"
                    if result.get('model_used'):
                        result_content += f"\nModel: {result.get('model_used')}"
                elif tool_name == 'text_to_speech' and result.get('success'):
                    file_name = result.get('file_name', 'generated_audio.mp3')
                    download_url = result.get('download_url') or result.get('audio_url', '')
                    result_content = f"Audio generated successfully.\n\nFile: {file_name}"
                    if download_url:
                        result_content += f"\n\nDownload (full link): {download_url}"
                elif tool_name == 'combine_video_audio' and result.get('success'):
                    file_name = result.get('file_name', 'generated_video.mp4')
                    download_url = result.get('download_url') or result.get('video_url', '')
                    result_content = f"Video with sound combined successfully.\n\nFile: {file_name}"
                    if download_url:
                        result_content += f"\n\nDownload (full link): {download_url}"
                elif tool_name == 'text_to_image' and result.get('success'):
                    file_name = result.get('file_name', 'generated_image.png')
                    rel = result.get('download_url') or result.get('image_url', '')
                    display_url = rel
                    if rel and rel.startswith('/') and not rel.startswith('//'):
                        site = getattr(settings, 'SITE_URL', '') or ''
                        if site:
                            display_url = site.rstrip('/') + rel
                    result_content = f"Image generated successfully.\n\nFile: {file_name}"
                    if result.get('model_used'):
                        result_content += f"\nModel: {result.get('model_used')}"
                    if display_url:
                        result_content += (
                            "\n\nShare this exact URL with the user (same file_id as below; "
                            "do not substitute UUIDs or paths from earlier messages):\n"
                            f"{display_url}"
                        )
                    if result.get('file_id'):
                        result_content += f"\nFile ID: {result.get('file_id')}"
                elif 'data' in result:
                    result_content = f"Result: {json.dumps(result.get('data', {}), indent=2)}"
                else:
                    # Generic success message
                    result_content = json.dumps(result, indent=2)
            else:
                result_content = json.dumps(result, indent=2)
            
            # Update tool call record
            tool_call.result_content = result_content
            tool_call.result_files = result_files
            tool_call.state = 'done'
            from django.utils import timezone
            tool_call.completed_at = timezone.now()
            tool_call.save()
            
            return {
                "name": tool_name,
                "parameters": json.dumps(parameters),
                "result_content": result_content,
                "result_files": result_files,
                "completed_at": tool_call.completed_at.isoformat(),
                "error": result.get('error', ''),
                "state": "done",
                "full_result": result
            }
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {str(e)}", exc_info=True)
            tool_call.error = str(e)
            tool_call.state = 'error'
            tool_call.save()
            
            return {
                "name": tool_name,
                "parameters": json.dumps(parameters),
                "result_content": "",
                "result_files": [],
                "error": str(e),
                "state": "error"
            }
    
    def _guess_file_type(self, file_name: str) -> str:
        """Guess MIME type from file extension."""
        import mimetypes
        mime_type, _ = mimetypes.guess_type(file_name)
        return mime_type or 'application/octet-stream'
    
    def format_tools_for_prompt(self) -> str:
        """Format tools information for inclusion in system prompt (optimized for token usage)."""
        tools_schema = self.get_tools_schema()
        if not tools_schema:
            return ""
        
        tools_text = "\n\nTools:\n"
        
        for tool in tools_schema:
            tools_text += f"{tool['name']}: {tool['description']}"
            if tool.get('parameters'):
                params = [f"{p['name']}{'*' if p.get('required') else ''}" for p in tool['parameters']]
                tools_text += f" Params: {', '.join(params)}"
                # Emphasize that text_prompt must be the real description (reduces placeholder "..." from LLM)
                if tool['name'] in ('text_to_video', 'image_to_video'):
                    tools_text += " (text_prompt = full scene description, never use ... or placeholder)"
            tools_text += "\n"
        
        return tools_text



