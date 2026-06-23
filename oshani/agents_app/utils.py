"""Utility functions for agents_app."""
import secrets
import hashlib


def generate_api_key():
    """Generate a secure random API key."""
    return secrets.token_urlsafe(32)


def hash_api_key(api_key):
    """Hash an API key for storage."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def verify_api_key(api_key, hashed_key):
    """Verify an API key against its hash."""
    return hash_api_key(api_key) == hashed_key

