"""WebSocket consumers for agents_app."""
import json
import logging
import uuid
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import Agent, TestResult, AgentFeedback, Conversation
from .ollama_integration import OllamaClient
from .feedback_optimizer import FeedbackOptimizer
from .agent_loop import AgentLoop

logger = logging.getLogger(__name__)


class AgentChatConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer for agent chat."""
    
    async def connect(self):
        """Handle WebSocket connection."""
        self.agent_id = self.scope['url_route']['kwargs']['agent_id']
        self.user = self.scope['user']
        self.room_group_name = f'agent_chat_{self.agent_id}'
        
        # Verify user is authenticated and has access to the agent
        if not self.user.is_authenticated:
            await self.close()
            return
        
        # Verify agent exists and user has access
        agent = await self.get_agent()
        if not agent:
            await self.close()
            return
        
        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        
        # Send welcome message
        await self.send(text_data=json.dumps({
            'type': 'system_message',
            'message': f'Connected to agent: {agent.name}',
            'agent_id': str(self.agent_id)
        }))
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def receive(self, text_data):
        """Receive message from WebSocket."""
        try:
            data = json.loads(text_data)
            message_type = data.get('type', 'chat_message')
            
            if message_type == 'chat_message':
                query = data.get('message', '').strip()
                expected_response = data.get('expected_response', '').strip()
                
                if not query:
                    await self.send(text_data=json.dumps({
                        'type': 'error',
                        'message': 'Message cannot be empty'
                    }))
                    return
                
                # Send typing indicator
                await self.send(text_data=json.dumps({
                    'type': 'typing',
                    'status': True
                }))
                
                # Extract file information
                files = data.get('files', [])
                file_urls = data.get('file_urls', [])
                
                # Process the message
                await self.handle_chat_message(query, expected_response, files=files, file_urls=file_urls)
            elif message_type == 'feedback':
                # Handle feedback for a previous response
                response_id = data.get('response_id')
                feedback_type = data.get('feedback_type', 'neutral')
                feedback_text = data.get('feedback_text', '').strip()
                query = data.get('query', '')
                response = data.get('response', '')
                
                await self.handle_feedback(response_id, query, response, feedback_type, feedback_text)
            else:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': f'Unknown message type: {message_type}'
                }))
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Invalid JSON format'
            }))
        except Exception as e:
            logger.error(f"Error in receive: {str(e)}", exc_info=True)
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Error processing message: {str(e)}'
            }))
    
    async def handle_chat_message(self, query, expected_response, files=None, file_urls=None):
        """Handle chat message and get agent response."""
        try:
            agent = await self.get_agent()
            if not agent:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Agent not found'
                }))
                return
            
            # Get model information - wrap in sync function to avoid lazy loading issues
            model_info = await self.get_model_info(agent)
            if not model_info:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Agent is not configured with a model. Please configure the agent with a valid LLM model.'
                }))
                return
            
            model_provider = model_info['provider']
            model_id = model_info['model_id']
            
            if not model_id or not str(model_id).strip():
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Agent model ID is missing. Please reconfigure the agent with a valid model.'
                }))
                return
            
            model_id = str(model_id).strip()
            
            # Process files if provided
            file_context = ''
            if files and len(files) > 0:
                file_names = [f.get('name', 'Unknown') for f in files]
                file_context = f"\n\nAttached files: {', '.join(file_names)}"
                # In a full implementation, you would read file contents here
                # For now, we just mention the files in the context
            
            # Add file context to query
            if file_context:
                query = query + file_context
            
            # Retrieve training data
            training_data_list = await self.get_training_data(agent)
            
            # Get enhanced context from feedback optimizer
            enhanced_context = await self.get_enhanced_context(agent, query)
            if enhanced_context:
                # Add positive feedback examples to context
                context_note = "\n\nExamples of well-received responses to similar queries:\n"
                for i, example in enumerate(enhanced_context[:3], 1):  # Top 3 examples
                    context_note += f"\nExample {i}:\nQuery: {example['similar_query']}\nResponse: {example['good_response']}...\n"
                if not training_data_list:
                    training_data_list = []
                training_data_list.append({
                    'content': {'text': context_note},
                    'data_type': 'feedback_optimization'
                })
            
            # Get optimized system prompt if feedback optimization is enabled
            system_prompt = await self.get_optimized_system_prompt(agent)
            
            # Get timestamp
            timestamp = await self.get_timestamp()
            
            # Send user message
            await self.send(text_data=json.dumps({
                'type': 'user_message',
                'message': query,
                'timestamp': timestamp
            }))
            
            # Check if this is a streaming response (Ollama)
            is_streaming = model_provider == 'ollama'
            
            if is_streaming:
                # For streaming, create a message container first
                response_id = str(uuid.uuid4())
                response_timestamp = await self.get_timestamp()
                
                # Send initial message container for streaming
                await self.send(text_data=json.dumps({
                    'type': 'agent_message_start',
                    'timestamp': response_timestamp,
                    'model': model_id,
                    'provider': model_provider,
                    'response_id': response_id,
                    'query': query
                }))
                
                # Get streaming response (chunks are sent during streaming)
                result = await self.get_agent_response(
                    agent, query, model_provider, model_id, training_data_list, system_prompt
                )
                
                response_text = result.get('response', 'No response received')
                
                # Send final message with complete response
                await self.send(text_data=json.dumps({
                    'type': 'agent_message',
                    'message': response_text,
                    'timestamp': response_timestamp,
                    'model': model_id,
                    'provider': model_provider,
                    'response_id': response_id,
                    'query': query,
                    'tool_calls_count': 0,
                    'iterations': result.get('iterations', 1)
                }))
            else:
                # Non-streaming response (Bedrock or other)
                result = await self.get_agent_response(
                    agent, query, model_provider, model_id, training_data_list, system_prompt
                )
                
                # Send tool calls if any
                tool_calls = result.get('tool_calls', [])
                if tool_calls:
                    for tool_call in tool_calls:
                        tool_name = tool_call.get('tool', 'unknown')
                        tool_params = tool_call.get('parameters', {})
                        tool_result = tool_call.get('result', {})
                        
                        # Send tool call notification
                        await self.send(text_data=json.dumps({
                            'type': 'tool_call',
                            'tool_name': tool_name,
                            'parameters': tool_params,
                            'timestamp': await self.get_timestamp()
                        }))
                        
                        # Send tool result
                        await self.send(text_data=json.dumps({
                            'type': 'tool_result',
                            'tool_name': tool_name,
                            'result': tool_result,
                            'timestamp': await self.get_timestamp()
                        }))
                
                response_text = result.get('response', 'No response received')
                
                # Get timestamp for response
                response_timestamp = await self.get_timestamp()
                
                # Generate a unique response ID for feedback tracking
                response_id = str(uuid.uuid4())
                
                # Send agent response with response_id for feedback
                await self.send(text_data=json.dumps({
                    'type': 'agent_message',
                    'message': response_text,
                    'timestamp': response_timestamp,
                    'model': model_id,
                    'provider': model_provider,
                    'response_id': response_id,
                    'query': query,  # Include query for feedback context
                    'tool_calls_count': len(tool_calls),
                    'iterations': result.get('iterations', 1)
                }))
            
            # Save test result if expected response provided
            if expected_response:
                passed = bool(expected_response and expected_response.lower() in response_text.lower())
                await self.save_test_result(agent, query, expected_response, response_text, passed)
            
            # Send typing indicator off
            await self.send(text_data=json.dumps({
                'type': 'typing',
                'status': False
            }))
            
        except Exception as e:
            logger.error(f"Error handling chat message: {str(e)}", exc_info=True)
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Error: {str(e)}'
            }))
            await self.send(text_data=json.dumps({
                'type': 'typing',
                'status': False
            }))
    
    @database_sync_to_async
    def get_agent(self):
        """Get agent from database with related model prefetched."""
        try:
            return Agent.objects.select_related('model').get(id=self.agent_id, user=self.user)
        except Agent.DoesNotExist:
            return None
    
    @database_sync_to_async
    def get_model_info(self, agent):
        """Get model information from agent (sync operation)."""
        if not agent.model:
            return None
        return {
            'provider': agent.model.provider,
            'model_id': agent.model.model_id,
        }
    
    @database_sync_to_async
    def get_training_data(self, agent):
        """Get training data for agent."""
        training_data_list = []
        # Use list() to force evaluation of the queryset
        training_data_objs = list(agent.training_data.all())
        for td in training_data_objs:
            training_data_list.append({
                'content': td.content,
                'data_type': td.data_type,
            })
        return training_data_list
    
    async def get_agent_response(self, agent, query, model_provider, model_id, training_data_list, system_prompt=None):
        """Get response from agent using AgentLoop (async operation)."""
        try:
            # Check if this is an Ollama model and we should stream
            if model_provider == 'ollama':
                # Use streaming for Ollama models
                return await self._get_ollama_streaming_response(
                    agent, query, model_id, training_data_list, system_prompt
                )
            else:
                # Use AgentLoop for tool calling support (non-streaming)
                result = await database_sync_to_async(self._get_agent_response_with_loop)(
                    agent, query, system_prompt
                )
                return result
        except Exception as e:
            logger.error(f"Error getting agent response: {str(e)}", exc_info=True)
            return {'response': f'Error: {str(e)}', 'tool_calls': []}
    
    async def _get_ollama_streaming_response(self, agent, query, model_id, training_data_list, system_prompt=None):
        """Get streaming response from Ollama agent."""
        from .ollama_integration import is_ollama_available
        import asyncio
        from queue import Queue
        import threading
        
        if not is_ollama_available():
            raise ValueError(
                "Ollama is not configured or not reachable. Set OLLAMA_ENABLED and OLLAMA_BASE_URL and ensure the server is running."
            )
        # Get system prompt
        if not system_prompt:
            system_prompt = agent.configuration.get('instruction') or agent.configuration.get('system_prompt', '')
        
        # Create Ollama client
        client = OllamaClient()
        full_response = ""
        
        # Queue to pass chunks from thread to async
        chunk_queue = Queue()
        stream_done = threading.Event()
        stream_error = [None]
        
        def stream_in_thread():
            """Run streaming in a separate thread."""
            try:
                for chunk in client.invoke_agent_stream(
                    agent_id=str(agent.id),
                    query=query,
                    model=model_id,
                    system_prompt=system_prompt,
                    training_data=training_data_list if training_data_list else None
                ):
                    chunk_queue.put(chunk)
                    if chunk.get('done', False):
                        break
            except Exception as e:
                logger.error(f"Error in stream thread: {str(e)}", exc_info=True)
                stream_error[0] = e
            finally:
                stream_done.set()
        
        try:
            # Start streaming in background thread
            stream_thread = threading.Thread(target=stream_in_thread, daemon=True)
            stream_thread.start()
            
            # Process chunks as they arrive
            while not stream_done.is_set() or not chunk_queue.empty():
                try:
                    # Wait for chunk with timeout
                    chunk = chunk_queue.get(timeout=0.1)
                    content = chunk.get('content', '')
                    done = chunk.get('done', False)
                    
                    if content:
                        full_response += content
                        # Send chunk to WebSocket
                        await self.send(text_data=json.dumps({
                            'type': 'stream_chunk',
                            'content': content,
                            'done': False
                        }))
                    
                    if done:
                        # Send final message
                        await self.send(text_data=json.dumps({
                            'type': 'stream_chunk',
                            'content': '',
                            'done': True
                        }))
                        break
                except Exception:
                    # Timeout or empty queue, check if done
                    await asyncio.sleep(0.05)
                    continue
            
            # Wait for thread to finish
            stream_thread.join(timeout=5)
            
            # Check for errors
            if stream_error[0]:
                raise stream_error[0]
            
            return {
                'response': full_response,
                'tool_calls': [],
                'iterations': 1
            }
        except Exception as e:
            logger.error(f"Error streaming from Ollama: {str(e)}", exc_info=True)
            raise
    
    def _get_agent_response_with_loop(self, agent, query, system_prompt=None):
        """Get response using AgentLoop (sync helper)."""
        # Get or create conversation
        conversation = None
        
        # For testing status agents, always start fresh - don't reuse existing conversations
        if agent.status != 'testing':
            try:
                # Try to get the most recent active conversation for this agent
                conversation = Conversation.objects.filter(
                    agent=agent,
                    user=agent.user,
                    status='active'
                ).order_by('-updated_at').first()
            except Exception:
                pass
        
        # If no conversation (or agent is testing), create a new one
        if not conversation:
            import uuid
            conversation = Conversation.objects.create(
                agent=agent,
                user=agent.user,
                conversation_id=str(uuid.uuid4()),
                status='active'
            )
        
        # Create agent loop
        agent_loop = AgentLoop(agent, conversation)
        
        # Get system prompt
        if not system_prompt:
            system_prompt = agent.configuration.get('instruction') or agent.configuration.get('system_prompt', '')
        
        # Execute agent loop
        result = agent_loop.execute(query, system_prompt)
        
        return {
            'response': result.get('response', ''),
            'tool_calls': result.get('tool_calls', []),
            'iterations': result.get('iterations', 1),
            'conversation_id': result.get('conversation_id', '')
        }
    
    @database_sync_to_async
    def save_test_result(self, agent, query, expected, actual, passed):
        """Save test result to database."""
        TestResult.objects.create(
            agent=agent,
            test_query=query,
            expected_response=expected,
            actual_response=actual,
            passed=passed,
            score=1.0 if passed else 0.0
        )
    
    @database_sync_to_async
    def get_timestamp(self):
        """Get current timestamp."""
        from django.utils import timezone
        return timezone.now().isoformat()
    
    async def handle_feedback(self, response_id, query, response, feedback_type, feedback_text):
        """Handle user feedback for an agent response."""
        try:
            agent = await self.get_agent()
            if not agent:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Agent not found'
                }))
                return
            
            # Validate feedback type
            valid_types = ['positive', 'negative', 'neutral']
            if feedback_type not in valid_types:
                feedback_type = 'neutral'
            
            # Save feedback
            await self.save_feedback(
                agent, query, response, feedback_type, feedback_text
            )
            
            # Trigger optimization if enough feedback collected
            optimization_result = await self.trigger_optimization(agent)
            
            # Send confirmation with optimization status
            confirmation_message = 'Thank you for your feedback! This helps us improve the agent.'
            if optimization_result.get('optimized'):
                confirmation_message += ' Agent instructions have been optimized based on feedback.'
            
            await self.send(text_data=json.dumps({
                'type': 'feedback_received',
                'response_id': response_id,
                'message': confirmation_message,
                'feedback_type': feedback_type,
                'optimization_applied': optimization_result.get('optimized', False)
            }))
        except Exception as e:
            logger.error(f"Error handling feedback: {str(e)}", exc_info=True)
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'Error saving feedback: {str(e)}'
            }))
    
    @database_sync_to_async
    def save_feedback(self, agent, query, response, feedback_type, feedback_text):
        """Save feedback to database."""
        AgentFeedback.objects.create(
            agent=agent,
            user=self.user,
            query=query,
            response=response,
            feedback_type=feedback_type,
            feedback_text=feedback_text
        )
    
    @database_sync_to_async
    def get_enhanced_context(self, agent, query):
        """Get enhanced context from feedback optimizer."""
        try:
            optimizer = FeedbackOptimizer(agent)
            return optimizer.get_enhanced_context(query)
        except Exception as e:
            logger.error(f"Error getting enhanced context: {str(e)}", exc_info=True)
            return []
    
    @database_sync_to_async
    def get_optimized_system_prompt(self, agent):
        """Get optimized system prompt based on feedback."""
        try:
            optimizer = FeedbackOptimizer(agent)
            if optimizer.should_use_enhanced_prompt():
                # Get the optimized instruction from configuration
                return agent.configuration.get('instruction') or agent.configuration.get('system_prompt')
            else:
                # Use original instruction
                return agent.configuration.get('instruction') or agent.configuration.get('system_prompt')
        except Exception as e:
            logger.error(f"Error getting optimized prompt: {str(e)}", exc_info=True)
            return agent.configuration.get('instruction') if agent.configuration else None
    
    @database_sync_to_async
    def trigger_optimization(self, agent):
        """Trigger agent optimization based on feedback."""
        try:
            optimizer = FeedbackOptimizer(agent)
            return optimizer.optimize_agent_instructions()
        except Exception as e:
            logger.error(f"Error triggering optimization: {str(e)}", exc_info=True)
            return {'optimized': False, 'error': str(e)}
    
    # Handler for group messages (if needed for broadcasting)
    async def chat_message(self, event):
        """Handle chat message from room group."""
        message = event['message']
        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            'message': message
        }))

