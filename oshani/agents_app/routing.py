"""WebSocket routing for agents_app."""
from django.urls import re_path
from channels.generic.websocket import AsyncWebsocketConsumer
import json
import logging

logger = logging.getLogger(__name__)

# Import consumers
from . import consumers


class ErrorConsumer(AsyncWebsocketConsumer):
    """Consumer to handle invalid WebSocket routes with helpful error messages."""
    
    async def connect(self):
        await self.accept()
        await self.send(text_data=json.dumps({
            'type': 'error',
            'message': 'Invalid WebSocket route. Use /ws/agent/<agent_id>/chat/ instead.'
        }))
        await self.close(code=4004)  # Invalid route


websocket_urlpatterns = [
    re_path(r'^ws/agent/(?P<agent_id>\d+)/chat/$', consumers.AgentChatConsumer.as_asgi()),
    # Catch-all for invalid routes - provide helpful error
    re_path(r'^ws/chat/$', ErrorConsumer.as_asgi()),
    re_path(r'^ws/.*$', ErrorConsumer.as_asgi()),  # Catch any other ws/ routes
]




