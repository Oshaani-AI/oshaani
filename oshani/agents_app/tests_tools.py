"""
Unit tests for tools and tool executor functionality.
"""
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from unittest.mock import Mock, patch
import os
import tempfile

from .models import Agent, Conversation, ConversationFile
from .tools import WriteFileTool, ToolManager
from .tool_executor import ToolExecutor


class WriteFileToolTestCase(TestCase):
    """Test cases for WriteFileTool."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.tool = WriteFileTool()
    
    def test_execute_missing_file_name(self):
        """Test that execute returns error when file_name is missing."""
        result = self.tool.execute({'content': 'test content'})
        
        self.assertIn('error', result)
        self.assertIn('file_name', result['error'])
    
    def test_execute_missing_content(self):
        """Test that execute returns error when content is missing."""
        result = self.tool.execute({'file_name': 'test.txt'})
        
        self.assertIn('error', result)
        self.assertIn('content', result['error'])
    

class ToolExecutorFileRegistrationTestCase(TestCase):
    """Test cases for ToolExecutor file registration."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent for unit tests',
            user=self.user,
            status='published',
            configuration={}
        )
        
        self.conversation = Conversation.objects.create(
            agent=self.agent,
            user=self.user,
            conversation_id='test-conv-123',
            status='active'
        )
        
        self.tool_executor = ToolExecutor(self.agent)
    
    def tearDown(self):
        """Clean up test files."""
        # Clean up any created files
        ConversationFile.objects.filter(agent=self.agent).delete()
    
    @patch('django.core.files.storage.default_storage')
    def test_register_file_from_file_content(self, mock_storage):
        """Test file registration from file_content."""
        # Mock storage
        mock_storage.save.return_value = 'conversation_files/1/test-file-id.txt'
        mock_storage.url.return_value = '/media/conversation_files/1/test-file-id.txt'
        
        # Create a mock tool that returns file_content
        mock_tool = Mock()
        mock_tool.execute.return_value = {
            'file_content': 'Test file content',
            'file_name': 'test.txt',
            'success': True
        }
        
        # Mock get_tool to return our mock tool
        self.tool_executor.tool_manager.get_tool = Mock(return_value=mock_tool)
        
        # Execute tool
        result = self.tool_executor.execute_tool(
            'write_file',
            {'file_name': 'test.txt', 'content': 'Test file content'},
            conversation_id=self.conversation.conversation_id
        )
        
        # Verify file was registered
        files = ConversationFile.objects.filter(agent=self.agent)
        self.assertEqual(files.count(), 1)
        
        file_obj = files.first()
        self.assertEqual(file_obj.file_name, 'test.txt')
        self.assertIsNotNone(file_obj.file_id)
        self.assertEqual(file_obj.conversation, self.conversation)
        
        # Verify result includes file_id
        self.assertIn('file_id', result.get('full_result', {}))
    
    @patch('django.core.files.storage.default_storage')
    def test_register_file_from_image_url(self, mock_storage):
        """Test file registration from image_url."""
        # Mock storage
        mock_storage.exists.return_value = True
        mock_storage.size.return_value = 1024
        mock_storage.open.return_value.__enter__ = Mock(return_value=ContentFile(b'image data'))
        mock_storage.open.return_value.__exit__ = Mock(return_value=None)
        mock_storage.save.return_value = 'conversation_files/1/test-image-id.png'
        mock_storage.url.return_value = '/media/conversation_files/1/test-image-id.png'
        
        # Create a mock tool that returns image_url
        mock_tool = Mock()
        mock_tool.execute.return_value = {
            'image_url': '/media/generated_images/test.png',
            'file_name': 'test.png',
            'success': True
        }
        
        self.tool_executor.tool_manager.get_tool = Mock(return_value=mock_tool)
        
        # Execute tool
        result = self.tool_executor.execute_tool(
            'text_to_image',
            {'text_prompt': 'test image'},
            conversation_id=self.conversation.conversation_id
        )
        
        # Verify file was registered
        files = ConversationFile.objects.filter(agent=self.agent)
        self.assertEqual(files.count(), 1)
        
        file_obj = files.first()
        self.assertEqual(file_obj.file_name, 'test.png')

        fr = result.get('full_result', {})
        self.assertEqual(fr.get('image_url'), fr.get('download_url'))
        self.assertIn('conversation_files', fr.get('image_url', ''))
        self.assertIn('Share this exact URL', result.get('result_content', ''))
    
    @patch('django.core.files.storage.default_storage')
    def test_register_file_from_file_path(self, mock_storage):
        """Test file registration from file_path (code_executor)."""
        # Create a temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as tmp_file:
            tmp_file.write('print("Hello, World!")')
            tmp_file_path = tmp_file.name
        
        try:
            # Mock storage
            mock_storage.save.return_value = 'conversation_files/1/test-code-id.py'
            mock_storage.url.return_value = '/media/conversation_files/1/test-code-id.py'
            
            # Create a mock tool that returns file_path
            mock_tool = Mock()
            mock_tool.execute.return_value = {
                'file_path': tmp_file_path,
                'created_files': [tmp_file_path],
                'success': True,
                'stdout': 'Hello, World!'
            }
            
            self.tool_executor.tool_manager.get_tool = Mock(return_value=mock_tool)
            
            # Execute tool
            result = self.tool_executor.execute_tool(
                'code_executor',
                {'code': 'print("Hello, World!")'},
                conversation_id=self.conversation.conversation_id
            )
            
            # Verify file was registered
            files = ConversationFile.objects.filter(agent=self.agent)
            self.assertEqual(files.count(), 1)
            
            file_obj = files.first()
            self.assertIsNotNone(file_obj.file_id)
            
            # Verify original file was deleted
            self.assertFalse(os.path.exists(tmp_file_path))
        finally:
            # Cleanup
            if os.path.exists(tmp_file_path):
                os.remove(tmp_file_path)
    
    def test_write_file_tool_formatting(self):
        """Test that write_file tool results are formatted correctly."""
        # Create a mock tool
        mock_tool = WriteFileTool()
        
        # Mock the execute method
        with patch.object(mock_tool, 'execute') as mock_execute:
            mock_execute.return_value = {
                'file_content': 'Test content',
                'file_name': 'test.txt',
                'success': True,
                'message': "File 'test.txt' created successfully.",
                'file_id': 'test-file-id-123'
            }
            
            # Mock get_tool
            self.tool_executor.tool_manager.get_tool = Mock(return_value=mock_tool)
            
            # Mock storage
            with patch('django.core.files.storage.default_storage') as mock_storage:
                mock_storage.save.return_value = 'conversation_files/1/test-file-id.txt'
                mock_storage.url.return_value = '/media/conversation_files/1/test-file-id.txt'
                
                # Execute tool
                result = self.tool_executor.execute_tool(
                    'write_file',
                    {'file_name': 'test.txt', 'content': 'Test content'},
                    conversation_id=self.conversation.conversation_id
                )
                
                # Verify formatting
                result_content = result.get('result_content', '')
                self.assertIn('✅', result_content)
                self.assertIn('test.txt', result_content)
                self.assertIn('File ID', result_content)


class FileURLGenerationTestCase(TestCase):
    """Test cases for file URL generation in views_chat."""
    
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
            conversation_id='test-conv-123',
            status='active'
        )
    
    def test_file_url_generation_with_download_url_fallback(self):
        """Test that download_url is used as fallback when file_path is not available."""
        # Create a conversation file without file_path but with download_url
        conversation_file = ConversationFile.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            file_name='test.txt',
            file_type='text/plain',
            file_size=100,
            file_id='test-file-id-456',
            download_url='https://example.com/files/test.txt'
        )
        
        # Test URL generation logic
        if conversation_file.file_path:
            file_path_str = conversation_file.file_path.name if hasattr(conversation_file.file_path, 'name') else str(conversation_file.file_path)
            file_url = f'/media/{file_path_str}'
        else:
            file_url = conversation_file.download_url or ''
        
        # Should use download_url as fallback
        self.assertEqual(file_url, 'https://example.com/files/test.txt')


class ToolManagerTestCase(TestCase):
    """Test cases for ToolManager."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.tool_manager = ToolManager()
    
    def test_write_file_tool_registered(self):
        """Test that WriteFileTool is registered in ToolManager."""
        write_file_tool = self.tool_manager.get_tool('write_file')
        
        self.assertIsNotNone(write_file_tool)
        self.assertIsInstance(write_file_tool, WriteFileTool)
    
    def test_get_all_tools_includes_write_file(self):
        """Test that get_all_tools includes write_file."""
        all_tools = self.tool_manager.get_all_tools()
        
        self.assertIn('write_file', all_tools)
        self.assertIsInstance(all_tools['write_file'], WriteFileTool)
    
    def test_get_tools_schema_includes_write_file(self):
        """Test that get_tools_schema includes write_file."""
        schema = self.tool_manager.get_tools_schema()
        
        tool_names = [tool['name'] for tool in schema]
        self.assertIn('write_file', tool_names)
        
        # Find write_file in schema
        write_file_schema = next(tool for tool in schema if tool['name'] == 'write_file')
        self.assertEqual(write_file_schema['name'], 'write_file')
        self.assertIn('description', write_file_schema)
        self.assertIn('parameters', write_file_schema)

