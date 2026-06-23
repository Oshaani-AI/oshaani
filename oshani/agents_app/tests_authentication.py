"""
Unit tests for authentication classes.
"""
from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User
from rest_framework import exceptions

from .models import Agent, UserProfile, UserAPIKey
from .authentication import (
    AgentAPIKeyAuthentication,
    UserAPIKeyAuthentication,
    SessionOrAgentAPIKeyAuthentication
)
from .utils import generate_api_key, hash_api_key


class AgentAPIKeyAuthenticationTestCase(TestCase):
    """Test cases for AgentAPIKeyAuthentication."""
    
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
        
        # Generate API key
        self.api_key = self.agent.generate_api_key()
        
        self.auth = AgentAPIKeyAuthentication()
        self.factory = RequestFactory()
    
    def test_authenticate_with_valid_api_key(self):
        """Test authentication with valid API key."""
        request = self.factory.get(
            '/api/test/',
            HTTP_AUTHORIZATION=f'ApiKey {self.api_key}'
        )
        
        user, agent = self.auth.authenticate(request)
        
        self.assertIsNone(user)  # Should be None for agent auth
        self.assertIsNotNone(agent)
        self.assertEqual(agent.id, self.agent.id)
    
    def test_authenticate_with_x_api_key_header(self):
        """Test authentication with X-API-Key header."""
        request = self.factory.get(
            '/api/test/',
            HTTP_X_API_KEY=self.api_key
        )
        
        user, agent = self.auth.authenticate(request)
        
        self.assertIsNone(user)
        self.assertIsNotNone(agent)
        self.assertEqual(agent.id, self.agent.id)
    
    def test_authenticate_with_invalid_api_key(self):
        """Test authentication with invalid API key."""
        request = self.factory.get(
            '/api/test/',
            HTTP_AUTHORIZATION='ApiKey invalid-key'
        )
        
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(request)
    
    def test_authenticate_with_no_api_key(self):
        """Test authentication with no API key."""
        request = self.factory.get('/api/test/')
        
        result = self.auth.authenticate(request)
        self.assertIsNone(result)
    
    def test_authenticate_with_unpublished_agent(self):
        """Test that unpublished agents cannot authenticate."""
        # Unpublish agent
        self.agent.status = 'draft'
        self.agent.save()
        
        request = self.factory.get(
            '/api/test/',
            HTTP_AUTHORIZATION=f'ApiKey {self.api_key}'
        )
        
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(request)
    
    def test_get_api_key_from_authorization_header(self):
        """Test extracting API key from Authorization header."""
        request = self.factory.get(
            '/api/test/',
            HTTP_AUTHORIZATION='ApiKey test-key-123'
        )
        
        api_key = self.auth.get_api_key(request)
        self.assertEqual(api_key, 'test-key-123')
    
    def test_get_api_key_from_x_api_key_header(self):
        """Test extracting API key from X-API-Key header."""
        request = self.factory.get(
            '/api/test/',
            HTTP_X_API_KEY='test-key-456'
        )
        
        api_key = self.auth.get_api_key(request)
        self.assertEqual(api_key, 'test-key-456')


class UserAPIKeyAuthenticationTestCase(TestCase):
    """Test cases for UserAPIKeyAuthentication."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # Create user API key
        self.api_key = generate_api_key()
        self.user_api_key = UserAPIKey.objects.create(
            user=self.user,
            name='Test Key',
            api_key_hash=hash_api_key(self.api_key),
            is_active=True
        )
        
        self.auth = UserAPIKeyAuthentication()
        self.factory = RequestFactory()
    
    def test_authenticate_with_valid_user_api_key(self):
        """Test authentication with valid user API key."""
        request = self.factory.get(
            '/api/test/',
            HTTP_AUTHORIZATION=f'ApiKey {self.api_key}'
        )
        
        user, auth = self.auth.authenticate(request)
        
        self.assertIsNotNone(user)
        self.assertEqual(user.id, self.user.id)
    
    def test_authenticate_with_invalid_user_api_key(self):
        """Test authentication with invalid user API key."""
        request = self.factory.get(
            '/api/test/',
            HTTP_AUTHORIZATION='ApiKey invalid-key'
        )
        
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(request)
    
    def test_authenticate_with_inactive_key(self):
        """Test that inactive keys cannot authenticate."""
        self.user_api_key.is_active = False
        self.user_api_key.save()
        
        request = self.factory.get(
            '/api/test/',
            HTTP_AUTHORIZATION=f'ApiKey {self.api_key}'
        )
        
        with self.assertRaises(exceptions.AuthenticationFailed):
            self.auth.authenticate(request)


class SessionOrAgentAPIKeyAuthenticationTestCase(TestCase):
    """Test cases for SessionOrAgentAPIKeyAuthentication."""
    
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
        
        self.api_key = self.agent.generate_api_key()
        
        self.auth = SessionOrAgentAPIKeyAuthentication()
        self.factory = RequestFactory()
    
    def test_authenticate_with_api_key(self):
        """Test authentication with API key."""
        request = self.factory.get(
            '/api/test/',
            HTTP_AUTHORIZATION=f'ApiKey {self.api_key}'
        )
        
        user, agent = self.auth.authenticate(request)
        
        self.assertIsNone(user)
        self.assertIsNotNone(agent)
        self.assertEqual(agent.id, self.agent.id)
    
    def test_authenticate_with_no_auth(self):
        """Test authentication with no auth (should return None)."""
        request = self.factory.get('/api/test/')
        
        result = self.auth.authenticate(request)
        self.assertIsNone(result)

