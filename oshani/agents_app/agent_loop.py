"""Agent loop implementation for agent processing."""
import json
import logging
import re
import os
from typing import Dict, Any, List, Optional
from .models import Agent, Conversation, ConversationMessage, ToolCall
from .tool_executor import ToolExecutor
from .conversation_memory import get_conversation_memory
from .conversation_session_state import (
    apply_user_message_to_session_state,
    build_session_state_prompt_block,
    structured_session_state_enabled,
    trim_conversation_history,
)
from .exam_question_bank import build_exam_deterministic_reply

logger = logging.getLogger(__name__)


class AgentLoop:
    """Implements the agent loop - planning, tool calling, and response generation."""
    
    MAX_ITERATIONS = 6   # Max loop iterations (avoid 10 LLM calls; break when no tool calls)
    MAX_TOOL_CALLS_PER_TURN = 8  # Safeguard: stop after this many tool calls in one user message
    MAX_RESPONSE_LENGTH = 8000  # Maximum characters for a response (to prevent runaway output)
    REPETITION_THRESHOLD = 3  # Number of times a phrase can repeat before being cut off
    
    def __init__(self, agent: Agent, conversation: Optional[Conversation] = None, user=None):
        self.agent = agent
        self.conversation = conversation
        self.user = user
        self.tool_executor = ToolExecutor(agent)
        self.iteration_count = 0
        
        # Check if this is a new conversation and send introduction if enabled
        if self.conversation:
            # Check if conversation has any messages (excluding tool calls/results)
            message_count = ConversationMessage.objects.filter(
                conversation=self.conversation
            ).exclude(
                message_type__in=['tool_call', 'tool_result']
            ).count()
            
            # If conversation is new (no messages), send introduction
            if message_count == 0:
                send_introduction = self.agent.configuration.get('send_introduction', True) if self.agent.configuration else True
                if send_introduction:
                    self._send_introduction(self.conversation)
        
        # Initialize LangChain memory for conversation context
        self._seen_phrases = set()  # Track seen phrases for repetition detection
        try:
            memory_type = agent.configuration.get('memory_type', 'buffer') if agent.configuration else 'buffer'
            max_token_limit = agent.configuration.get('memory_max_tokens') if agent.configuration else None
            self.memory = get_conversation_memory(
                conversation=conversation,
                memory_type=memory_type,
                max_token_limit=max_token_limit
            )
        except Exception as e:
            logger.warning(f"Failed to initialize LangChain memory: {str(e)}. Conversation context will be limited.")
            # Fallback: create a simple memory-like object
            self.memory = type('obj', (object,), {
                'get_conversation_history': lambda *args, **kwargs: "",
                'chat_memory': type('obj', (object,), {
                    'add_user_message': lambda *args, **kwargs: None,
                    'add_ai_message': lambda *args, **kwargs: None
                })()
            })()
    
    def _detect_repetition(self, text: str) -> tuple[bool, str]:
        """
        Detect if text contains excessive repetition.
        Returns (is_repetitive, cleaned_text).
        """
        if not text or len(text) < 100:
            return False, text
        
        # Look for repeating sentences/paragraphs
        sentences = re.split(r'[.!?\n]+', text)
        sentence_counts = {}
        
        for sentence in sentences:
            cleaned = sentence.strip().lower()
            if len(cleaned) > 30:  # Only track substantial sentences
                sentence_counts[cleaned] = sentence_counts.get(cleaned, 0) + 1
        
        # Check if any sentence repeats too many times
        for sentence, count in sentence_counts.items():
            if count >= self.REPETITION_THRESHOLD:
                logger.warning(f"Detected repetitive content: '{sentence[:50]}...' repeated {count} times")
                # Clean the text by keeping only unique sentences
                seen = set()
                unique_parts = []
                for part in sentences:
                    cleaned = part.strip().lower()
                    if cleaned and cleaned not in seen:
                        seen.add(cleaned)
                        unique_parts.append(part.strip())
                
                cleaned_text = '. '.join(unique_parts[:10])  # Keep first 10 unique sentences
                if cleaned_text and not cleaned_text.endswith('.'):
                    cleaned_text += '.'
                return True, cleaned_text
        
        return False, text
    
    def _structured_session_state_enabled(self) -> bool:
        return structured_session_state_enabled(self.agent)

    def _update_session_state_from_user_message(self, user_message: str) -> None:
        if not self.conversation or not self._structured_session_state_enabled():
            return
        try:
            apply_user_message_to_session_state(self.agent, self.conversation, user_message)
            self.conversation.refresh_from_db(fields=['session_state', 'updated_at'])
        except Exception as e:
            logger.warning(
                'Failed to update session_state for conversation %s: %s',
                getattr(self.conversation, 'conversation_id', None),
                e,
                exc_info=True,
            )

    def _get_session_state_prompt_block(self) -> str:
        if not self.conversation or not self._structured_session_state_enabled():
            return ''
        return build_session_state_prompt_block(self.conversation)

    def _try_exam_deterministic_reply(self, user_message: str) -> Optional[str]:
        """Grade MCQ and present next question from training-data bank (no LLM)."""
        if not self.conversation or not self._structured_session_state_enabled():
            return None
        try:
            return build_exam_deterministic_reply(self.agent, self.conversation, user_message)
        except Exception as e:
            logger.warning(
                'Exam deterministic reply failed for %s: %s',
                getattr(self.conversation, 'conversation_id', None),
                e,
                exc_info=True,
            )
            return None

    def _wrap_query_with_history_and_session_state(
        self, query: str, conversation_history: str, tool_instruction: bool = True,
    ) -> str:
        """Prepend session state and optional trimmed history before the current user query."""
        if self._structured_session_state_enabled() and conversation_history:
            conversation_history = trim_conversation_history(conversation_history, max_lines=8)

        session_block = self._get_session_state_prompt_block()
        tool_hint = (
            'When calling tools, fill parameters (e.g. text_prompt, url, duration) from the '
            'Previous Conversation when the user refers to it (e.g. \'that\', \'it\', \'go ahead\'). '
            'For video tools, use the user\'s stated video description or content from history as text_prompt.\n\n'
            if tool_instruction
            else ''
        )

        parts = []
        if session_block:
            parts.append(session_block)
        if conversation_history:
            parts.append(f"Previous Conversation:\n{conversation_history}\n\n{tool_hint}")
        parts.append(f"Current User Query: {query}")
        return '\n\n'.join(parts)

    def _truncate_if_too_long(self, text: str) -> str:
        """Truncate text if it exceeds maximum length."""
        if len(text) > self.MAX_RESPONSE_LENGTH:
            logger.warning(f"Response too long ({len(text)} chars), truncating to {self.MAX_RESPONSE_LENGTH}")
            text = text[:self.MAX_RESPONSE_LENGTH]
            # Try to end at a sentence boundary
            last_period = text.rfind('.')
            if last_period > self.MAX_RESPONSE_LENGTH * 0.8:
                text = text[:last_period + 1]
        return text

    def _summarize_conversation(self, text: str, max_chars: int) -> str:
        """Summarize conversation history efficiently, keeping recent context and key points from earlier messages."""
        if len(text) <= max_chars or max_chars < 200:
            return text[:max_chars] if len(text) > max_chars else text
        lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
        if not lines:
            return text[:max_chars] + "..."
        # Keep last N lines (most recent exchange) in full; summarize the rest
        reserve_recent = min(max_chars // 3, 1200)  # ~1/3 for recent
        recent_lines = []
        current = 0
        for i in range(len(lines) - 1, -1, -1):
            if current + len(lines[i]) + 1 <= reserve_recent:
                recent_lines.insert(0, lines[i])
                current += len(lines[i]) + 1
            else:
                break
        earlier = [ln for ln in lines if ln not in recent_lines]
        if not earlier:
            return '\n'.join(recent_lines)
        # Summarize earlier: one compact line per "message" (User/Assistant pattern or single line)
        summary_lines = []
        budget = max_chars - current - 80  # space for "[Earlier conversation summarized]: "
        for ln in earlier[:50]:  # cap at 50 lines to summarize
            if budget <= 0:
                break
            # First 60 chars of each line to preserve intent
            snippet = ln[:60] + ("..." if len(ln) > 60 else "")
            if len(snippet) + 2 <= budget:
                summary_lines.append(snippet)
                budget -= len(snippet) + 2
        prefix = "[Earlier conversation summarized]: "
        summarized = prefix + " | ".join(summary_lines)
        if summary_lines and len(summary_lines) < len(earlier):
            summarized += " ..."
        return summarized + "\n\n" + "\n".join(recent_lines)

    def _summarize_rag_or_text(self, text: str, max_chars: int) -> str:
        """Summarize RAG context or long text by keeping key sentences/chunk starts without losing context."""
        if len(text) <= max_chars or max_chars < 200:
            return text[:max_chars] if len(text) > max_chars else text
        import re
        chunks = re.split(r'\n\n+|\n(?=\s*\[\d+\])', text)
        kept = []
        length = 0
        for ch in chunks:
            ch = ch.strip()
            if not ch or len(ch) < 3:
                continue
            prefix = ""
            m = re.match(r'^(\[\d+\]\s*)', ch)
            if m:
                prefix = m.group(1)
                ch = ch[m.end():].strip()
            first_sent = ch.split('.')[0].strip() + '.' if '.' in ch else ch[:120]
            if len(first_sent) > 120:
                first_sent = first_sent[:117] + "..."
            line = (prefix + first_sent).strip()
            if not line:
                continue
            need = len(line) + (1 if kept else 0)
            if length + need <= max_chars - 35:
                kept.append(line)
                length += need
            else:
                break
        if not kept:
            return text[:max_chars - 20] + "... [summarized]"
        result = "\n".join(kept)
        if len(text) > length + 50:
            result += "\n... [further context summarized]"
        return result

    def execute_stream(self, query: str, system_prompt: Optional[str] = None):
        """Execute the agent loop with streaming response (generator, Ollama and Bedrock)."""
        # Initialize conversation if needed
        if not self.conversation:
            self.conversation = self._create_conversation()
        
        # Save user message
        user_msg = self._save_message('user', query)
        self._update_session_state_from_user_message(query)
        initial_user_query = query

        exam_reply = self._try_exam_deterministic_reply(query)
        if exam_reply:
            agent_msg = self._save_message('agent', exam_reply)
            yield {'type': 'chunk', 'content': exam_reply, 'done': False}
            yield {
                'type': 'complete',
                'message_id': agent_msg.id if agent_msg else None,
                'user_message_id': user_msg.id if user_msg else None,
                'conversation_id': self.conversation.conversation_id,
                'tool_calls': [],
                'iterations': 0,
                'was_repetitive': False,
                'response': exam_reply,
            }
            return
        
        # Get enhanced system prompt
        enhanced_system_prompt = self._get_enhanced_system_prompt(system_prompt)
        
        # Agent loop (streaming only for final response, not tool calls)
        tool_calls_made = []
        final_response = ""
        
        # Check if model supports streaming
        model_provider = self.agent.model.provider if self.agent.model else None
        supports_streaming = model_provider in ['ollama', 'bedrock']
        
        for iteration in range(self.MAX_ITERATIONS):
            self.iteration_count = iteration + 1
            
            # For streaming, we only stream the final response (no tool calls in streaming mode)
            # If tool calls are needed, we'll handle them non-streaming
            if iteration == 0:
                # First iteration - try to get streaming response
                if supports_streaming:
                    # Stream the response for both Ollama and Bedrock
                    for chunk in self._get_agent_response_stream(query, enhanced_system_prompt, tool_calls_made):
                        if chunk.get('type') == 'chunk':
                            yield chunk
                        elif chunk.get('type') == 'done':
                            final_response = chunk.get('full_response', '')
                            break
                        elif chunk.get('type') == 'error':
                            # Handle streaming error
                            yield chunk
                            return
                    
                    # Check for tool calls in the response (pass user input so url_resolver only when user provided a URL)
                    tool_calls = self._extract_tool_calls(final_response, user_message=initial_user_query)
                    self._fill_video_prompt_from_user(tool_calls, initial_user_query)
                    if tool_calls:
                        # Tool calls found - execute them and stream events
                        tool_results = []
                        for tool_call in tool_calls:
                            tool_name = tool_call.get('tool', 'unknown')
                            tool_params = tool_call.get('parameters', {})
                            
                            # Yield tool call start event
                            yield {
                                'type': 'tool_call_start',
                                'tool': tool_name,
                                'parameters': tool_params
                            }
                            
                            # Execute tool
                            result = self._execute_tool_call(tool_call)
                            tool_results.append(result)
                            
                            # Build tool call result for streaming (ensure serializable)
                            # Extract only serializable fields from result
                            serializable_result = {
                                'name': result.get('name', tool_name),
                                'result_content': result.get('result_content', ''),
                                'result_files': result.get('result_files', []),
                                'error': result.get('error', ''),
                                'state': result.get('state', 'done')
                            }
                            
                            tool_call_result = {
                                'tool': tool_name,
                                'parameters': tool_params,
                                'result': result
                            }
                            tool_calls_made.append(tool_call_result)
                            
                            # Yield tool call result event (use serializable version)
                            yield {
                                'type': 'tool_call_result',
                                'tool': tool_name,
                                'parameters': tool_params,
                                'result': serializable_result
                            }
                        
                        # Get non-streaming response with tool results
                        query = self._build_query_with_tool_results(query, tool_results)
                        final_response = self._get_agent_response(query, enhanced_system_prompt, tool_calls_made)
                        # If no more tool calls in this response, we're done (avoid redundant next iteration)
                        tool_calls = self._extract_tool_calls(final_response, user_message=initial_user_query)
                        self._fill_video_prompt_from_user(tool_calls, initial_user_query)
                        # Dedupe: skip already-executed tool calls this turn
                        if tool_calls and tool_calls_made:
                            seen_keys = {(m.get('tool'), json.dumps(m.get('parameters', {}), sort_keys=True)) for m in tool_calls_made}
                            tool_calls = [tc for tc in tool_calls if (tc.get('tool'), json.dumps(tc.get('parameters', {}), sort_keys=True)) not in seen_keys]
                        if not tool_calls:
                            break
                        if len(tool_calls_made) + len(tool_calls) > self.MAX_TOOL_CALLS_PER_TURN:
                            logger.warning(f"[LLM Optimization] Stopping after {len(tool_calls_made)} tool calls (cap {self.MAX_TOOL_CALLS_PER_TURN})")
                            break
                        # Response contains more tool calls: execute them, update query, then next iteration gets next response
                        tool_results = []
                        for tool_call in tool_calls:
                            tool_name = tool_call.get('tool', 'unknown')
                            tool_params = tool_call.get('parameters', {})
                            yield {'type': 'tool_call_start', 'tool': tool_name, 'parameters': tool_params}
                            result = self._execute_tool_call(tool_call)
                            tool_results.append(result)
                            tool_calls_made.append({
                                'tool': tool_name, 'parameters': tool_params, 'result': result
                            })
                            yield {
                                'type': 'tool_call_result',
                                'tool': tool_name,
                                'parameters': tool_params,
                                'result': {
                                    'name': result.get('name', tool_name),
                                    'result_content': result.get('result_content', ''),
                                    'result_files': result.get('result_files', []),
                                    'error': result.get('error', ''),
                                    'state': result.get('state', 'done')
                                }
                            }
                        query = self._build_query_with_tool_results(query, tool_results)
                    else:
                        # No tool calls, final response
                        break
                else:
                    # Non-streaming model, use regular execute
                    result = self.execute(query, system_prompt)
                    yield {
                        'type': 'chunk',
                        'content': result.get('response', ''),
                        'done': True
                    }
                    return
            else:
                # Subsequent iterations (after tool calls) - use non-streaming
                response = self._get_agent_response(query, enhanced_system_prompt, tool_calls_made)
                tool_calls = self._extract_tool_calls(response, user_message=initial_user_query)
                self._fill_video_prompt_from_user(tool_calls, initial_user_query)
                # Dedupe: skip already-executed tool calls this turn
                if tool_calls and tool_calls_made:
                    seen_keys = {(m.get('tool'), json.dumps(m.get('parameters', {}), sort_keys=True)) for m in tool_calls_made}
                    tool_calls = [tc for tc in tool_calls if (tc.get('tool'), json.dumps(tc.get('parameters', {}), sort_keys=True)) not in seen_keys]
                if tool_calls:
                    if len(tool_calls_made) + len(tool_calls) > self.MAX_TOOL_CALLS_PER_TURN:
                        logger.warning(f"[LLM Optimization] Stopping after {len(tool_calls_made)} tool calls (cap {self.MAX_TOOL_CALLS_PER_TURN})")
                        final_response = response
                        break
                    tool_results = []
                    for tool_call in tool_calls:
                        tool_name = tool_call.get('tool', 'unknown')
                        tool_params = tool_call.get('parameters', {})
                        
                        # Yield tool call start event
                        yield {
                            'type': 'tool_call_start',
                            'tool': tool_name,
                            'parameters': tool_params
                        }
                        
                        # Execute tool
                        result = self._execute_tool_call(tool_call)
                        tool_results.append(result)
                        
                        # Build tool call result for streaming (ensure serializable)
                        # Extract only serializable fields from result
                        serializable_result = {
                            'name': result.get('name', tool_name),
                            'result_content': result.get('result_content', ''),
                            'result_files': result.get('result_files', []),
                            'error': result.get('error', ''),
                            'state': result.get('state', 'done')
                        }
                        
                        tool_call_result = {
                            'tool': tool_name,
                            'parameters': tool_params,
                            'result': result
                        }
                        tool_calls_made.append(tool_call_result)
                        
                        # Yield tool call result event (use serializable version)
                        yield {
                            'type': 'tool_call_result',
                            'tool': tool_name,
                            'parameters': tool_params,
                            'result': serializable_result
                        }
                    
                    query = self._build_query_with_tool_results(query, tool_results)
                else:
                    final_response = response
                    break
        
        # Check for repetitive content and clean if necessary
        is_repetitive, cleaned_response = self._detect_repetition(final_response)
        if is_repetitive:
            logger.warning(f"Cleaned repetitive response from {len(final_response)} to {len(cleaned_response)} chars")
            final_response = cleaned_response
        
        # Truncate if too long
        final_response = self._truncate_if_too_long(final_response)
        
        # Save agent response
        agent_msg = self._save_message('agent', final_response)
        
        yield {
            'type': 'complete',
            'message_id': agent_msg.id if agent_msg else None,
            'user_message_id': user_msg.id if user_msg else None,
            'conversation_id': self.conversation.conversation_id,
            'tool_calls': tool_calls_made,
            'iterations': self.iteration_count,
            'was_repetitive': is_repetitive
        }
    
    def execute(self, query: str, system_prompt: Optional[str] = None) -> Dict[str, Any]:
        """Execute the agent loop for a query."""
        # Check cache first (only for queries without conversation context)
        from .cache_utils import get_cached_response, set_cached_response, get_training_data_hash
        
        # Determine if we should use cache:
        # - Use cache if there's no conversation OR if conversation has no previous messages
        # - Don't use cache if conversation has history (context makes responses unique)
        use_cache = False
        if self.conversation is None:
            use_cache = True
        else:
            # Check if conversation has any previous messages (excluding the current query)
            message_count = ConversationMessage.objects.filter(
                conversation=self.conversation
            ).exclude(
                message_type__in=['tool_call', 'tool_result']
            ).count()
            # Use cache if conversation exists but has no messages (new conversation)
            use_cache = message_count == 0
        
        if use_cache:
            # Get model info for cache key
            model_id = None
            if self.agent.model:
                model_id = self.agent.model.model_id
            
            # Get training data hash for cache invalidation
            training_data_hash = get_training_data_hash(self.agent)
            
            # Get enhanced system prompt for cache key
            enhanced_system_prompt = self._get_enhanced_system_prompt(system_prompt)
            
            # Try to get cached response
            cached_response = get_cached_response(
                agent_id=self.agent.id,
                query=query,
                system_prompt=enhanced_system_prompt,
                model_id=model_id,
                training_data_hash=training_data_hash
            )
            
            if cached_response:
                logger.info(f"Using cached response for agent {self.agent.id}")
                # Still save the user message and create conversation if needed
                if not self.conversation:
                    self.conversation = self._create_conversation()
                user_msg = self._save_message('user', query)
                agent_msg = self._save_message('agent', cached_response.get('response', ''))
                cached_response['user_message_id'] = user_msg.id if user_msg else None
                cached_response['message_id'] = agent_msg.id if agent_msg else None
                return cached_response
        
        # Get enhanced system prompt with tools information
        enhanced_system_prompt = self._get_enhanced_system_prompt(system_prompt)
        
        # Initialize conversation if needed
        if not self.conversation:
            self.conversation = self._create_conversation()
        
        # Save user message
        user_msg = self._save_message('user', query)
        self._update_session_state_from_user_message(query)
        initial_user_query = query

        exam_reply = self._try_exam_deterministic_reply(query)
        if exam_reply:
            agent_msg = self._save_message('agent', exam_reply)
            return {
                'success': True,
                'response': exam_reply,
                'message_id': agent_msg.id if agent_msg else None,
                'user_message_id': user_msg.id if user_msg else None,
                'conversation_id': self.conversation.conversation_id,
                'tool_calls': [],
                'iterations': 0,
            }
        
        # Agent loop
        final_response = None
        tool_calls_made = []
        
        for iteration in range(self.MAX_ITERATIONS):
            self.iteration_count = iteration + 1
            
            # Get agent response (may include tool calls)
            response = self._get_agent_response(query, enhanced_system_prompt, tool_calls_made)
            
            # Check if response contains tool calls (pass user input so url_resolver is only injected when user provided a URL)
            tool_calls = self._extract_tool_calls(response, user_message=initial_user_query)
            self._fill_video_prompt_from_user(tool_calls, initial_user_query)
            
            logger.info(f"Iteration {self.iteration_count}: Response length={len(response)}, Tool calls found={len(tool_calls)}")
            if tool_calls:
                logger.info(f"Tool calls to execute: {[tc.get('tool', 'unknown') for tc in tool_calls]}")
            
            # Dedupe: skip tool calls we already executed this turn (stops model from retrying same call)
            if tool_calls and tool_calls_made:
                seen_keys = {(m.get('tool'), json.dumps(m.get('parameters', {}), sort_keys=True)) for m in tool_calls_made}
                tool_calls = [tc for tc in tool_calls if (tc.get('tool'), json.dumps(tc.get('parameters', {}), sort_keys=True)) not in seen_keys]
                if not tool_calls:
                    logger.info("[LLM Optimization] All tool calls already executed this turn; using response as final")
                    final_response = response
                    break
            if tool_calls:
                # Safeguard: avoid runaway iterations (e.g. 10 iterations for 1 real tool call)
                if len(tool_calls_made) + len(tool_calls) > self.MAX_TOOL_CALLS_PER_TURN:
                    logger.warning(f"[LLM Optimization] Stopping after {len(tool_calls_made)} tool calls (cap {self.MAX_TOOL_CALLS_PER_TURN})")
                    final_response = response
                    break
                # Execute tools
                tool_results = []
                for tool_call in tool_calls:
                    result = self._execute_tool_call(tool_call)
                    tool_results.append(result)
                    tool_calls_made.append({
                        'tool': tool_call.get('tool'),
                        'parameters': tool_call.get('parameters', {}),
                        'result': result
                    })
                # Add tool results to context for next iteration
                query = self._build_query_with_tool_results(query, tool_results)
            else:
                # No tool calls, this is the final response
                final_response = response
                break
        
        if not final_response:
            final_response = response  # Use last response if loop ended
        
        # Check for repetitive content and clean if necessary
        is_repetitive, cleaned_response = self._detect_repetition(final_response)
        if is_repetitive:
            logger.warning(f"Cleaned repetitive response from {len(final_response)} to {len(cleaned_response)} chars")
            final_response = cleaned_response
        
        # Truncate if too long
        final_response = self._truncate_if_too_long(final_response)
        
        # Save agent response
        agent_msg = self._save_message('agent', final_response)
        
        result = {
            'response': final_response,
            'tool_calls': tool_calls_made,
            'iterations': self.iteration_count,
            'conversation_id': self.conversation.conversation_id,
            'user_message_id': user_msg.id if user_msg else None,
            'message_id': agent_msg.id if agent_msg else None,
            'was_repetitive': is_repetitive
        }
        
        # Cache the response (only if no tool calls were made, as tool results can vary)
        # Also only cache if this was a new conversation (not part of ongoing conversation)
        if use_cache and not tool_calls_made:
            try:
                model_id = None
                if self.agent.model:
                    model_id = self.agent.model.model_id
                
                training_data_hash = get_training_data_hash(self.agent)
                
                set_cached_response(
                    agent_id=self.agent.id,
                    query=query,
                    response_data=result,
                    system_prompt=enhanced_system_prompt,
                    model_id=model_id,
                    training_data_hash=training_data_hash
                )
            except Exception as e:
                logger.warning(f"Failed to cache response: {str(e)}")
        
        return result
    
    def _get_enhanced_system_prompt(self, base_system_prompt: Optional[str] = None) -> str:
        """Get system prompt enhanced with tools information and feedback optimization."""
        if not base_system_prompt:
            base_system_prompt = self.agent.configuration.get('instruction') or self.agent.configuration.get('system_prompt', '')
        
        # Check if agent has been optimized based on feedback
        # If configuration has 'last_optimized' and 'optimization_based_on_feedbacks', use optimized version
        if self.agent.configuration and self.agent.configuration.get('optimization_based_on_feedbacks'):
            # Agent has been optimized - the instruction already includes feedback-based improvements
            logger.debug(f"Using feedback-optimized instructions for agent {self.agent.id}")
        else:
            # Try to get feedback-based enhancements if available
            try:
                from .feedback_optimizer import FeedbackOptimizer
                optimizer = FeedbackOptimizer(self.agent)
                if optimizer.should_use_enhanced_prompt():
                    # Check if we should auto-optimize (if there's enough feedback but not yet optimized)
                    analysis = optimizer.analyze_feedback()
                    if analysis['total'] >= 5 and not self.agent.configuration.get('optimization_based_on_feedbacks'):
                        logger.info(f"Auto-optimizing agent {self.agent.id} based on {analysis['total']} feedbacks")
                        result = optimizer.optimize_agent_instructions()
                        if result.get('optimized'):
                            # Reload agent to get updated configuration
                            self.agent.refresh_from_db()
                            base_system_prompt = self.agent.configuration.get('instruction') or self.agent.configuration.get('system_prompt', '')
                            logger.info(f"Agent {self.agent.id} instructions auto-optimized based on feedback")
            except Exception as e:
                logger.warning(f"Failed to apply feedback optimization: {str(e)}")
        
        # Add tools information
        tools_info = self.tool_executor.format_tools_for_prompt()
        
        enhanced_prompt = base_system_prompt
        if tools_info:
            enhanced_prompt += tools_info
        
        # Add concise agent loop instructions (optimized for token usage)
        enhanced_prompt += (
            "\n\nTOOLS — CRITICAL FORMAT: When you need to call any tool, your entire assistant message must be "
            "ONLY valid JSON: {\"tool_calls\":[{\"tool\":\"<name>\",\"parameters\":{...}}]} with no text before or after "
            "(no <reasoning> tags, no markdown ``` fences, no \"Here is\" or \"I will\" preamble). "
            "Use exact tool names from the Tools list (e.g. url_resolver, text_to_image, text_to_video, web_search).\n"
            "(0) Still images (text_to_image): If the user asks to generate, create, draw, render, illustrate, design, "
            "or produce a still image, picture, graphic, illustration, logo, banner, thumbnail, infographic visual, "
            "or social-post image (including \"image for LinkedIn\" / \"post visual\"), your NEXT message must be ONLY: "
            '{"tool_calls":[{"tool":"text_to_image","parameters":{"text_prompt":"<one string: full scene, subjects, '
            'style, lighting, colors, composition, any text shown in-image>","aspect_ratio":"16:9"}}]} '
            "Choose aspect_ratio 1:1, 16:9, or 9:16 to match the request; use 1:1 if unspecified. "
            "Put the user's topic and constraints inside text_prompt—never an empty or placeholder prompt. "
            "Do not output planning, chain-of-thought, or English explanation instead of this JSON on that turn. "
            "After the tool returns in a later turn, you may answer in normal prose and include the returned image URL.\n"
            "(1) Video from text: set text_prompt to the user's exact description or a close paraphrase of what they asked for—never substitute a different topic or generic phrase. Call text_to_video with that detailed text_prompt (subject, action, setting, lighting, style as a video caption). Example: {\"tool_calls\": [{\"tool\": \"text_to_video\", \"parameters\": {\"text_prompt\": \"Cinematic shot of a cat playing piano in a sunlit room. Soft lighting, 4K.\", \"duration\": \"9s\"}}]} — always put the real scene description in text_prompt, never \"...\". "
            "(2) Video from image: call image_to_video with image_path and a text_prompt that describes the desired motion or scene (e.g. camera movement, action); be specific. "
            "(3) Video from URL/domain: call url_resolver only when the user's message contains a URL or domain; do not call url_resolver for video creation if the user did not provide a URL. When the user did provide a URL, call url_resolver with that URL first, then use the returned content to build a detailed text_prompt for text_to_video (or image_to_video if they also provide an image). "
            "(4) Video length: default 9 seconds; up to 2 minutes (12–120s in 6s steps). If the user did not specify, use 9s or 12s then call the video tool with the duration parameter. "
            "(5) When the user asks to create or generate a video, ALWAYS use the chat context and history: use any existing data already in the conversation—e.g. url_resolver result, script, storyboard, blueprint, user instructions (duration, style, sound)—to build the text_prompt and narration. Do not call url_resolver unless the user's input includes a URL or domain. If url_resolver was already called in this conversation, use that content; do not call url_resolver again. If a script or storyboard exists in the conversation, use it for text_to_video prompt and text_to_speech script. Default to video WITH SOUND: (a) url_resolver only if no website content in history yet; (b) text_to_video with a detailed text_prompt from the existing content/script/storyboard; (c) text_to_speech with the script or summary from context; (d) combine_video_audio; (e) return the final video link. Skip (c)–(d) only if the user explicitly asked for silent video. Output tool_calls JSON; do not output long reasoning instead of tool calls. "
            "(6) Listing or managing agents: when the user asks to list agents, create an agent, get/update/delete an agent, or manage agents, use the MCP tools if available—e.g. a tool whose name contains list_agents (to list agents), create_agent, get_agent, update_agent, delete_agent. Call the exact tool name from the Tools list above. If a previous attempt failed with 'Available images (use one as image_path): ...', use one of those filenames. Do not output reasoning instead of a tool call when you can act; respond with ONLY JSON when using tools. For building/architecture context use svg_diagram."
        )
        
        return enhanced_prompt
    
    def _create_conversation(self) -> Conversation:
        """Create a new conversation and send introduction if enabled."""
        import uuid
        conversation_id = str(uuid.uuid4())
        conversation = Conversation.objects.create(
            agent=self.agent,
            user=self.agent.user,
            conversation_id=conversation_id
        )
        
        # Send introduction if enabled
        send_introduction = self.agent.configuration.get('send_introduction', True) if self.agent.configuration else True
        if send_introduction:
            self._send_introduction(conversation)
        
        return conversation
    
    def _send_introduction(self, conversation: Conversation):
        """Send agent introduction message when a new conversation starts."""
        try:
            # Generate introduction from agent name and description
            introduction_parts = []
            
            if self.agent.name:
                introduction_parts.append(f"Hello! I'm **{self.agent.name}**.")
            
            if self.agent.description:
                introduction_parts.append(self.agent.description)
            elif self.agent.configuration and self.agent.configuration.get('instruction'):
                # Use instruction as fallback if no description
                instruction = self.agent.configuration.get('instruction', '')
                # Take first sentence or first 200 chars
                if instruction:
                    first_sentence = instruction.split('.')[0] if '.' in instruction else instruction[:200]
                    introduction_parts.append(f"I'm here to help you with: {first_sentence}")
            
            if not introduction_parts:
                introduction_parts.append("Hello! I'm here to help you.")
            
            introduction_parts.append("How can I assist you today?")
            
            introduction_text = " ".join(introduction_parts)
            
            # Save introduction as agent message
            self._save_message('agent', introduction_text)
            
            logger.info(f"Sent introduction for agent {self.agent.id} in conversation {conversation.conversation_id}")
        except Exception as e:
            logger.warning(f"Failed to send introduction: {str(e)}", exc_info=True)
            # Don't fail conversation creation if introduction fails
    
    def _save_message(self, message_type: str, content: str, tool_name: str = '', tool_parameters: Dict = None, tool_result: Dict = None):
        """Save a message to the conversation and update LangChain memory."""
        # Save to database
        message = ConversationMessage.objects.create(
            conversation=self.conversation,
            message_type=message_type,
            content=content,
            tool_name=tool_name,
            tool_parameters=tool_parameters or {},
            tool_result=tool_result or {}
        )
        return message
        
        # Update LangChain memory (only for user and agent messages)
        # This ensures memory is synced immediately after saving to database
        try:
            if message_type == 'user' and hasattr(self.memory, 'chat_memory'):
                self.memory.chat_memory.add_user_message(content)
                logger.debug(f"Added user message to memory: {content[:50]}...")
            elif message_type == 'agent' and hasattr(self.memory, 'chat_memory'):
                self.memory.chat_memory.add_ai_message(content)
                logger.debug(f"Added agent message to memory: {content[:50]}...")
        except Exception as e:
            logger.warning(f"Failed to update memory after saving {message_type} message: {str(e)}", exc_info=True)
    
    def _get_agent_response(self, query: str, system_prompt: str, tool_results: List[Dict]) -> str:
        """Get response from agent (sync operation - will be wrapped in async context)."""
        import time
        start_time = time.time()
        
        from .aws_integration import get_bedrock_client
        # Pass user to bedrock client for subscription checks
        from .ollama_integration import OllamaClient, is_ollama_available
        from .rag_service import get_rag_service
        
        # Get conversation history - try multiple methods for robustness
        history_start = time.time()
        conversation_history = ""
        
        # Method 1: Try to get from LangChain memory
        try:
            get_history = getattr(self.memory, 'get_conversation_history', None)
            if callable(get_history):
                conversation_history = get_history()
                if conversation_history:
                    logger.debug(f"Retrieved conversation history from memory ({len(conversation_history)} chars)")
        except Exception as e:
            logger.debug(f"Error getting history from memory: {str(e)}")
        
        # Method 2: If memory failed or returned empty, load directly from database
        if not conversation_history and self.conversation:
            try:
                # Load all messages from database (excluding tool calls)
                messages = ConversationMessage.objects.filter(
                    conversation=self.conversation
                ).exclude(
                    message_type__in=['tool_call', 'tool_result']
                ).order_by('created_at')
                
                history_parts = []
                # Exclude the current query from history to avoid duplication
                # (it will be included separately as "Current User Query")
                for msg in messages:
                    # Skip the most recent user message if it matches the current query
                    # This prevents duplication since we'll add it as "Current User Query"
                    if msg.message_type == 'user' and msg.content.strip() == query.strip():
                        continue
                    if msg.message_type == 'user':
                        history_parts.append(f"User: {msg.content}")
                    elif msg.message_type == 'agent':
                        history_parts.append(f"Assistant: {msg.content}")
                
                if history_parts:
                    conversation_history = "\n".join(history_parts)
                    logger.info(f"Loaded {len(history_parts)} messages from database for conversation {self.conversation.conversation_id}")
                    
                    # Also update memory with database messages for future use
                    try:
                        if hasattr(self.memory, 'chat_memory'):
                            # Clear and reload memory from database
                            if hasattr(self.memory.chat_memory, 'clear'):
                                self.memory.chat_memory.clear()
                            for msg in messages:
                                if msg.message_type == 'user':
                                    self.memory.chat_memory.add_user_message(msg.content)
                                elif msg.message_type == 'agent':
                                    self.memory.chat_memory.add_ai_message(msg.content)
                    except Exception as e:
                        logger.debug(f"Failed to sync memory with database: {str(e)}")
            except Exception as e:
                logger.warning(f"Error loading conversation history from database: {str(e)}", exc_info=True)
        
        # Method 3: Fallback to memory messages if database also failed
        if not conversation_history:
            try:
                if hasattr(self.memory, 'chat_memory') and hasattr(self.memory.chat_memory, 'messages'):
                    messages = self.memory.chat_memory.messages
                    history_parts = []
                    for msg in messages:
                        # Handle different message types
                        if hasattr(msg, 'content'):
                            if hasattr(msg, 'type'):
                                if msg.type == 'human':
                                    history_parts.append(f"User: {msg.content}")
                                elif msg.type == 'ai':
                                    history_parts.append(f"Assistant: {msg.content}")
                            else:
                                # Try to infer from class name
                                class_name = msg.__class__.__name__
                                if 'Human' in class_name:
                                    history_parts.append(f"User: {msg.content}")
                                elif 'AI' in class_name:
                                    history_parts.append(f"Assistant: {msg.content}")
                    if history_parts:
                        conversation_history = "\n".join(history_parts)
                        logger.debug(f"Retrieved {len(history_parts)} messages from memory fallback")
            except Exception as e:
                logger.debug(f"Error in memory fallback: {str(e)}")
        
        # Log if no history was found
        if not conversation_history:
            logger.warning(f"No conversation history found for conversation {self.conversation.conversation_id if self.conversation else 'None'}")
        
        history_duration = time.time() - history_start
        logger.info(f"[Performance] Conversation history loading: {history_duration:.3f}s")
        
        # Build query with tool results if any
        if tool_results:
            query_with_context = f"{query}\n\nTool Results:\n"
            for i, result in enumerate(tool_results, 1):
                tool_name = result.get('tool', 'unknown')
                tool_result = result.get('result', {})
                query_with_context += f"\n[{i}] Tool: {tool_name}\n"
                query_with_context += f"Result: {json.dumps(tool_result, indent=2)}\n"
            query = query_with_context
        
        # Get model information early to determine token limits
        if not self.agent.model:
            raise ValueError("Agent must have a model configured")
        
        model = self.agent.model
        model_provider = model.provider
        model_id = model.model_id
        
        # Check if this is a model with 8192 token limit (needed for early truncation)
        model_has_8192_limit = False
        if model_id:
            # Models known to have 8192 token context limits
            models_with_8192_limit = [
                'anthropic.claude-3-haiku',
                'anthropic.claude-3-sonnet',
                'anthropic.claude-3-opus',
                'amazon.nova-lite',
                'amazon.nova-micro',
                'amazon.nova-pro',
            ]
            model_has_8192_limit = any(limit_model in model_id.lower() for limit_model in models_with_8192_limit)
        
        session_block = self._get_session_state_prompt_block()
        # Add conversation history to the query if available (or session state alone)
        if conversation_history or session_block:
            if model_has_8192_limit and conversation_history and not self._structured_session_state_enabled():
                max_history_chars = 2000
                if len(conversation_history) > max_history_chars:
                    lines = conversation_history.split('\n')
                    truncated_lines = []
                    current_length = 0
                    for line in reversed(lines):
                        if current_length + len(line) + 1 <= max_history_chars:
                            truncated_lines.insert(0, line)
                            current_length += len(line) + 1
                        else:
                            break
                    conversation_history = "... [earlier conversation truncated] ...\n" + '\n'.join(truncated_lines)
                    logger.info(
                        f"[LLM Optimization] Truncated conversation history to {len(conversation_history)} chars for 8192-token model"
                    )
            query = self._wrap_query_with_history_and_session_state(query, conversation_history)
            if session_block:
                logger.info(
                    'Including DB session_state block (%s chars) for conversation %s',
                    len(session_block),
                    self.conversation.conversation_id if self.conversation else None,
                )
            if conversation_history:
                logger.info(
                    f"Including conversation history ({len(conversation_history)} chars, "
                    f"~{len(conversation_history.split(chr(10)))} messages) in prompt"
                )
        else:
            logger.debug('No conversation history or session state to include in prompt')
        
        # If model is not available, try to use inference profile
        if not model.is_available:
            logger.warning(f"Model {model.model_id} is not available, checking for inference profile fallback")
            
            # Check if agent has an inference profile configured
            if self.agent.inference_profile and self.agent.inference_profile.is_available():
                logger.info(f"Using inference profile {self.agent.inference_profile.profile_name} as fallback")
                # Use the inference profile's model if it's available
                inference_model = self.agent.inference_profile.model
                if inference_model and inference_model.is_available:
                    model = inference_model
                    model_provider = model.provider
                    model_id = model.model_id
                    logger.info(f"Switched to inference profile model: {model_id}")
                else:
                    logger.warning(f"Inference profile model {inference_model.model_id if inference_model else 'None'} is also not available")
            else:
                # Try to find any available inference profile for this model or compatible models
                from .models import InferenceProfile
                # First, try to find inference profiles for the same model
                available_profiles = InferenceProfile.objects.filter(
                    model=model,
                    status='active'
                ).exclude(profile_arn__isnull=True).exclude(profile_arn='')
                
                # If none found, try to find inference profiles for models with same provider
                if not available_profiles.exists():
                    available_profiles = InferenceProfile.objects.filter(
                        model__provider=model_provider,
                        model__is_available=True,
                        status='active'
                    ).exclude(profile_arn__isnull=True).exclude(profile_arn='')
                
                if available_profiles.exists():
                    # Use the first available inference profile
                    fallback_profile = available_profiles.first()
                    logger.info(f"Found available inference profile {fallback_profile.profile_name} for fallback")
                    self.agent.inference_profile = fallback_profile
                    self.agent.save(update_fields=['inference_profile'])
                    
                    # Use the inference profile's model
                    inference_model = fallback_profile.model
                    if inference_model and inference_model.is_available:
                        model = inference_model
                        model_provider = model.provider
                        model_id = model.model_id
                        logger.info(f"Switched to inference profile model: {model_id}")
                    else:
                        logger.warning(f"Inference profile model {inference_model.model_id if inference_model else 'None'} is not available")
                else:
                    logger.warning(f"No available inference profiles found for model {model.model_id} or compatible models")
        
        # Use RAG to retrieve relevant context instead of sending all training data
        rag_start = time.time()
        rag_context = ""
        use_rag = self.agent.configuration.get('use_rag', True)  # Default to True
        
        if use_rag:
            try:
                # Determine embedding provider based on model provider
                embedding_provider = 'bedrock' if model_provider == 'bedrock' else 'ollama'
                
                # Get vector store backend from settings or agent configuration
                from django.conf import settings
                vector_store_backend = getattr(settings, 'RAG_VECTOR_STORE_BACKEND', 'qdrant')
                if self.agent.configuration and 'rag_vector_store_backend' in self.agent.configuration:
                    vector_store_backend = self.agent.configuration['rag_vector_store_backend']
                
                rag_service = get_rag_service(
                    embedding_provider=embedding_provider,
                    vector_store_backend=vector_store_backend
                )
                
                # OPTIMIZATION: Extract only the user query for embedding, not the full conversation history
                # The conversation history is included in the final prompt, but for RAG retrieval,
                # we only need to embed the actual user query to find relevant context
                rag_query = query
                
                # If query contains "Current User Query:", extract just that part
                if "Current User Query:" in rag_query:
                    parts = rag_query.split("Current User Query:")
                    if len(parts) > 1:
                        rag_query = parts[-1].strip()
                        # Also remove "Previous Conversation:" prefix if present
                        if rag_query.startswith("Previous Conversation:"):
                            rag_query = rag_query.split("\n\n", 1)[-1].strip()
                
                # If query contains "Tool Results:", extract just the original query part
                if "Tool Results:" in rag_query:
                    parts = rag_query.split("Tool Results:")
                    rag_query = parts[0].strip()
                
                # Limit query length for embedding (embeddings work better with concise queries)
                # Take first 500 characters or first sentence, whichever is shorter
                if len(rag_query) > 500:
                    # Try to find a sentence boundary
                    sentence_end = rag_query[:500].rfind('.')
                    if sentence_end > 200:  # Only truncate at sentence if reasonable
                        rag_query = rag_query[:sentence_end + 1]
                    else:
                        rag_query = rag_query[:500]
                
                logger.debug(f"[RAG Optimization] Using query for embedding: {rag_query[:100]}... (length: {len(rag_query)})")
                
                # Try to get enhanced context from feedback optimizer for similar queries
                try:
                    from .feedback_optimizer import FeedbackOptimizer
                    optimizer = FeedbackOptimizer(self.agent)
                    enhanced_contexts = optimizer.get_enhanced_context(rag_query)  # Use optimized query
                    if enhanced_contexts:
                        logger.debug(f"Found {len(enhanced_contexts)} similar queries with positive feedback")
                except Exception as e:
                    logger.debug(f"Could not get enhanced context from feedback: {str(e)}")
                    enhanced_contexts = []
                
                # Retrieve relevant chunks using optimized query
                rag_context = rag_service.get_retrieval_context(
                    agent_id=self.agent.id,
                    query=rag_query,  # Use optimized query for embedding
                    top_k=5  # Get top 5 most relevant chunks
                )
                
                if rag_context:
                    logger.info(f"Retrieved RAG context for agent {self.agent.id}")
            except Exception as e:
                logger.warning(f"RAG retrieval failed, falling back to full training data: {str(e)}")
                use_rag = False
        
        rag_duration = time.time() - rag_start
        logger.info(f"[Performance] RAG retrieval: {rag_duration:.3f}s")
        
        # Extract training data content (optimized: skip if RAG provided context)
        # When RAG is enabled and successful, we don't need to load all training data
        # When RAG is disabled or failed, we use full training data
        training_data_start = time.time()
        training_data_list = []
        
        # Only load training data if RAG is disabled or failed
        if not use_rag or not rag_context:
            training_data_objs = self.agent.training_data.all()
            for td in training_data_objs:
                # Extract actual content from training data
                content_text = self._extract_training_data_content(td)
                if content_text:
                    training_data_list.append({
                        'content': {'text': content_text},
                        'data_type': td.data_type,
                    })
        else:
            logger.debug(f"[Performance] Skipping training data loading (RAG provided context)")
        
        training_data_duration = time.time() - training_data_start
        if training_data_list:
            logger.info(f"[Performance] Training data loading: {training_data_duration:.3f}s ({len(training_data_list)} items)")
        
        # Build final query with RAG context if available
        final_query = query
        if rag_context:
            # OPTIMIZATION: Limit RAG context length (reduced for token efficiency)
            # For 8192 token models, use stricter limits
            if model_has_8192_limit:
                max_rag_context_length = 1500  # characters (roughly 430 tokens) - stricter for 8192 models
            else:
                max_rag_context_length = 2000  # characters (roughly 570 tokens)
            
            original_rag_length = len(rag_context)
            if len(rag_context) > max_rag_context_length:
                rag_context = self._summarize_rag_or_text(rag_context, max_rag_context_length)
                logger.debug(f"[LLM Optimization] Summarized RAG context from {original_rag_length} to {len(rag_context)} chars (key points preserved)")
            
            final_query = f"{rag_context}\n\nUser Query: {query}"
        # If RAG is disabled or failed, include training data in the query directly (optimized)
        elif training_data_list:
            training_context = "\n\nKnowledge:\n"
            max_training_items = 3  # Reduced from 5
            max_item_length = 600  # Reduced from 1000 chars (~170 tokens per item)
            
            for idx, data_item in enumerate(training_data_list[:max_training_items], 1):
                text_content = data_item.get('content', {}).get('text', '')
                if text_content:
                    # Limit each training data item length
                    if len(text_content) > max_item_length:
                        text_content = text_content[:max_item_length] + "..."
                    training_context += f"[{idx}] {text_content}\n"
            
            if len(training_data_list) > max_training_items:
                training_context += f"[{len(training_data_list) - max_training_items} more items omitted]\n"
            
            final_query = f"{training_context}\nQuery: {query}"
        
        # OPTIMIZATION: Calculate actual token budget and manage context
        # Model limit: Use model_has_8192_limit already determined earlier
        if model_has_8192_limit:
            # Stricter limits for 8192 token models
            # Reserve: ~1000 tokens for response, ~500 tokens for system prompt overhead, ~200 tokens buffer
            # Available for user content: ~6500 tokens
            max_tokens_for_content = 6500
            max_chars_for_content = max_tokens_for_content * 3.5  # ~22750 chars
            logger.info(f"[LLM Optimization] Using strict token limits for 8192-token model: {model_id}")
        else:
            # Standard limits for larger context models
            # Reserve: ~1000 tokens for response, ~500 tokens for system prompt overhead
            # Available for user content: ~6600 tokens
            max_tokens_for_content = 6600
            max_chars_for_content = max_tokens_for_content * 3.5  # ~23100 chars
        
        # Calculate current sizes
        system_prompt_size = len(system_prompt) if system_prompt else 0
        final_query_size = len(final_query)
        total_size = system_prompt_size + final_query_size
        
        # Estimate tokens (conservative: 3.5 chars per token)
        estimated_tokens = int(total_size / 3.5)
        
        logger.info(f"[LLM Performance] System prompt: {system_prompt_size} chars (~{int(system_prompt_size/3.5)} tokens), Query: {final_query_size} chars (~{int(final_query_size/3.5)} tokens), Total: {total_size} chars (~{estimated_tokens} tokens)")
        
        # For 8192 token models, also truncate system prompt if needed
        if model_has_8192_limit and system_prompt_size > 2000:
            max_system_prompt_chars = 2000
            original_system_prompt = system_prompt
            system_prompt = system_prompt[:max_system_prompt_chars] + "... [system prompt truncated]"
            system_prompt_size = len(system_prompt)
            logger.warning(f"[LLM Optimization] Truncated system prompt from {len(original_system_prompt)} to {system_prompt_size} chars for 8192-token model")
        
        # Extract user query first (must keep)
        user_query_part = query
        if "Current User Query:" in final_query:
            parts = final_query.split("Current User Query:")
            if len(parts) > 1:
                user_query_part = parts[-1].strip()
        elif "User Query:" in final_query:
            parts = final_query.split("User Query:")
            if len(parts) > 1:
                user_query_part = parts[-1].strip()
        
        # If prompt exceeds token budget, summarize content efficiently instead of truncating (preserve context)
        if estimated_tokens > max_tokens_for_content:
            logger.warning(f"[LLM Optimization] Prompt too large ({estimated_tokens} tokens > {max_tokens_for_content}), summarizing content to preserve context")
            excess_tokens = estimated_tokens - max_tokens_for_content
            excess_chars = int(excess_tokens * 3.5)

            if "Previous Conversation:" in final_query:
                parts = final_query.split("Previous Conversation:")
                if len(parts) > 1:
                    conversation_and_query = parts[1]
                    if "\n\nCurrent User Query:" in conversation_and_query:
                        conversation_part = conversation_and_query.split("\n\nCurrent User Query:")[0]
                    elif "\n\nUser Query:" in conversation_and_query:
                        conversation_part = conversation_and_query.split("\n\nUser Query:")[0]
                    else:
                        conversation_part = conversation_and_query

                    rag_context_length = len(rag_context) if rag_context else 0
                    reserved_space = len(user_query_part) + rag_context_length + system_prompt_size + 500
                    available_space = max_chars_for_content - reserved_space

                    if len(conversation_part) > available_space and available_space > 300:
                        conversation_part = self._summarize_conversation(conversation_part, available_space)
                        if rag_context:
                            final_query = f"{rag_context}\n\nPrevious Conversation:\n{conversation_part}\n\nCurrent User Query: {user_query_part}"
                        else:
                            final_query = f"Previous Conversation:\n{conversation_part}\n\nCurrent User Query: {user_query_part}"
                        logger.info(f"[LLM Optimization] Summarized conversation history to {len(conversation_part)} chars (context preserved)")
                    else:
                        if rag_context_length > 0 and len(conversation_part) + rag_context_length + len(user_query_part) + system_prompt_size > max_chars_for_content:
                            available_for_rag = max_chars_for_content - len(conversation_part) - len(user_query_part) - system_prompt_size - 500
                            if available_for_rag > 500:
                                rag_context = self._summarize_rag_or_text(rag_context, available_for_rag)
                                final_query = f"{rag_context}\n\nPrevious Conversation:\n{conversation_part}\n\nCurrent User Query: {user_query_part}"
                                logger.info(f"[LLM Optimization] Summarized RAG context to {len(rag_context)} chars (key points preserved)")
            else:
                if rag_context and len(rag_context) + len(user_query_part) + system_prompt_size > max_chars_for_content:
                    available_for_rag = max_chars_for_content - len(user_query_part) - system_prompt_size - 500
                    if available_for_rag > 500:
                        rag_context = self._summarize_rag_or_text(rag_context, available_for_rag)
                        final_query = f"{rag_context}\n\nUser Query: {user_query_part}"
                        logger.info(f"[LLM Optimization] Summarized RAG context to {len(rag_context)} chars (key points preserved)")
        
        # Get response
        llm_start = time.time()
        if model_provider == 'ollama':
            if not is_ollama_available():
                raise ValueError(
                    "Ollama is not configured or not reachable. Set OLLAMA_ENABLED and OLLAMA_BASE_URL and ensure the server is running."
                )
            client = OllamaClient()
            result = client.invoke_agent(
                str(self.agent.id),
                final_query,
                model=model_id,
                system_prompt=system_prompt,
                training_data=training_data_list if training_data_list else None
            )
        else:
            # For shared agents, use agent owner's subscription for billing/access checks
            # The user parameter is used for subscription checks, but billing goes to agent owner
            billing_user = self.agent.user if self.agent.user != self.user else self.user
            client = get_bedrock_client(user=billing_user)
            # Get inference profile ARN if agent has one configured
            inference_profile_arn = None
            if self.agent.inference_profile and self.agent.inference_profile.profile_arn:
                inference_profile_arn = self.agent.inference_profile.profile_arn
            # Pass agent.id for ownership checks (not quick_suite_agent_id which may not be unique)
            agent_id_for_invoke = self.agent.quick_suite_agent_id or str(self.agent.id)
            result = client.invoke_agent(
                agent_id_for_invoke,
                final_query,
                model=model_id,
                system_prompt=system_prompt,
                model_provider=model_provider,
                training_data=training_data_list if training_data_list else None,
                inference_profile_arn=inference_profile_arn,
                user=billing_user,  # Use agent owner for subscription checks
                agent_db_id=self.agent.id  # Pass actual DB ID for ownership lookup
            )
        
        llm_duration = time.time() - llm_start
        total_duration = time.time() - start_time
        logger.info(f"[Performance] LLM inference: {llm_duration:.3f}s")
        logger.info(f"[Performance] Total _get_agent_response: {total_duration:.3f}s (history: {history_duration:.3f}s, RAG: {rag_duration:.3f}s, training_data: {training_data_duration:.3f}s, LLM: {llm_duration:.3f}s)")
        
        return result.get('response', '')
    
    def _get_agent_response_stream(self, query: str, system_prompt: str, tool_results: List[Dict]):
        """Get streaming response from agent (generator for Ollama and Bedrock)."""
        import time
        start_time = time.time()
        
        from .ollama_integration import OllamaClient, is_ollama_available
        from .aws_integration import get_bedrock_client
        from .rag_service import get_rag_service
        
        # Get conversation history (same as non-streaming)
        history_start = time.time()
        conversation_history = ""
        
        # Method 1: Try to get from LangChain memory
        try:
            get_history = getattr(self.memory, 'get_conversation_history', None)
            if callable(get_history):
                conversation_history = get_history()
                if conversation_history:
                    logger.debug(f"Retrieved conversation history from memory ({len(conversation_history)} chars)")
        except Exception as e:
            logger.debug(f"Error getting history from memory: {str(e)}")
        
        # Method 2: If memory failed or returned empty, load directly from database
        if not conversation_history and self.conversation:
            try:
                messages = ConversationMessage.objects.filter(
                    conversation=self.conversation
                ).exclude(
                    message_type__in=['tool_call', 'tool_result']
                ).order_by('created_at')
                
                history_parts = []
                for msg in messages:
                    if msg.message_type == 'user' and msg.content.strip() == query.strip():
                        continue
                    if msg.message_type == 'user':
                        history_parts.append(f"User: {msg.content}")
                    elif msg.message_type == 'agent':
                        history_parts.append(f"Assistant: {msg.content}")
                
                if history_parts:
                    conversation_history = "\n".join(history_parts)
                    logger.info(f"Loaded {len(history_parts)} messages from database for conversation {self.conversation.conversation_id}")
                    # Sync memory from DB (same as non-streaming) so next turn has correct history
                    try:
                        if hasattr(self.memory, 'chat_memory'):
                            if hasattr(self.memory.chat_memory, 'clear'):
                                self.memory.chat_memory.clear()
                            for msg in messages:
                                if msg.message_type == 'user':
                                    self.memory.chat_memory.add_user_message(msg.content)
                                elif msg.message_type == 'agent':
                                    self.memory.chat_memory.add_ai_message(msg.content)
                    except Exception as e:
                        logger.debug(f"Failed to sync memory from database (streaming): {str(e)}")
            except Exception as e:
                logger.warning(f"Error loading conversation history from database: {str(e)}", exc_info=True)
        
        history_duration = time.time() - history_start
        
        # Build query with tool results if any
        if tool_results:
            query_with_context = f"{query}\n\nTool Results:\n"
            for i, result in enumerate(tool_results, 1):
                tool_name = result.get('tool', 'unknown')
                tool_result = result.get('result', {})
                query_with_context += f"\n[{i}] Tool: {tool_name}\n"
                query_with_context += f"Result: {json.dumps(tool_result, indent=2)}\n"
            query = query_with_context
        
        session_block = self._get_session_state_prompt_block()
        if conversation_history or session_block:
            query = self._wrap_query_with_history_and_session_state(query, conversation_history)

        # Get model information
        if not self.agent.model:
            raise ValueError("Agent must have a model configured")
        
        model = self.agent.model
        model_provider = model.provider
        model_id = model.model_id
        
        # Use RAG to retrieve relevant context
        rag_start = time.time()
        rag_context = ""
        use_rag = self.agent.configuration.get('use_rag', True)
        
        if use_rag:
            try:
                embedding_provider = 'bedrock' if model_provider == 'bedrock' else 'ollama'
                from django.conf import settings
                vector_store_backend = getattr(settings, 'RAG_VECTOR_STORE_BACKEND', 'qdrant')
                if self.agent.configuration and 'rag_vector_store_backend' in self.agent.configuration:
                    vector_store_backend = self.agent.configuration['rag_vector_store_backend']
                
                rag_service = get_rag_service(
                    embedding_provider=embedding_provider,
                    vector_store_backend=vector_store_backend
                )
                
                rag_context = rag_service.get_retrieval_context(
                    agent_id=self.agent.id,
                    query=query,
                    top_k=5
                )
            except Exception as e:
                logger.warning(f"RAG retrieval failed: {str(e)}")
                use_rag = False
        
        rag_duration = time.time() - rag_start
        
        # Build final query with RAG context if available
        final_query = query
        if rag_context:
            final_query = f"{rag_context}\n\nUser Query: {query}"
        
        # Get streaming response based on provider
        full_response = ""
        
        if model_provider == 'ollama':
            if not is_ollama_available():
                yield {
                    'type': 'error',
                    'content': 'Ollama is not configured or not reachable. Set OLLAMA_ENABLED and OLLAMA_BASE_URL and ensure the server is running.',
                    'done': True
                }
                return
            # Ollama streaming
            client = OllamaClient()
            for chunk in client.invoke_agent_stream(
                str(self.agent.id),
                final_query,
                model=model_id,
                system_prompt=system_prompt,
                training_data=None
            ):
                content = chunk.get('content', '')
                done = chunk.get('done', False)
                
                if content:
                    full_response += content
                    yield {
                        'type': 'chunk',
                        'content': content,
                        'done': False
                    }
                
                if done:
                    llm_duration = time.time() - start_time
                    logger.info(f"[Performance] Streaming LLM inference (Ollama): {llm_duration:.3f}s")
                    yield {
                        'type': 'done',
                        'content': '',
                        'done': True,
                        'full_response': full_response
                    }
                    break
        
        elif model_provider == 'bedrock':
            # Bedrock streaming
            billing_user = self.agent.user if self.agent.user != self.user else self.user
            client = get_bedrock_client(user=billing_user)
            
            # Get inference profile ARN if configured
            inference_profile_arn = None
            if self.agent.inference_profile and self.agent.inference_profile.profile_arn:
                inference_profile_arn = self.agent.inference_profile.profile_arn
            
            for chunk in client.invoke_agent_stream(
                str(self.agent.id),
                final_query,
                model=model_id,
                system_prompt=system_prompt,
                inference_profile_arn=inference_profile_arn,
                user=billing_user,
                agent_db_id=self.agent.id
            ):
                chunk_type = chunk.get('type', '')
                content = chunk.get('content', '')
                done = chunk.get('done', False)
                
                if chunk_type == 'chunk' and content:
                    full_response += content
                    yield {
                        'type': 'chunk',
                        'content': content,
                        'done': False
                    }
                
                if chunk_type == 'done' or done:
                    llm_duration = time.time() - start_time
                    logger.info(f"[Performance] Streaming LLM inference (Bedrock): {llm_duration:.3f}s")
                    yield {
                        'type': 'done',
                        'content': '',
                        'done': True,
                        'full_response': chunk.get('full_response', full_response)
                    }
                    break
                
                if chunk_type == 'error':
                    logger.error(f"Bedrock streaming error: {chunk.get('error', 'Unknown error')}")
                    yield chunk
                    break
        
        else:
            # Unsupported provider for streaming
            raise ValueError(f"Streaming is not supported for provider: {model_provider}")
    
    def _fill_video_prompt_from_user(self, tool_calls: List[Dict[str, Any]], user_message: Optional[str]) -> None:
        """When the LLM sends text_to_video/image_to_video with empty or placeholder text_prompt, fill from user message so a video is still generated."""
        if not user_message or not user_message.strip():
            return
        user_text = user_message.strip()[:2000]
        placeholders = ('.', '..', '...', '…', 'n/a', 'na', 'tbd', 'prompt', 'text', 'caption', 'video description', 'enter prompt', 'user prompt', 'description here')
        for tc in tool_calls:
            if tc.get('tool') not in ('text_to_video', 'image_to_video'):
                continue
            params = tc.setdefault('parameters', {})
            raw = (params.get('text_prompt') or params.get('prompt') or params.get('text') or params.get('caption') or '').strip()
            if not raw:
                params['text_prompt'] = user_text
                logger.info("Filled text_prompt from user message (was empty)")
                continue
            low = raw.lower()
            if low in placeholders or (len(low) <= 10 and not low.replace('.', '').replace(' ', '')):
                params['text_prompt'] = user_text
                logger.info("Filled text_prompt from user message (was placeholder: %s)", raw[:50])
    
    def _extract_tool_calls(self, response: str, user_message: str = None) -> List[Dict[str, Any]]:
        """Extract tool calls from agent response. user_message is the current turn's user input (used to only inject url_resolver when the user provided a URL)."""
        tool_calls = []
        # Strip only <reasoning> and </reasoning> tags so content (and any tool calls) inside is still visible to parsers
        response_clean = response
        if '<reasoning>' in response or '</reasoning>' in response:
            response_clean = re.sub(r'</?reasoning>', ' ', response_clean)
            response_clean = re.sub(r'\s+', ' ', response_clean).strip()
        # Use cleaned response for all extraction attempts
        response = response_clean
        
        # Method 1: Try to find and parse complete JSON objects containing tool_calls
        # Look for JSON objects that start with { and contain "tool_calls"
        # This handles nested structures better
        try:
            # First, try to find JSON objects that might contain tool_calls
            # Look for patterns like {"tool_calls": [...]}
            # Use a more sophisticated approach to find balanced braces
            start_idx = 0
            while True:
                # Find the start of a potential JSON object
                start_pos = response.find('{"tool_calls"', start_idx)
                if start_pos == -1:
                    # Also try with spaces: { "tool_calls"
                    start_pos = response.find('{ "tool_calls"', start_idx)
                if start_pos == -1:
                    # Also try with newlines
                    start_pos = response.find('{\n"tool_calls"', start_idx)
                if start_pos == -1:
                    break
                
                # Find the matching closing brace
                brace_count = 0
                end_pos = start_pos
                in_string = False
                escape_next = False
                
                for i in range(start_pos, len(response)):
                    char = response[i]
                    
                    if escape_next:
                        escape_next = False
                        continue
                    
                    if char == '\\':
                        escape_next = True
                        continue
                    
                    if char == '"' and not escape_next:
                        in_string = not in_string
                        continue
                    
                    if not in_string:
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                end_pos = i + 1
                                break
                
                if brace_count == 0 and end_pos > start_pos:
                    json_str = response[start_pos:end_pos]
                    try:
                        data = json.loads(json_str)
                        if 'tool_calls' in data and isinstance(data['tool_calls'], list):
                            tool_calls.extend(data['tool_calls'])
                            logger.info(f"Extracted {len(data['tool_calls'])} tool calls from JSON")
                    except json.JSONDecodeError as e:
                        logger.debug(f"Failed to parse JSON at position {start_pos}: {str(e)}")
                        # Try to extract tool calls from malformed JSON
                        pass
                
                start_idx = end_pos if end_pos > start_pos else start_pos + 1
        except Exception as e:
            logger.debug(f"Error in JSON extraction: {str(e)}")
        
        # Method 2: Try to find individual tool call objects
        # Look for patterns like: {"tool": "tool_name", "parameters": {...}}
        # This handles cases where tool calls are not wrapped in a tool_calls array
        try:
            # Find all potential tool call objects
            tool_pattern = r'\{\s*"tool"\s*:\s*"([^"]+)"\s*,\s*"parameters"\s*:\s*(\{.*?\})\s*\}'
            matches = re.finditer(tool_pattern, response, re.DOTALL)
            
            for match in matches:
                tool_name = match.group(1)
                params_str = match.group(2)
                
                # Try to parse parameters as JSON
                try:
                    # Handle nested JSON in parameters
                    parameters = json.loads(params_str)
                    tool_calls.append({
                        'tool': tool_name,
                        'parameters': parameters
                    })
                    logger.info(f"Extracted tool call: {tool_name}")
                except json.JSONDecodeError:
                    # If parameters parsing fails, try to extract a simpler version
                    try:
                        # Try to find the parameters object with balanced braces
                        param_start = match.start(2)
                        param_end = match.end(2)
                        
                        # Count braces to find the complete parameters object
                        brace_count = 0
                        in_string = False
                        escape_next = False
                        
                        for i in range(param_start, len(response)):
                            char = response[i]
                            
                            if escape_next:
                                escape_next = False
                                continue
                            
                            if char == '\\':
                                escape_next = True
                                continue
                            
                            if char == '"' and not escape_next:
                                in_string = not in_string
                                continue
                            
                            if not in_string:
                                if char == '{':
                                    brace_count += 1
                                elif char == '}':
                                    brace_count -= 1
                                    if brace_count == 0:
                                        param_end = i + 1
                                        break
                        
                        if brace_count == 0:
                            params_str = response[param_start:param_end]
                            parameters = json.loads(params_str)
                            tool_calls.append({
                                'tool': tool_name,
                                'parameters': parameters
                            })
                            logger.info(f"Extracted tool call with balanced braces: {tool_name}")
                    except (json.JSONDecodeError, Exception) as e:
                        logger.debug(f"Failed to parse parameters for tool {tool_name}: {str(e)}")
                        # Add tool call with empty parameters as fallback
                        tool_calls.append({
                            'tool': tool_name,
                            'parameters': {}
                        })
        except Exception as e:
            logger.debug(f"Error in flexible tool extraction: {str(e)}")
        
        # Method 2b: Handle format tool_name.{"param": "value"} or tool_name({"param": "value"})
        # Also: "Use web_search...\n{"query": "..."}" where JSON is on following lines
        if not tool_calls:
            try:
                valid_tools = set(self.tool_executor.tool_manager.get_all_tools().keys())
                # Pattern 1: tool_name.{" or tool_name({" (same line or with newlines)
                pattern = r'([a-zA-Z_][a-zA-Z0-9_]*)\s*[.(]\s*(\{)'
                for match in re.finditer(pattern, response, re.DOTALL):
                    tool_name = match.group(1)
                    if tool_name not in valid_tools:
                        continue
                    param_start = match.start(2)
                    brace_count = 0
                    param_end = param_start
                    for i in range(param_start, len(response)):
                        char = response[i]
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                param_end = i + 1
                                break
                    if brace_count == 0 and param_end > param_start:
                        params_str = response[param_start:param_end]
                        try:
                            parameters = json.loads(params_str)
                            tool_calls.append({
                                'tool': tool_name,
                                'parameters': parameters
                            })
                            logger.info(f"Extracted tool call from tool_name.{{...}} format: {tool_name}")
                            break  # Use first valid match
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                logger.debug(f"Error in tool_name.params extraction: {str(e)}")
        
        # Method 2c: "Use web_search...\n{"query": "..."}" or "call url_resolver...\n{"query":"..."}" or "call text_to_video...\n{"text_prompt":"..."}" - tool in text, JSON nearby
        if not tool_calls:
            try:
                valid_tools = set(self.tool_executor.tool_manager.get_all_tools().keys())
                # Video/speech tools: match JSON that has "text_prompt" (text_to_video, image_to_video, text_to_speech) or "script"/"voice_id"
                video_speech_tools = ['text_to_video', 'image_to_video', 'text_to_speech', 'combine_video_audio']
                for pattern, param_keys, preferred_tools in [
                    (r'"(?:query|url)"\s*:\s*', ['query', 'url'], None),
                    (r'"text_prompt"\s*:\s*', ['text_prompt', 'duration', 'aspect_ratio', 'resolution', 'model'], video_speech_tools),
                    (r'"(?:text|voice_id|engine)"\s*:\s*', ['text', 'voice_id', 'engine'], ['text_to_speech']),
                ]:
                    if tool_calls:
                        break
                    for m in re.finditer(pattern, response):
                        param_start = response.rfind('{', 0, m.start())
                        if param_start == -1:
                            continue
                        brace_count = 0
                        param_end = -1
                        for i in range(param_start, len(response)):
                            c = response[i]
                            if c == '{':
                                brace_count += 1
                            elif c == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    param_end = i + 1
                                    break
                        if param_end == -1:
                            continue
                        json_str = response[param_start:param_end]
                        try:
                            params = json.loads(json_str)
                            if not any(k in params for k in param_keys):
                                continue
                            search_start = max(0, param_start - 1200)
                            text_before = response[search_start:param_start]
                            candidates = list(valid_tools)
                            if preferred_tools:
                                candidates = [t for t in preferred_tools if t in valid_tools] + [t for t in candidates if t not in preferred_tools]
                            for tool_name in candidates:
                                if tool_name not in text_before.lower():
                                    continue
                                if tool_name == 'url_resolver':
                                    tool_calls.append({
                                        'tool': tool_name,
                                        'parameters': {'url': params.get('url') or params.get('query', '')}
                                    })
                                elif tool_name in video_speech_tools:
                                    tool_calls.append({
                                        'tool': tool_name,
                                        'parameters': {k: v for k, v in params.items() if v is not None and v != ''}
                                    })
                                else:
                                    tool_calls.append({
                                        'tool': tool_name,
                                        'parameters': {'query': params.get('query', params.get('url', ''))}
                                    })
                                logger.info(f"Extracted tool call from nearby tool mention + JSON: {tool_name}")
                                break
                            if tool_calls:
                                break
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                logger.debug(f"Error in tool-mention + JSON extraction: {str(e)}")
        
        # Method 3: Try to parse the entire response as JSON (in case it's pure JSON)
        if not tool_calls:
            try:
                data = json.loads(response.strip())
                if isinstance(data, dict) and 'tool_calls' in data:
                    if isinstance(data['tool_calls'], list):
                        tool_calls.extend(data['tool_calls'])
                        logger.info(f"Parsed entire response as JSON, found {len(data['tool_calls'])} tool calls")
            except json.JSONDecodeError:
                pass
        
        # Method 4: Try to extract JSON from code blocks (```json ... ```)
        if not tool_calls:
            try:
                # Look for JSON code blocks
                json_block_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
                matches = re.finditer(json_block_pattern, response, re.DOTALL)
                for match in matches:
                    json_str = match.group(1)
                    try:
                        data = json.loads(json_str)
                        if isinstance(data, dict) and 'tool_calls' in data:
                            if isinstance(data['tool_calls'], list):
                                tool_calls.extend(data['tool_calls'])
                                logger.info(f"Extracted {len(data['tool_calls'])} tool calls from JSON code block")
                    except json.JSONDecodeError:
                        pass
            except Exception as e:
                logger.debug(f"Error extracting from JSON code blocks: {str(e)}")
        
        # Method 5: Try to find JSON-like structures even with text before/after
        if not tool_calls:
            try:
                # Look for the pattern: tool_calls followed by array
                # This handles cases like "Here's the tool call: {"tool_calls": [...]}"
                pattern = r'(?:^|\n|\.|\s)(\{[^{]*"tool_calls"\s*:\s*\[[^\]]+\][^}]*\})'
                matches = re.finditer(pattern, response, re.DOTALL | re.MULTILINE)
                for match in matches:
                    json_str = match.group(1)
                    try:
                        data = json.loads(json_str)
                        if isinstance(data, dict) and 'tool_calls' in data:
                            if isinstance(data['tool_calls'], list):
                                tool_calls.extend(data['tool_calls'])
                                logger.info(f"Extracted {len(data['tool_calls'])} tool calls from loose JSON pattern")
                    except json.JSONDecodeError:
                        pass
            except Exception as e:
                logger.debug(f"Error in loose JSON extraction: {str(e)}")
        
        # Method 6: Try to extract tool calls from markdown or formatted text
        if not tool_calls:
            try:
                # Look for patterns like:
                # Tool: tool_name
                # Parameters: {...}
                tool_name_pattern = r'(?:Tool|tool|TOOL)[\s:]+([a-zA-Z_][a-zA-Z0-9_]*)'
                param_pattern = r'(?:Parameters|parameters|PARAMETERS)[\s:]+(\{.*?\})'
                
                tool_matches = list(re.finditer(tool_name_pattern, response))
                param_matches = list(re.finditer(param_pattern, response, re.DOTALL))
                
                # Try to pair tool names with parameters
                for tool_match in tool_matches:
                    tool_name = tool_match.group(1)
                    # Find the closest parameter match after this tool name
                    for param_match in param_matches:
                        if param_match.start() > tool_match.end():
                            try:
                                params_str = param_match.group(1)
                                # Try to balance braces
                                brace_count = params_str.count('{') - params_str.count('}')
                                if brace_count > 0:
                                    # Need to find more closing braces
                                    remaining = response[param_match.end():]
                                    for i, char in enumerate(remaining):
                                        if char == '}':
                                            brace_count -= 1
                                            if brace_count == 0:
                                                params_str = response[param_match.start(1):param_match.end(1) + i + 1]
                                                break
                                
                                parameters = json.loads(params_str)
                                tool_calls.append({
                                    'tool': tool_name,
                                    'parameters': parameters
                                })
                                logger.info(f"Extracted tool call from formatted text: {tool_name}")
                                break
                            except (json.JSONDecodeError, Exception) as e:
                                logger.debug(f"Failed to parse parameters for formatted tool {tool_name}: {str(e)}")
                                # Add with empty parameters
                                tool_calls.append({
                                    'tool': tool_name,
                                    'parameters': {}
                                })
                                break
            except Exception as e:
                logger.debug(f"Error in formatted text extraction: {str(e)}")
        
        # Method 7: Tool name in prose + standalone JSON (e.g. ...url_resolver...{"query":"..."} or ...mcp_X...{"param": 1})
        if not tool_calls:
            try:
                valid_tools = set(self.tool_executor.tool_manager.get_all_tools().keys())
                # Match MCP tool names in text
                tool_name_candidates = re.findall(
                    r'(?:call\s+)?(?:the\s+)?(?:tool\s+)?(mcp_[A-Za-z0-9_ ]+?)(?:\s*[.,]|\s+The\s|\s+Let\s|\s+with\s|$)',
                    response, re.IGNORECASE
                )
                tool_name_candidates += re.findall(r'\b(mcp_[A-Za-z0-9_ ]+?)(?=\s*[.,]|\s+with\s|$)', response)
                # Also match url_resolver, web_search, video/speech tools when mentioned in prose
                for name in ['url_resolver', 'web_search', 'text_to_image', 'text_to_video', 'image_to_video', 'text_to_speech', 'combine_video_audio']:
                    if name in valid_tools and name in response.lower():
                        tool_name_candidates.append(name)
                tool_name_candidates = list(dict.fromkeys(t.strip() for t in tool_name_candidates))
                param_start = response.find('{')
                if param_start >= 0 and valid_tools:
                    brace_count = 0
                    end_pos = param_start
                    in_str = False
                    escape = False
                    for i in range(param_start, len(response)):
                        c = response[i]
                        if escape:
                            escape = False
                            continue
                        if c == '\\':
                            escape = True
                            continue
                        if c == '"':
                            in_str = not in_str
                            continue
                        if not in_str:
                            if c == '{':
                                brace_count += 1
                            elif c == '}':
                                brace_count -= 1
                            if brace_count == 0:
                                end_pos = i + 1
                                break
                    if brace_count == 0 and end_pos > param_start:
                        try:
                            params = json.loads(response[param_start:end_pos])
                            if isinstance(params, dict):
                                text_before = response[:param_start]
                                for name in tool_name_candidates:
                                    name = name.strip()
                                    if name not in valid_tools:
                                        continue
                                    if name in text_before.lower():
                                        # url_resolver expects "url"; accept "query" as alias
                                        if name == 'url_resolver':
                                            params = {'url': params.get('url') or params.get('query', '')}
                                        tool_calls.append({'tool': name, 'parameters': params})
                                        logger.info(f"Extracted tool call from prose+JSON: {name}")
                                        break
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                logger.debug(f"Error in prose+JSON extraction: {str(e)}")
        
        # Method 8: Truncated or incomplete JSON - infer tool and params from prose (e.g. "list models with provider 'bedrock'.{"query":"")
        if not tool_calls and '{' in response:
            try:
                valid_tools = set(self.tool_executor.tool_manager.get_all_tools().keys())
                # Find valid tools mentioned in response (exact or case-insensitive; MCP names may have spaces)
                mentioned_tools = [t for t in valid_tools if t in response or t.lower() in response.lower()]
                if not mentioned_tools:
                    # Match by salient parts: "list models" -> tool containing list_models
                    resp_lower = response.lower()
                    mentioned_tools = [t for t in valid_tools if any(
                        p in resp_lower for p in t.lower().replace('_', ' ').split()
                    )]
                # Infer parameters from prose
                params = {}
                if re.search(r"provider\s+['\"]?(?:bedrock|ollama|aws)['\"]?", response, re.IGNORECASE):
                    m = re.search(r"provider\s+['\"]?(bedrock|ollama|aws)['\"]?", response, re.IGNORECASE)
                    if m:
                        params['provider'] = m.group(1).lower()
                if re.search(r"['\"]?(query|url)['\"]?\s*[:=]", response, re.IGNORECASE):
                    m = re.search(r'["\']?query["\']?\s*:\s*["\']([^"\']*)', response)
                    if m:
                        params['query'] = m.group(1).strip()
                    m = re.search(r'["\']?url["\']?\s*:\s*["\']([^"\']*)', response)
                    if m:
                        params['url'] = m.group(1).strip()
                if params or mentioned_tools:
                    # Prefer list_models-style tool when we inferred provider
                    chosen = None
                    if params.get('provider'):
                        for t in mentioned_tools:
                            if 'list' in t.lower() and 'model' in t.lower():
                                chosen = t
                                break
                    if not chosen and mentioned_tools:
                        chosen = mentioned_tools[0]
                    if chosen:
                        tool_calls.append({'tool': chosen, 'parameters': params})
                        logger.info(f"Extracted tool call from prose (truncated/incomplete JSON): {chosen}")
            except Exception as e:
                logger.debug(f"Error in truncated-JSON prose extraction: {str(e)}")

        # Method 9: User asked to create/generate video and no JSON yet - only inject url_resolver when the USER's input contains a URL
        if not tool_calls and user_message:
            try:
                resp_lower = response.lower()
                if ('create' in resp_lower and 'video' in resp_lower) or ('generate' in resp_lower and 'video' in resp_lower) or ('demo video' in resp_lower) or ('video with sound' in resp_lower):
                    # Look for URL only in the user's message, not in the model response
                    url_match = re.search(r'https?://[^\s\)\]\'"<>]+', user_message)
                    if not url_match:
                        url_match = re.search(r'(?:https?://)?([a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,})(?:/[^\s\)\]\'"]*)?', user_message)
                    if url_match:
                        url = url_match.group(0).strip()
                        if not url.startswith('http'):
                            url = 'https://' + url
                        valid_tools = set(self.tool_executor.tool_manager.get_all_tools().keys())
                        if 'url_resolver' in valid_tools:
                            tool_calls.append({'tool': 'url_resolver', 'parameters': {'url': url}})
                            logger.info(f"Extracted url_resolver from create-video intent (URL in user input): {url[:60]}...")
            except Exception as e:
                logger.debug(f"Error in create-video fallback extraction: {str(e)}")

        # Method 10: User asked for a still image but model returned no parseable JSON — inject text_to_image
        if not tool_calls and user_message:
            try:
                um = user_message.lower()
                explicit_image = any(
                    p in um for p in (
                        'generate an image', 'generate image', 'create an image', 'create image',
                        'image generation', 'draw me ', 'draw a ', 'text_to_image', 'text to image',
                        'illustration for', 'illustration of', 'make an image', 'make me an image',
                        'render an image', 'still image', 'banner image', 'thumbnail image',
                        'picture for', 'graphic for',
                    )
                )
                verb_and_noun = bool(
                    re.search(r'\b(generate|create|draw|make|produce|design|render)\b', um)
                    and re.search(r'\b(image|illustration|picture|graphic|thumbnail|banner|infographic|logo)\b', um)
                )
                wants_still_image = explicit_image or verb_and_noun
                valid_tools = set(self.tool_executor.tool_manager.get_all_tools().keys())
                if wants_still_image and 'text_to_image' in valid_tools:
                    base_prompt = user_message.strip()
                    extra = re.sub(r'<reasoning>[\s\S]*?</reasoning>', '', response, flags=re.IGNORECASE)
                    extra = re.sub(r'</?reasoning>', '', extra, flags=re.IGNORECASE).strip()
                    if len(extra) > 80 and len(base_prompt) < 500:
                        base_prompt = (base_prompt + '\n\n' + extra[:4000]).strip()
                    base_prompt = base_prompt[:8000]
                    if base_prompt:
                        if re.search(r'\b(9:16|portrait|story|reel|tiktok|vertical)\b', um):
                            aspect = '9:16'
                        elif re.search(r'\b(16:9|linkedin|youtube|wide|landscape|presentation)\b', um):
                            aspect = '16:9'
                        else:
                            aspect = '1:1'
                        tool_calls.append({
                            'tool': 'text_to_image',
                            'parameters': {'text_prompt': base_prompt, 'aspect_ratio': aspect},
                        })
                        logger.info(
                            'Injected text_to_image from user intent (no parseable tool JSON in model response)'
                        )
            except Exception as e:
                logger.debug(f"Error in image-generation fallback extraction: {str(e)}")

        # Remove duplicates (same tool with same parameters)
        # Drop placeholder/invalid names (e.g. model output literal "name" from prompt example)
        _placeholder_names = {'name', 'tool_name', 'toolname', 'example'}
        try:
            _valid = set(self.tool_executor.tool_manager.get_all_tools().keys())
            tool_calls = [
                tc for tc in tool_calls
                if (tc.get('tool') or '').strip()
                and (tc.get('tool') or '').strip().lower() not in _placeholder_names
                and (tc.get('tool') or '').strip() in _valid
            ]
        except Exception:
            tool_calls = [
                tc for tc in tool_calls
                if (tc.get('tool') or '').strip()
                and (tc.get('tool') or '').strip().lower() not in _placeholder_names
            ]
        seen = set()
        unique_tool_calls = []
        for tc in tool_calls:
            key = (tc.get('tool'), json.dumps(tc.get('parameters', {}), sort_keys=True))
            if key not in seen:
                seen.add(key)
                unique_tool_calls.append(tc)
        tool_calls = unique_tool_calls
        
        # Log extracted tool calls for debugging
        if tool_calls:
            logger.info(f"Extracted {len(tool_calls)} tool call(s): {[tc.get('tool', 'unknown') for tc in tool_calls]}")
        else:
            # Enhanced logging for debugging
            logger.warning(f"No tool calls found in response (length={len(response)}). Response preview: {response[:500]}")
            # Log if response contains tool-related keywords
            tool_keywords = ['tool', 'read_file', 'web_search', 'url_resolver', 'ocr', 'summarization', 'question_answering', 'translation', 'text_to_image', 'text_to_video', 'image_to_video', 'text_to_speech', 'combine_video_audio', 'list_agents', 'create_agent', 'get_agent', 'mcp_']
            found_keywords = [kw for kw in tool_keywords if kw.lower() in response.lower()]
            if found_keywords:
                logger.info(f"Response contains tool-related keywords: {found_keywords} - but no valid tool calls extracted")
        
        return tool_calls
    
    def _execute_tool_call(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single tool call."""
        tool_name = (tool_call.get('tool') or '').strip()
        parameters = tool_call.get('parameters', {})
        
        if not tool_name:
            return {"error": "Tool name is required", "state": "error"}
        # Reject placeholder/example names that models sometimes output literally
        if tool_name.lower() in ('name', 'tool_name', 'toolname', 'example'):
            return {
                "error": f"Invalid tool name '{tool_name}'. Use an actual tool from the Tools list (e.g. url_resolver, text_to_video, web_search).",
                "state": "error"
            }
        
        # Execute tool - pass conversation to tool_executor for file registration
        if not hasattr(self.tool_executor, 'conversation'):
            self.tool_executor.conversation = self.conversation
        result = self.tool_executor.execute_tool(
            tool_name,
            parameters,
            self.conversation.conversation_id if self.conversation else None
        )
        
        # Save tool call message
        self._save_message(
            'tool_call',
            f"Calling tool: {tool_name}",
            tool_name=tool_name,
            tool_parameters=parameters
        )
        
        # Save tool result message
        self._save_message(
            'tool_result',
            json.dumps(result),
            tool_name=tool_name,
            tool_result=result
        )
        
        return result
    
    def _extract_training_data_content(self, training_data) -> str:
        """Extract text content from training data, handling file uploads."""
        try:
            # If it's a file upload, read the file content
            if training_data.data_type == 'file' and training_data.file_path:
                file_path = training_data.file_path.path if hasattr(training_data.file_path, 'path') else None
                if not file_path and hasattr(training_data.file_path, 'name'):
                    # Try to construct full path
                    from django.conf import settings
                    media_root = getattr(settings, 'MEDIA_ROOT', '')
                    if media_root:
                        file_path = os.path.join(media_root, training_data.file_path.name)
                
                if file_path and os.path.exists(file_path):
                    return self._read_file_content(file_path)
                else:
                    logger.warning(f"Training data file not found: {training_data.file_path.name}")
                    return ""
            
            # If it's stored as text content in the content field
            if training_data.content:
                if isinstance(training_data.content, dict):
                    # Try to get text content from various keys
                    text = training_data.content.get('text') or \
                           training_data.content.get('content') or \
                           training_data.content.get('data')
                    if text:
                        return str(text)
                elif isinstance(training_data.content, str):
                    return training_data.content
            
            return ""
        except Exception as e:
            logger.error(f"Error extracting training data content: {str(e)}", exc_info=True)
            return ""
    
    def _read_file_content(self, file_path: str) -> str:
        """Read text content from various file types."""
        try:
            file_ext = os.path.splitext(file_path)[1].lower()
            
            # Plain text files
            if file_ext in ['.txt', '.text', '.md', '.markdown', '.rst', '.log', '.conf', '.config', '.ini']:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
            
            # JSON files
            if file_ext == '.json':
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return json.dumps(data, indent=2)
            
            # CSV/TSV files
            if file_ext in ['.csv', '.tsv']:
                import csv
                content = []
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    reader = csv.reader(f)
                    for row in reader:
                        content.append(', '.join(row))
                return '\n'.join(content)
            
            # Try to use external libraries for complex formats
            # PDF files
            if file_ext == '.pdf':
                try:
                    import PyPDF2
                    content = []
                    with open(file_path, 'rb') as f:
                        pdf_reader = PyPDF2.PdfReader(f)
                        for page in pdf_reader.pages:
                            content.append(page.extract_text())
                    return '\n\n'.join(content)
                except ImportError:
                    logger.warning("PyPDF2 not installed, cannot read PDF files")
                    return f"[PDF file: {os.path.basename(file_path)} - PDF reading not available]"
                except Exception as e:
                    logger.error(f"Error reading PDF: {str(e)}")
                    return f"[PDF file: {os.path.basename(file_path)} - Error: {str(e)}]"
            
            # Word documents (.docx)
            if file_ext == '.docx':
                try:
                    from docx import Document
                    doc = Document(file_path)
                    content = []
                    for paragraph in doc.paragraphs:
                        content.append(paragraph.text)
                    return '\n'.join(content)
                except ImportError:
                    logger.warning("python-docx not installed, cannot read DOCX files")
                    return f"[Word document: {os.path.basename(file_path)} - DOCX reading not available]"
                except Exception as e:
                    logger.error(f"Error reading DOCX: {str(e)}")
                    return f"[Word document: {os.path.basename(file_path)} - Error: {str(e)}]"
            
            # Try to read as text for other file types (code files, etc.)
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
            except UnicodeDecodeError:
                # If UTF-8 fails, try with different encoding
                try:
                    with open(file_path, 'r', encoding='latin-1', errors='ignore') as f:
                        return f.read()
                except Exception as e:
                    logger.error(f"Error reading file {file_path}: {str(e)}")
                    return f"[File: {os.path.basename(file_path)} - Could not read content]"
        
        except Exception as e:
            logger.error(f"Error reading file content from {file_path}: {str(e)}", exc_info=True)
            return f"[File: {os.path.basename(file_path)} - Error: {str(e)}]"
    
    def _build_query_with_tool_results(self, original_query: str, tool_results: List[Dict]) -> str:
        """Build a new query that includes tool results."""
        query = f"Original query: {original_query}\n\n"
        query += "Tool execution results:\n"
        for i, result in enumerate(tool_results, 1):
            tool_name = result.get('tool', 'unknown')
            tool_result = result.get('result', {})
            query += f"\n[{i}] Tool: {tool_name}\n"
            query += f"Result: {json.dumps(tool_result, indent=2)}\n"
        query += "\nPlease use these tool results to provide a complete answer to the user's original query."
        return query


