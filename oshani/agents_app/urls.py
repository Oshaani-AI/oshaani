"""URL configuration for agents_app API."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    AgentViewSet,
    AIModelViewSet,
    MCPServerViewSet,
    NotificationViewSet,
    UserProfileViewSet,
    InferenceProfileViewSet,
    AgentChatView,
    AgentQueryView,
    AgentAgentsListView,
    AgentAgentDetailView,
    CreateConversationView,
    AgentWebhookView,
    ContinueConversationView,
    GetAnswerView,
    FindConversationView,
    UploadFileView,
    DownloadFileView,
    CreateUploadURLView,
    GetDownloadURLView
)

router = DefaultRouter()
router.register(r'agents', AgentViewSet, basename='agent')
router.register(r'models', AIModelViewSet, basename='model')
router.register(r'mcp-servers', MCPServerViewSet, basename='mcp-server')
router.register(r'notifications', NotificationViewSet, basename='notification')
router.register(r'profile', UserProfileViewSet, basename='profile')
router.register(r'inference-profiles', InferenceProfileViewSet, basename='inference-profile')

urlpatterns = [
    path('', include(router.urls)),
    # REST API v1 endpoints
    path('v1/chat', AgentChatView.as_view(), name='agent-chat'),
    path('v1/query', AgentQueryView.as_view(), name='agent-query'),
    path('v1/agents', AgentAgentsListView.as_view(), name='agent-agents-list'),
    path('v1/agents/<int:agent_id>', AgentAgentDetailView.as_view(), name='agent-agent-detail'),
    # REST API endpoints
    path('webhook/agent/', AgentWebhookView.as_view(), name='agent-webhook'),
    path('create_conversation', CreateConversationView.as_view(), name='create-conversation'),
    path('continue_conversation', ContinueConversationView.as_view(), name='continue-conversation'),
    path('get_answer', GetAnswerView.as_view(), name='get-answer'),
    path('find_conversation', FindConversationView.as_view(), name='find-conversation'),
    path('upload_file', UploadFileView.as_view(), name='upload-file'),
    path('download_file', DownloadFileView.as_view(), name='download-file'),
    path('create_upload_url', CreateUploadURLView.as_view(), name='create-upload-url'),
    path('get_download_url', GetDownloadURLView.as_view(), name='get-download-url'),
    # Contact form endpoint
    path('contact/', include('agents_app.urls_contact')),
]

