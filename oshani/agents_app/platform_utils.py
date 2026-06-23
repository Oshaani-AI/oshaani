"""Platform access helpers for open-source self-hosted deployments."""
from decimal import Decimal


def can_use_bedrock(user):
    if not user or not user.is_authenticated:
        return False, "Authentication required"
    return True, None


def can_use_ollama(user):
    if not user or not user.is_authenticated:
        return False, "Authentication required"
    return True, None


def enforce_model_provider(user, requested_provider):
    if not user or not user.is_authenticated:
        return False, "Authentication required", None
    if requested_provider in ('bedrock', 'ollama'):
        return True, None, None
    return False, f"Unknown model provider: {requested_provider}", None


def can_perform_action(user, metric_type, count=1, is_daily=False):
    return True, 0, None, None


def track_usage(user, metric_type, count=1, metadata=None, is_daily=False):
    return None


def get_usage_count(user, metric_type, is_daily=False):
    return 0


def get_usage_limit(user, metric_type):
    return None


def has_sufficient_credits(user, action_type, count=1):
    return True, Decimal('0'), Decimal('0')


def get_credit_cost(action_type, count=1):
    return Decimal('0')


def get_conversation_retention_days(user, default_retention_days=30):
    return default_retention_days


def process_revenue_share(user_who_spent, agent_owner, credits_spent, agent_name, share_percentage=None):
    return False, Decimal('0'), None


def get_agent_revenue_share_percentage(agent):
    return Decimal('0')
