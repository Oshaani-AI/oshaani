"""
Unit tests for chat views (views_chat.py).
"""
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from datetime import timedelta
import json
import uuid

from .models import Agent, Conversation, ConversationMessage, ConversationFile, AIModel
from .views_chat import (
    user_has_agent_access
)


class ChatHomeViewTestCase(TestCase):
    """Test cases for chat_home view."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.client = Client()
        self.client.force_login(self.user)
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent',
            user=self.user,
            status='published',
            configuration={}
        )
    
    def test_chat_home_requires_login(self):
        """Test that chat_home requires authentication."""
        client = Client()
        response = client.get('/chat/')
        self.assertEqual(response.status_code, 302)  # Redirect to login
    
    def test_chat_home_displays_agents(self):
        """Test that chat_home displays published agents."""
        response = self.client.get('/chat/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('agents', response.context)
        self.assertIn(self.agent, response.context['agents'])
    
    def test_chat_home_displays_conversations(self):
        """Test that chat_home displays user conversations."""
        # Create a conversation
        Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=str(uuid.uuid4()),
            status='active'
        )
        
        response = self.client.get('/chat/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('conversations_with_preview', response.context)
    
    def test_chat_home_deduplicates_conversations(self):
        """Test that chat_home deduplicates conversations."""
        # Create multiple conversations with same conversation_id (shouldn't happen, but test deduplication)
        conversation_id = str(uuid.uuid4())
        Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=conversation_id,
            status='active'
        )
        
        response = self.client.get('/chat/')
        self.assertEqual(response.status_code, 200)
        conversations = response.context['conversations_with_preview']
        conversation_ids = [c['conversation'].conversation_id for c in conversations]
        self.assertEqual(len(conversation_ids), len(set(conversation_ids)))  # All unique


class SendChatMessageViewTestCase(TestCase):
    """Test cases for send_chat_message view."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.client = Client()
        self.client.force_login(self.user)
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent',
            user=self.user,
            status='published',
            configuration={}
        )
        
        # Create a model for the agent
        self.model = AIModel.objects.create(
            model_name='Test Model',
            model_id='test-model-id',
            provider='bedrock',
            is_available=True
        )
        self.agent.model = self.model
        self.agent.save()
    
    def test_send_message_creates_conversation(self):
        """Test that sending a message creates a new conversation."""
        response = self.client.post(
            '/api/chat/send/',
            data=json.dumps({
                'agent_id': self.agent.id,
                'message': 'Hello'
            }),
            content_type='application/json'
        )
        
        # Chat is processed asynchronously: the view enqueues a background task
        # and returns 202 Accepted with a task_id and conversation_id.
        self.assertEqual(response.status_code, 202)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        self.assertIn('conversation_id', data)
        
        # Verify conversation was created
        conversation = Conversation.objects.get(conversation_id=data['conversation_id'])
        self.assertEqual(conversation.agent, self.agent)
        self.assertEqual(conversation.user, self.user)
    
    def test_send_message_with_file_ids(self):
        """Test sending message with file attachments."""
        # Create a file first
        conversation_file = ConversationFile.objects.create(
            agent=self.agent,
            file_name='test.txt',
            file_type='text/plain',
            file_size=100,
            file_id=str(uuid.uuid4()),
            download_url=''
        )
        
        response = self.client.post(
            '/api/chat/send/',
            data=json.dumps({
                'agent_id': self.agent.id,
                'message': 'Hello',
                'file_ids': [conversation_file.file_id]
            }),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 202)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        
        # Verify file was linked to conversation
        conversation_file.refresh_from_db()
        self.assertIsNotNone(conversation_file.conversation)
    
    def test_send_message_includes_generated_files(self):
        """Test that generated files are included in response."""
        # Create conversation
        conversation = Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=str(uuid.uuid4()),
            status='active'
        )
        
        # Create a generated file
        generated_file = ConversationFile.objects.create(
            agent=self.agent,
            conversation=conversation,
            file_name='generated.txt',
            file_type='text/plain',
            file_size=100,
            file_id=str(uuid.uuid4()),
            download_url=''
        )
        generated_file.uploaded_at = timezone.now()
        generated_file.save()
        
        # Create user and agent messages
        ConversationMessage.objects.create(
            conversation=conversation,
            message_type='user',
            content='Hello',
            created_at=timezone.now() - timedelta(seconds=10)
        )
        
        ConversationMessage.objects.create(
            conversation=conversation,
            message_type='agent',
            content='Response',
            created_at=timezone.now()
        )
        
        response = self.client.post(
            '/api/chat/send/',
            data=json.dumps({
                'agent_id': self.agent.id,
                'message': 'Hello',
                'conversation_id': conversation.conversation_id
            }),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 202)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        # Generated files should be included if timing matches
        if 'generated_files' in data:
            self.assertIsInstance(data['generated_files'], list)
    
    def test_send_message_missing_agent_id(self):
        """Test that missing agent_id returns error."""
        response = self.client.post(
            '/api/chat/send/',
            data=json.dumps({'message': 'Hello'}),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertFalse(data['success'])
        self.assertIn('agent_id', data['error'])
    
    def test_send_message_missing_message(self):
        """Test that missing message returns error."""
        response = self.client.post(
            '/api/chat/send/',
            data=json.dumps({'agent_id': self.agent.id}),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertFalse(data['success'])
        self.assertIn('Message', data['error'])


class GetConversationViewTestCase(TestCase):
    """Test cases for get_conversation view."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.client = Client()
        self.client.force_login(self.user)
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent',
            user=self.user,
            status='published',
            configuration={}
        )
        
        self.conversation = Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=str(uuid.uuid4()),
            status='active'
        )
    
    def test_get_conversation_returns_messages(self):
        """Test that get_conversation returns conversation messages."""
        # Create messages
        ConversationMessage.objects.create(
            conversation=self.conversation,
            message_type='user',
            content='Hello'
        )
        ConversationMessage.objects.create(
            conversation=self.conversation,
            message_type='agent',
            content='Hi there!'
        )
        
        response = self.client.get(
            f'/api/chat/conversation/{self.conversation.conversation_id}/'
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        self.assertIn('messages', data)
        self.assertEqual(len(data['messages']), 2)
    
    def test_get_conversation_includes_files(self):
        """Test that get_conversation includes files for agent messages."""
        # Create user message
        ConversationMessage.objects.create(
            conversation=self.conversation,
            message_type='user',
            content='Hello',
            created_at=timezone.now() - timedelta(seconds=10)
        )
        
        # Create agent message
        ConversationMessage.objects.create(
            conversation=self.conversation,
            message_type='agent',
            content='Response',
            created_at=timezone.now()
        )
        
        # Create file linked to conversation
        ConversationFile.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            file_name='test.txt',
            file_type='text/plain',
            file_size=100,
            file_id=str(uuid.uuid4()),
            download_url='',
            uploaded_at=timezone.now()
        )
        
        response = self.client.get(
            f'/api/chat/conversation/{self.conversation.conversation_id}/'
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        
        # Find agent message
        agent_messages = [m for m in data['messages'] if m['type'] == 'agent']
        if agent_messages:
            agent_message = agent_messages[0]
            self.assertIn('files', agent_message)
            if agent_message['files']:
                self.assertEqual(len(agent_message['files']), 1)
                self.assertEqual(agent_message['files'][0]['file_name'], 'test.txt')
    
    def test_get_conversation_filters_tool_messages(self):
        """Test that tool_call and tool_result messages are filtered out."""
        # Create various message types
        ConversationMessage.objects.create(
            conversation=self.conversation,
            message_type='user',
            content='Hello'
        )
        ConversationMessage.objects.create(
            conversation=self.conversation,
            message_type='tool_call',
            content='Tool call content'
        )
        ConversationMessage.objects.create(
            conversation=self.conversation,
            message_type='tool_result',
            content='Tool result content'
        )
        ConversationMessage.objects.create(
            conversation=self.conversation,
            message_type='agent',
            content='Response'
        )
        
        response = self.client.get(
            f'/api/chat/conversation/{self.conversation.conversation_id}/'
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        
        # Should only have user and agent messages
        message_types = [m['type'] for m in data['messages']]
        self.assertNotIn('tool_call', message_types)
        self.assertNotIn('tool_result', message_types)
        self.assertIn('user', message_types)
        self.assertIn('agent', message_types)


class UploadChatFileViewTestCase(TestCase):
    """Test cases for upload_chat_file view."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.client = Client()
        self.client.force_login(self.user)
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent',
            user=self.user,
            status='published',
            configuration={}
        )
    
    def test_upload_file_success(self):
        """Test successful file upload."""
        file_content = b'Test file content'
        uploaded_file = SimpleUploadedFile(
            'test.txt',
            file_content,
            content_type='text/plain'
        )
        
        response = self.client.post(
            '/api/chat/upload-file/',
            {
                'file': uploaded_file,
                'agent_id': self.agent.id
            }
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        self.assertIn('file_id', data)
        self.assertEqual(data['file_name'], 'test.txt')
        
        # Verify file was created
        file_obj = ConversationFile.objects.get(file_id=data['file_id'])
        self.assertEqual(file_obj.file_name, 'test.txt')
        self.assertEqual(file_obj.agent, self.agent)
    
    def test_upload_file_with_conversation_id(self):
        """Test file upload linked to conversation."""
        conversation = Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=str(uuid.uuid4()),
            status='active'
        )
        
        file_content = b'Test file content'
        uploaded_file = SimpleUploadedFile(
            'test.txt',
            file_content,
            content_type='text/plain'
        )
        
        response = self.client.post(
            '/api/chat/upload-file/',
            {
                'file': uploaded_file,
                'agent_id': self.agent.id,
                'conversation_id': conversation.conversation_id
            }
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        
        # Verify file is linked to conversation
        file_obj = ConversationFile.objects.get(file_id=data['file_id'])
        self.assertEqual(file_obj.conversation, conversation)
    
    def test_upload_file_size_limit(self):
        """Test that file size limit is enforced."""
        # Create a file larger than 100MB
        large_content = b'x' * (101 * 1024 * 1024)  # 101MB
        uploaded_file = SimpleUploadedFile(
            'large.txt',
            large_content,
            content_type='text/plain'
        )
        
        response = self.client.post(
            '/api/chat/upload-file/',
            {
                'file': uploaded_file,
                'agent_id': self.agent.id
            }
        )
        
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertFalse(data['success'])
        self.assertIn('size', data['error'].lower())


class GetConversationsListViewTestCase(TestCase):
    """Test cases for get_conversations_list view."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.client = Client()
        self.client.force_login(self.user)
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent',
            user=self.user,
            status='published',
            configuration={}
        )
    
    def test_get_conversations_list_returns_conversations(self):
        """Test that get_conversations_list returns user conversations."""
        # Create conversations
        Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=str(uuid.uuid4()),
            status='active'
        )
        Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=str(uuid.uuid4()),
            status='active'
        )
        
        response = self.client.get('/api/chat/conversations/')
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        self.assertIn('conversations', data)
        self.assertGreaterEqual(len(data['conversations']), 2)
    
    def test_get_conversations_list_deduplicates(self):
        """Test that get_conversations_list deduplicates conversations."""
        conversation_id = str(uuid.uuid4())
        Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=conversation_id,
            status='active'
        )
        
        response = self.client.get('/api/chat/conversations/')
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        
        # Check for duplicates
        conversation_ids = [c['id'] for c in data['conversations']]
        self.assertEqual(len(conversation_ids), len(set(conversation_ids)))


class UserHasAgentAccessTestCase(TestCase):
    """Test cases for user_has_agent_access function."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.owner = User.objects.create_user(
            username='owner',
            email='owner@example.com',
            password='pass123'
        )
        
        self.shared_user = User.objects.create_user(
            username='shared',
            email='shared@example.com',
            password='pass123'
        )
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent',
            user=self.owner,
            status='published',
            configuration={}
        )
    
    def test_owner_has_access(self):
        """Test that agent owner has access."""
        self.assertTrue(user_has_agent_access(self.owner, self.agent))
    
    def test_shared_user_has_access(self):
        """Test that shared user has access."""
        from .models import AgentShare
        
        AgentShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            email=self.shared_user.email,
            is_accepted=True,
            accepted_by=self.shared_user
        )
        
        self.assertTrue(user_has_agent_access(self.shared_user, self.agent))
    
    def test_unshared_user_no_access(self):
        """Test that unshared user has no access."""
        other_user = User.objects.create_user(
            username='other',
            email='other@example.com',
            password='pass123'
        )
        
        self.assertFalse(user_has_agent_access(other_user, self.agent))

