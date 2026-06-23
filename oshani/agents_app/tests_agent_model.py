"""
Unit tests for Agent model methods.
"""
from django.test import TestCase
from django.contrib.auth.models import User

from .models import Agent, AIModel, TrainingData


class AgentModelMethodsTestCase(TestCase):
    """Test cases for Agent model methods."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.model = AIModel.objects.create(
            model_name='Test Model',
            model_id='test-model-id',
            provider='bedrock',
            is_available=True
        )
        
        self.agent = Agent.objects.create(
            name='Test Agent',
            description='Test agent',
            user=self.user,
            model=self.model,
            status='draft',
            configuration={'instruction': 'Test instruction'}
        )
    
    def test_generate_api_key(self):
        """Test API key generation."""
        api_key = self.agent.generate_api_key()
        
        self.assertIsNotNone(api_key)
        self.assertGreater(len(api_key), 0)
        self.assertIsNotNone(self.agent.api_key_hash)
        self.assertIsNone(self.agent.api_key)  # Plaintext should not be stored
    
    def test_verify_api_key(self):
        """Test API key verification."""
        api_key = self.agent.generate_api_key()
        
        # Verify correct key
        self.assertTrue(self.agent.verify_api_key(api_key))
        
        # Verify incorrect key
        self.assertFalse(self.agent.verify_api_key('wrong-key'))
    
    def test_api_key_preview(self):
        """Test API key preview generation."""
        api_key = self.agent.generate_api_key()
        preview = self.agent.api_key_preview
        
        self.assertIsNotNone(preview)
        self.assertIn('...', preview)
        self.assertNotEqual(preview, api_key)  # Should not expose full key
    
    def test_sync_training_data_count(self):
        """Test syncing training data count."""
        # Initially count should be 0
        self.assertEqual(self.agent.training_data_count, 0)
        
        # Add training data
        TrainingData.objects.create(
            agent=self.agent,
            data_type='text',
            content={'input': 'test', 'output': 'response'}
        )
        # Signals may already sync training_data_count; assert DB is consistent
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.training_data_count, 1)
        self.assertFalse(self.agent.sync_training_data_count())

        # Force a mismatch to verify sync_training_data_count repairs it
        Agent.objects.filter(pk=self.agent.pk).update(training_data_count=0)
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.sync_training_data_count())
        self.assertEqual(self.agent.training_data_count, 1)
        self.assertFalse(self.agent.sync_training_data_count())
    
    def test_publish_agent(self):
        """Test publishing an agent."""
        # Set status to testing
        self.agent.status = 'testing'
        self.agent.save()
        
        # Add training data
        TrainingData.objects.create(
            agent=self.agent,
            data_type='text',
            content={'input': 'test', 'output': 'response'}
        )
        
        # Publish agent
        self.agent.publish()
        
        # Verify status changed
        self.assertEqual(self.agent.status, 'published')
        self.assertIsNotNone(self.agent.published_at)
        self.assertIsNotNone(self.agent.published_configuration)
        self.assertEqual(self.agent.published_training_data_count, 1)
        self.assertIsNotNone(self.agent.api_key_hash)
    
    def test_publish_agent_wrong_status(self):
        """Test that publishing requires testing status."""
        # Try to publish from draft status
        with self.assertRaises(ValueError):
            self.agent.publish()
    
    def test_has_unpublished_changes_no_changes(self):
        """Test has_unpublished_changes when no changes."""
        # Publish agent
        self.agent.status = 'testing'
        self.agent.save()
        self.agent.publish()
        
        # No changes should return False
        self.assertFalse(self.agent.has_unpublished_changes())
    
    def test_has_unpublished_changes_configuration(self):
        """Test has_unpublished_changes when configuration changes."""
        # Publish agent
        self.agent.status = 'testing'
        self.agent.save()
        self.agent.publish()
        
        # Change configuration
        self.agent.configuration['instruction'] = 'New instruction'
        self.agent.save()
        
        # Should detect changes
        self.assertTrue(self.agent.has_unpublished_changes())
    
    def test_has_unpublished_changes_training_data(self):
        """Test has_unpublished_changes when training data changes."""
        # Publish agent
        self.agent.status = 'testing'
        self.agent.save()
        self.agent.publish()
        
        # Add training data
        TrainingData.objects.create(
            agent=self.agent,
            data_type='text',
            content={'input': 'test', 'output': 'response'}
        )
        self.agent.sync_training_data_count()
        
        # Should detect changes
        self.assertTrue(self.agent.has_unpublished_changes())
    
    def test_get_change_summary(self):
        """Test get_change_summary method."""
        # Publish agent
        self.agent.status = 'testing'
        self.agent.save()
        self.agent.publish()
        
        # Make changes
        self.agent.configuration['instruction'] = 'New instruction'
        TrainingData.objects.create(
            agent=self.agent,
            data_type='text',
            content={'input': 'test', 'output': 'response'}
        )
        self.agent.sync_training_data_count()
        self.agent.save()
        
        # Get summary
        changes = self.agent.get_change_summary()
        
        self.assertGreater(len(changes), 0)
        self.assertTrue(any('Training data' in change for change in changes))
        self.assertTrue(any('Configuration' in change for change in changes))
    
    def test_get_change_summary_no_changes(self):
        """Test get_change_summary when no changes."""
        # Publish agent
        self.agent.status = 'testing'
        self.agent.save()
        self.agent.publish()
        
        # Get summary (no changes)
        changes = self.agent.get_change_summary()
        self.assertEqual(len(changes), 0)


class AgentStringRepresentationTestCase(TestCase):
    """Test cases for Agent string representation."""
    
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
    
    def test_agent_str(self):
        """Test Agent string representation."""
        str_repr = str(self.agent)
        self.assertIn('Test Agent', str_repr)
        self.assertIn('published', str_repr)







