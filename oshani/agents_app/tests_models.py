"""
Unit tests for models.
"""
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
import uuid

from .models import (
    Agent, Conversation, ConversationMessage, ConversationFile,
    AIModel, TrainingData, AgentShare
)


class ConversationFileModelTestCase(TestCase):
    """Test cases for ConversationFile model."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
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
    
    def test_create_conversation_file(self):
        """Test creating a ConversationFile."""
        file_id = str(uuid.uuid4())
        conversation_file = ConversationFile.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            file_name='test.txt',
            file_type='text/plain',
            file_size=100,
            file_id=file_id,
            download_url=''
        )
        
        self.assertEqual(conversation_file.file_name, 'test.txt')
        self.assertEqual(conversation_file.agent, self.agent)
        self.assertEqual(conversation_file.conversation, self.conversation)
        self.assertEqual(conversation_file.file_id, file_id)
    
    def test_conversation_file_without_conversation(self):
        """Test creating ConversationFile without conversation (for uploads)."""
        file_id = str(uuid.uuid4())
        conversation_file = ConversationFile.objects.create(
            agent=self.agent,
            file_name='test.txt',
            file_type='text/plain',
            file_size=100,
            file_id=file_id,
            download_url=''
        )
        
        self.assertIsNone(conversation_file.conversation)
        self.assertEqual(conversation_file.agent, self.agent)
    
    def test_conversation_file_str(self):
        """Test ConversationFile string representation."""
        file_id = str(uuid.uuid4())
        conversation_file = ConversationFile.objects.create(
            agent=self.agent,
            file_name='test.txt',
            file_type='text/plain',
            file_size=100,
            file_id=file_id,
            download_url=''
        )
        
        str_repr = str(conversation_file)
        self.assertIn('test.txt', str_repr)
        self.assertIn(file_id, str_repr)


class ConversationModelTestCase(TestCase):
    """Test cases for Conversation model."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent',
            user=self.user,
            status='published',
            configuration={}
        )
    
    def test_create_conversation(self):
        """Test creating a Conversation."""
        conversation_id = str(uuid.uuid4())
        conversation = Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=conversation_id,
            status='active'
        )
        
        self.assertEqual(conversation.conversation_id, conversation_id)
        self.assertEqual(conversation.agent, self.agent)
        self.assertEqual(conversation.user, self.user)
        self.assertEqual(conversation.status, 'active')
    
    def test_conversation_str(self):
        """Test Conversation string representation."""
        conversation_id = str(uuid.uuid4())
        conversation = Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=conversation_id,
            status='active'
        )
        
        str_repr = str(conversation)
        self.assertIn(conversation_id, str_repr)
        self.assertIn(self.agent.name, str_repr)


class ConversationMessageModelTestCase(TestCase):
    """Test cases for ConversationMessage model."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
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
    
    def test_create_user_message(self):
        """Test creating a user message."""
        message = ConversationMessage.objects.create(
            conversation=self.conversation,
            message_type='user',
            content='Hello, agent!'
        )
        
        self.assertEqual(message.message_type, 'user')
        self.assertEqual(message.content, 'Hello, agent!')
        self.assertEqual(message.conversation, self.conversation)
    
    def test_create_agent_message(self):
        """Test creating an agent message."""
        message = ConversationMessage.objects.create(
            conversation=self.conversation,
            message_type='agent',
            content='Hello, user!'
        )
        
        self.assertEqual(message.message_type, 'agent')
        self.assertEqual(message.content, 'Hello, user!')
    
    def test_message_str(self):
        """Test ConversationMessage string representation."""
        message = ConversationMessage.objects.create(
            conversation=self.conversation,
            message_type='user',
            content='Test message'
        )
        
        str_repr = str(message)
        self.assertIn('user', str_repr)
        self.assertIn(self.conversation.conversation_id, str_repr)

