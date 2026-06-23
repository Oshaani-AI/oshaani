"""
Unit tests for utility functions.
"""
from django.test import TestCase
from .utils import generate_api_key, hash_api_key, verify_api_key


class UtilsTestCase(TestCase):
    """Test cases for utility functions."""
    
    def test_generate_api_key(self):
        """Test API key generation."""
        key1 = generate_api_key()
        key2 = generate_api_key()
        
        # Keys should be different
        self.assertNotEqual(key1, key2)
        
        # Keys should be strings
        self.assertIsInstance(key1, str)
        self.assertIsInstance(key2, str)
        
        # Keys should have reasonable length
        self.assertGreater(len(key1), 20)
        self.assertGreater(len(key2), 20)
    
    def test_hash_api_key(self):
        """Test API key hashing."""
        key = generate_api_key()
        hash1 = hash_api_key(key)
        hash2 = hash_api_key(key)
        
        # Same key should produce same hash
        self.assertEqual(hash1, hash2)
        
        # Hash should be different from original key
        self.assertNotEqual(hash1, key)
        
        # Hash should be hex string
        self.assertIsInstance(hash1, str)
        self.assertEqual(len(hash1), 64)  # SHA256 produces 64 char hex
    
    def test_hash_api_key_different_keys(self):
        """Test that different keys produce different hashes."""
        key1 = generate_api_key()
        key2 = generate_api_key()
        
        hash1 = hash_api_key(key1)
        hash2 = hash_api_key(key2)
        
        self.assertNotEqual(hash1, hash2)
    
    def test_verify_api_key(self):
        """Test API key verification."""
        key = generate_api_key()
        hashed = hash_api_key(key)
        
        # Correct key should verify
        self.assertTrue(verify_api_key(key, hashed))
        
        # Wrong key should not verify
        wrong_key = generate_api_key()
        self.assertFalse(verify_api_key(wrong_key, hashed))
        
        # Wrong hash should not verify
        wrong_hash = hash_api_key(wrong_key)
        self.assertFalse(verify_api_key(key, wrong_hash))
    
    def test_verify_api_key_edge_cases(self):
        """Test API key verification edge cases."""
        key = generate_api_key()
        hashed = hash_api_key(key)
        
        # Empty key should not verify
        self.assertFalse(verify_api_key('', hashed))
        
        # Empty hash should not verify
        self.assertFalse(verify_api_key(key, ''))












