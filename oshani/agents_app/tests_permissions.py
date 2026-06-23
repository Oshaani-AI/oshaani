"""
Unit tests for permission classes.
"""
from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User, AnonymousUser

from .models import Agent, AgentShare
from .permissions import (
    IsAgentOwner,
    IsAgentOwnerOrReadOnly,
    HasAgentAPIKey,
    IsPublishedAgent,
    SessionOrAgentAPIKeyPermission
)


class IsAgentOwnerTestCase(TestCase):
    """Test cases for IsAgentOwner permission."""
    
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
        
        self.other_user = User.objects.create_user(
            username='other',
            email='other@example.com',
            password='pass123'
        )
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent',
            user=self.owner,
            status='published',
            configuration={}
        )
        
        # Create share for shared_user
        AgentShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            email=self.shared_user.email,
            is_accepted=True,
            accepted_by=self.shared_user
        )
        
        self.permission = IsAgentOwner()
        self.factory = RequestFactory()
    
    def test_owner_has_full_access(self):
        """Test that owner has full access."""
        request = self.factory.get('/api/test/')
        request.user = self.owner
        
        # Read access
        self.assertTrue(
            self.permission.has_object_permission(request, None, self.agent)
        )
        
        # Write access
        request.method = 'POST'
        self.assertTrue(
            self.permission.has_object_permission(request, None, self.agent)
        )
    
    def test_shared_user_read_only(self):
        """Test that shared user has read-only access."""
        request = self.factory.get('/api/test/')
        request.user = self.shared_user
        
        # Read access should be allowed
        self.assertTrue(
            self.permission.has_object_permission(request, None, self.agent)
        )
        
        # Write access should be denied
        request.method = 'POST'
        self.assertFalse(
            self.permission.has_object_permission(request, None, self.agent)
        )
    
    def test_other_user_no_access(self):
        """Test that other user has no access."""
        request = self.factory.get('/api/test/')
        request.user = self.other_user
        
        # Read access should be denied
        self.assertFalse(
            self.permission.has_object_permission(request, None, self.agent)
        )
        
        # Write access should be denied
        request.method = 'POST'
        self.assertFalse(
            self.permission.has_object_permission(request, None, self.agent)
        )


class IsAgentOwnerOrReadOnlyTestCase(TestCase):
    """Test cases for IsAgentOwnerOrReadOnly permission."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.owner = User.objects.create_user(
            username='owner',
            email='owner@example.com',
            password='pass123'
        )
        
        self.other_user = User.objects.create_user(
            username='other',
            email='other@example.com',
            password='pass123'
        )
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent',
            user=self.owner,
            status='published',
            configuration={}
        )
        
        self.permission = IsAgentOwnerOrReadOnly()
        self.factory = RequestFactory()
    
    def test_owner_has_full_access(self):
        """Test that owner has full access."""
        request = self.factory.get('/api/test/')
        request.user = self.owner
        
        # Read access
        self.assertTrue(
            self.permission.has_object_permission(request, None, self.agent)
        )
        
        # Write access
        request.method = 'POST'
        self.assertTrue(
            self.permission.has_object_permission(request, None, self.agent)
        )
    
    def test_other_user_read_only(self):
        """Test that other user has read-only access."""
        request = self.factory.get('/api/test/')
        request.user = self.other_user
        
        # Read access should be allowed
        self.assertTrue(
            self.permission.has_object_permission(request, None, self.agent)
        )
        
        # Write access should be denied
        request.method = 'POST'
        self.assertFalse(
            self.permission.has_object_permission(request, None, self.agent)
        )


class HasAgentAPIKeyTestCase(TestCase):
    """Test cases for HasAgentAPIKey permission."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='owner',
            email='owner@example.com',
            password='pass123'
        )
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent',
            user=self.user,
            status='published',
            configuration={}
        )
        
        self.permission = HasAgentAPIKey()
        self.factory = RequestFactory()
    
    def test_published_agent_has_permission(self):
        """Test that published agent has permission."""
        request = self.factory.get('/api/test/')
        request.auth = self.agent
        
        self.assertTrue(self.permission.has_permission(request, None))
    
    def test_unpublished_agent_no_permission(self):
        """Test that unpublished agent has no permission."""
        self.agent.status = 'draft'
        self.agent.save()
        
        request = self.factory.get('/api/test/')
        request.auth = self.agent
        
        self.assertFalse(self.permission.has_permission(request, None))
    
    def test_no_agent_no_permission(self):
        """Test that request without agent has no permission."""
        request = self.factory.get('/api/test/')
        request.auth = None
        
        self.assertFalse(self.permission.has_permission(request, None))


class IsPublishedAgentTestCase(TestCase):
    """Test cases for IsPublishedAgent permission."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='owner',
            email='owner@example.com',
            password='pass123'
        )
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent',
            user=self.user,
            status='published',
            configuration={}
        )
        
        self.permission = IsPublishedAgent()
        self.factory = RequestFactory()
    
    def test_published_agent_has_permission(self):
        """Test that published agent has permission."""
        request = self.factory.get('/api/test/')
        
        self.assertTrue(
            self.permission.has_object_permission(request, None, self.agent)
        )
    
    def test_unpublished_agent_no_permission(self):
        """Test that unpublished agent has no permission."""
        self.agent.status = 'draft'
        self.agent.save()
        
        request = self.factory.get('/api/test/')
        
        self.assertFalse(
            self.permission.has_object_permission(request, None, self.agent)
        )


class SessionOrAgentAPIKeyPermissionTestCase(TestCase):
    """Test cases for SessionOrAgentAPIKeyPermission."""
    
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
        
        self.permission = SessionOrAgentAPIKeyPermission()
        self.factory = RequestFactory()
    
    def test_session_auth_has_permission(self):
        """Test that session authentication has permission."""
        request = self.factory.get('/api/test/')
        request.user = self.user
        
        self.assertTrue(self.permission.has_permission(request, None))
    
    def test_agent_api_key_auth_has_permission(self):
        """Test that agent API key authentication has permission."""
        request = self.factory.get('/api/test/')
        request.user = AnonymousUser()
        request.auth = self.agent
        
        self.assertTrue(self.permission.has_permission(request, None))
    
    def test_unpublished_agent_no_permission(self):
        """Test that unpublished agent has no permission."""
        self.agent.status = 'draft'
        self.agent.save()
        
        request = self.factory.get('/api/test/')
        request.user = AnonymousUser()
        request.auth = self.agent
        
        self.assertFalse(self.permission.has_permission(request, None))
    
    def test_no_auth_no_permission(self):
        """Test that request without auth has no permission."""
        request = self.factory.get('/api/test/')
        request.user = AnonymousUser()
        
        self.assertFalse(self.permission.has_permission(request, None))







