"""
Unit tests for file serving functionality.
"""
from django.test import TestCase, RequestFactory, Client
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.http import Http404
from unittest.mock import Mock, patch, MagicMock
import uuid

from .models import Agent, Conversation, ConversationFile
from .views import serve_media_file


class ServeMediaFileViewTestCase(TestCase):
    """Test cases for serve_media_file view."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.other_user = User.objects.create_user(
            username='otheruser',
            email='other@example.com',
            password='pass123'
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
        
        self.factory = RequestFactory()
    
    @patch('django.core.files.storage.default_storage')
    def test_serve_file_success(self, mock_storage):
        """Test successful file serving."""
        # Create file
        file_id = str(uuid.uuid4())
        file_path = f'conversation_files/{self.agent.id}/{file_id}.txt'
        
        conversation_file = ConversationFile.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            file_name='test.txt',
            file_type='text/plain',
            file_size=100,
            file_id=file_id,
            download_url=''
        )
        conversation_file.file_path = file_path
        conversation_file.save()
        
        # Mock storage
        mock_storage.exists.return_value = True
        mock_file = Mock()
        mock_file.read.return_value = b'file content'
        mock_storage.open.return_value.__enter__ = Mock(return_value=mock_file)
        mock_storage.open.return_value.__exit__ = Mock(return_value=None)
        
        request = self.factory.get(f'/media/{file_path}')
        request.user = self.user
        
        response = serve_media_file(request, file_path)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain')
    
    def test_serve_file_not_in_conversation_files(self):
        """Test that files outside conversation_files directory are blocked."""
        request = self.factory.get('/media/other_directory/file.txt')
        request.user = self.user
        
        with self.assertRaises(Http404):
            serve_media_file(request, 'other_directory/file.txt')
    
    @patch('django.core.files.storage.default_storage')
    def test_serve_file_not_found(self, mock_storage):
        """Test that non-existent files return 404."""
        file_path = f'conversation_files/{self.agent.id}/nonexistent.txt'
        
        mock_storage.exists.return_value = False
        
        request = self.factory.get(f'/media/{file_path}')
        request.user = self.user
        
        with self.assertRaises(Http404):
            serve_media_file(request, file_path)
    
    @patch('django.core.files.storage.default_storage')
    def test_serve_file_unauthorized_user(self, mock_storage):
        """Test that unauthorized users cannot access files."""
        file_id = str(uuid.uuid4())
        file_path = f'conversation_files/{self.agent.id}/{file_id}.txt'
        
        conversation_file = ConversationFile.objects.create(
            agent=self.agent,
            conversation=self.conversation,
            file_name='test.txt',
            file_type='text/plain',
            file_size=100,
            file_id=file_id,
            download_url=''
        )
        conversation_file.file_path = file_path
        conversation_file.save()
        
        # Mock storage
        mock_storage.exists.return_value = True
        
        request = self.factory.get(f'/media/{file_path}')
        request.user = self.other_user  # Different user
        
        # Should raise 404 for unauthorized access
        with self.assertRaises(Http404):
            serve_media_file(request, file_path)

