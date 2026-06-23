"""
Integration tests for end-to-end workflows.
"""
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from unittest.mock import Mock, patch
import json
import uuid

from .models import Agent, Conversation, ConversationMessage, ConversationFile, AIModel
from .tool_executor import ToolExecutor
from .agent_loop import AgentLoop


class WriteFileWorkflowTestCase(TestCase):
    """Integration test for write_file tool workflow."""
    
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
        
        # Create model
        self.model = AIModel.objects.create(
            model_name='Test Model',
            model_id='test-model-id',
            provider='bedrock',
            is_available=True
        )
        self.agent.model = self.model
        self.agent.save()
        
        self.conversation = Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=str(uuid.uuid4()),
            status='active'
        )
    
    @patch('django.core.files.storage.default_storage')
    def test_write_file_creates_downloadable_file(self, mock_storage):
        """Test that write_file creates a file that can be downloaded."""
        # Mock storage
        mock_storage.save.return_value = 'conversation_files/1/test-file-id.txt'
        mock_storage.url.return_value = '/media/conversation_files/1/test-file-id.txt'
        
        # Create tool executor
        tool_executor = ToolExecutor(self.agent)
        
        # Execute write_file tool
        result = tool_executor.execute_tool(
            'write_file',
            {
                'file_name': 'essay.txt',
                'content': 'This is a test essay about Green Delhi.'
            },
            conversation_id=self.conversation.conversation_id
        )
        
        # Verify file was created
        files = ConversationFile.objects.filter(agent=self.agent)
        self.assertEqual(files.count(), 1)
        
        file_obj = files.first()
        self.assertEqual(file_obj.file_name, 'essay.txt')
        self.assertEqual(file_obj.conversation, self.conversation)
        self.assertIsNotNone(file_obj.file_id)
        
        # Verify result includes file information
        self.assertIn('file_id', result.get('full_result', {}))
        self.assertIn('file_name', result.get('full_result', {}))
    
    @patch('django.core.files.storage.default_storage')
    def test_write_file_result_formatting(self, mock_storage):
        """Test that write_file result is formatted correctly."""
        # Mock storage
        mock_storage.save.return_value = 'conversation_files/1/test-file-id.txt'
        mock_storage.url.return_value = '/media/conversation_files/1/test-file-id.txt'
        
        tool_executor = ToolExecutor(self.agent)
        
        result = tool_executor.execute_tool(
            'write_file',
            {
                'file_name': 'test.txt',
                'content': 'This is the full test content for the generated file.'
            },
            conversation_id=self.conversation.conversation_id
        )
        
        # Verify formatting
        result_content = result.get('result_content', '')
        self.assertIn('✅', result_content)
        self.assertIn('test.txt', result_content)


class FileRegistrationWorkflowTestCase(TestCase):
    """Integration test for file registration workflow."""
    
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
    
    @patch('django.core.files.storage.default_storage')
    def test_file_content_registration(self, mock_storage):
        """Test that file_content is properly registered."""
        mock_storage.save.return_value = 'conversation_files/1/test-id.txt'
        mock_storage.url.return_value = '/media/conversation_files/1/test-id.txt'
        
        tool_executor = ToolExecutor(self.agent)
        
        # Mock tool that returns file_content
        mock_tool = Mock()
        mock_tool.execute.return_value = {
            'file_content': 'Test content',
            'file_name': 'test.txt',
            'success': True
        }
        tool_executor.tool_manager.get_tool = Mock(return_value=mock_tool)
        
        result = tool_executor.execute_tool(
            'write_file',
            {'file_name': 'test.txt', 'content': 'Test content'},
            conversation_id=self.conversation.conversation_id
        )
        
        # Verify file was registered
        files = ConversationFile.objects.filter(agent=self.agent)
        self.assertEqual(files.count(), 1)
        
        file_obj = files.first()
        self.assertEqual(file_obj.file_name, 'test.txt')
        self.assertIsNotNone(file_obj.file_id)
        self.assertEqual(file_obj.conversation, self.conversation)
    
    @patch('django.core.files.storage.default_storage')
    def test_multiple_files_in_conversation(self, mock_storage):
        """Test that multiple files can be registered in same conversation."""
        mock_storage.save.return_value = 'conversation_files/1/test-id.txt'
        mock_storage.url.return_value = '/media/conversation_files/1/test-id.txt'
        
        tool_executor = ToolExecutor(self.agent)
        
        # Create first file
        mock_tool1 = Mock()
        mock_tool1.execute.return_value = {
            'file_content': 'Content 1',
            'file_name': 'file1.txt',
            'success': True
        }
        tool_executor.tool_manager.get_tool = Mock(return_value=mock_tool1)
        
        tool_executor.execute_tool(
            'write_file',
            {'file_name': 'file1.txt', 'content': 'Content 1'},
            conversation_id=self.conversation.conversation_id
        )
        
        # Create second file
        mock_tool2 = Mock()
        mock_tool2.execute.return_value = {
            'file_content': 'Content 2',
            'file_name': 'file2.txt',
            'success': True
        }
        tool_executor.tool_manager.get_tool = Mock(return_value=mock_tool2)
        
        tool_executor.execute_tool(
            'write_file',
            {'file_name': 'file2.txt', 'content': 'Content 2'},
            conversation_id=self.conversation.conversation_id
        )
        
        # Verify both files were registered
        files = ConversationFile.objects.filter(conversation=self.conversation)
        self.assertEqual(files.count(), 2)


class ChatWorkflowTestCase(TestCase):
    """Integration test for chat workflow with file generation."""
    
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
        
        # Create model
        self.model = AIModel.objects.create(
            model_name='Test Model',
            model_id='test-model-id',
            provider='bedrock',
            is_available=True
        )
        self.agent.model = self.model
        self.agent.save()
    
    @patch('agents_app.views_chat.AgentLoop')
    @patch('django.core.files.storage.default_storage')
    def test_chat_message_with_file_generation(self, mock_storage, mock_agent_loop):
        """Test that chat messages include generated files."""
        # Mock storage
        mock_storage.save.return_value = 'conversation_files/1/test-id.txt'
        mock_storage.url.return_value = '/media/conversation_files/1/test-id.txt'
        
        # Mock agent loop
        mock_loop_instance = Mock()
        mock_loop_instance.execute.return_value = {
            'response': 'I created the file for you.',
            'message_id': 1,
            'user_message_id': 2
        }
        mock_agent_loop.return_value = mock_loop_instance
        
        # Create conversation
        conversation = Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id=str(uuid.uuid4()),
            status='active'
        )
        
        # Create user message
        user_msg = ConversationMessage.objects.create(
            conversation=conversation,
            message_type='user',
            content='Create a file',
            created_at=timezone.now() - timedelta(seconds=10)
        )
        
        # Create agent message
        agent_msg = ConversationMessage.objects.create(
            conversation=conversation,
            message_type='agent',
            content='File created',
            created_at=timezone.now()
        )
        
        # Create generated file
        generated_file = ConversationFile.objects.create(
            agent=self.agent,
            conversation=conversation,
            file_name='generated.txt',
            file_type='text/plain',
            file_size=100,
            file_id=str(uuid.uuid4()),
            download_url='',
            uploaded_at=timezone.now()
        )
        
        response = self.client.post(
            '/api/chat/send/',
            data=json.dumps({
                'agent_id': self.agent.id,
                'message': 'Create a file',
                'conversation_id': conversation.conversation_id
            }),
            content_type='application/json'
        )
        
        # Chat runs asynchronously: the view returns 202 Accepted after enqueuing
        # the background task.
        self.assertEqual(response.status_code, 202)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        
        # Check if generated files are included
        if 'generated_files' in data:
            self.assertIsInstance(data['generated_files'], list)

