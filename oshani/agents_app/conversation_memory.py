"""LangChain-based conversation memory manager."""
import logging
from typing import List, Optional

try:
    from langchain_core.chat_history import InMemoryChatMessageHistory
    from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    InMemoryChatMessageHistory = None  # type: ignore[misc, assignment]
    BaseMessage = HumanMessage = AIMessage = None  # type: ignore[misc, assignment]
    logging.warning("LangChain not available. Conversation memory will be disabled.")

from .models import Conversation, ConversationMessage

logger = logging.getLogger(__name__)


def _format_history(messages: List[BaseMessage], max_tokens: Optional[int] = None) -> str:
    if not messages:
        return ""

    history_parts = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            history_parts.append(f"User: {msg.content}")
        elif isinstance(msg, AIMessage):
            history_parts.append(f"Assistant: {msg.content}")

    history = "\n".join(history_parts)

    if max_tokens and len(history) > max_tokens:
        history = history[-max_tokens:]
        first_newline = history.find("\n")
        if first_newline > 0:
            history = history[first_newline + 1:]
        history = "... (earlier messages truncated) ...\n" + history

    return history


class DatabaseBackedMemory:
    """Conversation memory backed by the database and langchain_core chat history."""

    def __init__(self, conversation: Optional[Conversation] = None, k: Optional[int] = None, **kwargs):
        self.conversation = conversation
        self.k = k
        if LANGCHAIN_AVAILABLE:
            self.chat_memory = InMemoryChatMessageHistory()
        else:
            self.chat_memory = type(
                'obj',
                (object,),
                {
                    'messages': [],
                    'add_user_message': lambda *args, **kwargs: None,
                    'add_ai_message': lambda *args, **kwargs: None,
                    'clear': lambda *args, **kwargs: None,
                },
            )()

        if conversation and LANGCHAIN_AVAILABLE:
            self._load_from_database()

    def _load_from_database(self):
        """Load conversation history from the database into memory."""
        if not self.conversation:
            return

        try:
            queryset = ConversationMessage.objects.filter(
                conversation=self.conversation
            ).exclude(
                message_type__in=['tool_call', 'tool_result']
            ).order_by('created_at')

            if self.k is not None:
                total = queryset.count()
                queryset = queryset[max(0, total - self.k):]

            message_count = 0
            for msg in queryset:
                if msg.message_type == 'user':
                    self.chat_memory.add_user_message(msg.content)
                    message_count += 1
                elif msg.message_type == 'agent':
                    self.chat_memory.add_ai_message(msg.content)
                    message_count += 1

            logger.info(
                "Loaded %s conversation messages from database for conversation %s",
                message_count,
                self.conversation.conversation_id,
            )
        except Exception as e:
            logger.error("Error loading conversation history from database: %s", e, exc_info=True)

    def save_context(self, inputs, outputs) -> None:
        """Save context to memory (database persistence is handled by agent_loop)."""

    def get_conversation_history(self, max_tokens: Optional[int] = None) -> str:
        messages = self.chat_memory.messages if hasattr(self.chat_memory, 'messages') else []

        if not messages and self.conversation and LANGCHAIN_AVAILABLE:
            try:
                self._load_from_database()
                messages = self.chat_memory.messages
            except Exception as e:
                logger.debug("Failed to reload from database: %s", e)

        return _format_history(messages, max_tokens)

    def get_messages(self) -> List[BaseMessage]:
        return self.chat_memory.messages if hasattr(self.chat_memory, 'messages') else []

    def clear(self):
        if hasattr(self.chat_memory, 'clear'):
            self.chat_memory.clear()


def get_conversation_memory(
    conversation: Optional[Conversation],
    memory_type: str = "buffer",
    max_token_limit: Optional[int] = None,
):
    """
    Get appropriate conversation memory for a chat session.

    Args:
        conversation: Conversation model instance
        memory_type: Type of memory ("buffer", "summary", "window")
        max_token_limit: Maximum tokens for memory (for summary/window types)

    Returns:
        Memory instance with get_conversation_history()
    """
    if not LANGCHAIN_AVAILABLE:
        logger.warning("LangChain not available, using fallback memory")
        return DatabaseBackedMemory(conversation)

    if not conversation:
        return DatabaseBackedMemory(None)

    if memory_type == "buffer":
        return DatabaseBackedMemory(conversation)

    if memory_type == "summary":
        logger.warning("Summary memory not fully implemented, using buffer memory")
        return DatabaseBackedMemory(conversation)

    if memory_type == "window":
        k = max_token_limit or 10
        return DatabaseBackedMemory(conversation, k=k)

    return DatabaseBackedMemory(conversation)
