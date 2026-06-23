"""
Unit tests for Agent sharing functionality.
"""
from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
import secrets

from .models import Agent, AgentShare, AgentPublicShare


class AgentShareModelTestCase(TestCase):
    """Test cases for AgentShare model."""
    
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
    
    def test_create_agent_share(self):
        """Test creating an AgentShare."""
        share = AgentShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            email='shared@example.com',
            token=secrets.token_urlsafe(32),
            message='Check this out!'
        )
        
        self.assertEqual(share.agent, self.agent)
        self.assertEqual(share.shared_by, self.owner)
        self.assertEqual(share.email, 'shared@example.com')
        self.assertFalse(share.is_accepted)
        self.assertIsNotNone(share.token)
    
    def test_agent_share_is_expired(self):
        """Test is_expired method."""
        # Create share with expiration
        share = AgentShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            email='shared@example.com',
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() - timedelta(days=1)  # Expired
        )
        
        self.assertTrue(share.is_expired())
        
        # Create share without expiration
        share_no_expiry = AgentShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            email='other@example.com',
            token=secrets.token_urlsafe(32)
        )
        
        self.assertFalse(share_no_expiry.is_expired())
    
    def test_agent_share_is_valid(self):
        """Test is_valid method."""
        # Valid share
        valid_share = AgentShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            email='shared@example.com',
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timedelta(days=7)
        )
        
        self.assertTrue(valid_share.is_valid())
        
        # Expired share
        expired_share = AgentShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            email='expired@example.com',
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() - timedelta(days=1)
        )
        
        self.assertFalse(expired_share.is_valid())
        
        # Accepted share
        accepted_share = AgentShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            email='accepted@example.com',
            token=secrets.token_urlsafe(32),
            is_accepted=True
        )
        
        self.assertFalse(accepted_share.is_valid())  # Already accepted
    
    def test_agent_share_str(self):
        """Test AgentShare string representation."""
        share = AgentShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            email='shared@example.com',
            token=secrets.token_urlsafe(32)
        )
        
        str_repr = str(share)
        self.assertIn(self.agent.name, str_repr)
        self.assertIn('shared@example.com', str_repr)
        self.assertIn(self.owner.username, str_repr)


class AgentPublicShareModelTestCase(TestCase):
    """Test cases for AgentPublicShare model."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.owner = User.objects.create_user(
            username='owner',
            email='owner@example.com',
            password='pass123'
        )
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent',
            user=self.owner,
            status='published',
            configuration={}
        )
    
    def test_create_public_share(self):
        """Test creating an AgentPublicShare."""
        public_share = AgentPublicShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            token=secrets.token_urlsafe(32)
        )
        
        self.assertEqual(public_share.agent, self.agent)
        self.assertEqual(public_share.shared_by, self.owner)
        self.assertTrue(public_share.is_active)
        self.assertEqual(public_share.access_count, 0)
        self.assertIsNotNone(public_share.token)
    
    def test_public_share_is_expired(self):
        """Test is_expired method."""
        # Create share with expiration
        expired_share = AgentPublicShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() - timedelta(days=1)
        )
        
        self.assertTrue(expired_share.is_expired())
        
        # Create share without expiration
        no_expiry_share = AgentPublicShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            token=secrets.token_urlsafe(32)
        )
        
        self.assertFalse(no_expiry_share.is_expired())
    
    def test_public_share_is_valid(self):
        """Test is_valid method."""
        # Valid share
        valid_share = AgentPublicShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            token=secrets.token_urlsafe(32),
            is_active=True,
            expires_at=timezone.now() + timedelta(days=7)
        )
        
        self.assertTrue(valid_share.is_valid())
        
        # Inactive share
        inactive_share = AgentPublicShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            token=secrets.token_urlsafe(32),
            is_active=False
        )
        
        self.assertFalse(inactive_share.is_valid())
        
        # Expired share
        expired_share = AgentPublicShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            token=secrets.token_urlsafe(32),
            is_active=True,
            expires_at=timezone.now() - timedelta(days=1)
        )
        
        self.assertFalse(expired_share.is_valid())
    
    def test_increment_access(self):
        """Test increment_access method."""
        public_share = AgentPublicShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            token=secrets.token_urlsafe(32)
        )
        
        # Initial count should be 0
        self.assertEqual(public_share.access_count, 0)
        self.assertIsNone(public_share.last_accessed_at)
        
        # Increment access
        public_share.increment_access()
        
        # Verify count increased
        self.assertEqual(public_share.access_count, 1)
        self.assertIsNotNone(public_share.last_accessed_at)
        
        # Increment again
        public_share.increment_access()
        self.assertEqual(public_share.access_count, 2)
    
    def test_public_share_str(self):
        """Test AgentPublicShare string representation."""
        public_share = AgentPublicShare.objects.create(
            agent=self.agent,
            shared_by=self.owner,
            token=secrets.token_urlsafe(32)
        )
        
        str_repr = str(public_share)
        self.assertIn(self.agent.name, str_repr)
        self.assertIn(self.owner.username, str_repr)












