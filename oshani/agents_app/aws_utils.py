"""Utility functions for AWS operations with IAM role support."""
import boto3
import logging
import os
from django.conf import settings

logger = logging.getLogger(__name__)


def is_ec2_instance():
    """Check if running on EC2 instance.
    
    Supports both IMDSv1 and IMDSv2 (token-based) metadata service.
    """
    try:
        import urllib.request
        import urllib.error
        
        # Try IMDSv2 first (token-based, more secure)
        try:
            # Request a token
            token_request = urllib.request.Request(
                'http://169.254.169.254/latest/api/token',
                headers={'X-aws-ec2-metadata-token-ttl-seconds': '21600'},
                method='PUT'
            )
            token_response = urllib.request.urlopen(token_request, timeout=2)
            if token_response.status == 200:
                token = token_response.read().decode('utf-8')
                # Use token to access metadata
                metadata_request = urllib.request.Request(
                    'http://169.254.169.254/latest/meta-data/',
                    headers={'X-aws-ec2-metadata-token': token}
                )
                metadata_response = urllib.request.urlopen(metadata_request, timeout=2)
                if metadata_response.status == 200:
                    logger.debug("EC2 instance detected (IMDSv2)")
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            logger.debug(f"IMDSv2 check failed: {str(e)}, trying IMDSv1")
        
        # Fall back to IMDSv1 (legacy, less secure but still used)
        try:
            response = urllib.request.urlopen('http://169.254.169.254/latest/meta-data/', timeout=2)
            if response.status == 200:
                logger.debug("EC2 instance detected (IMDSv1)")
                return True
        except (urllib.error.URLError, urllib.error.HTTPError):
            pass
        
        return False
    except Exception as e:
        logger.debug(f"Error checking EC2 instance: {str(e)}")
        return False


def get_ec2_region():
    """Get the AWS region from EC2 instance metadata.
    
    Supports both IMDSv1 and IMDSv2 (token-based) metadata service.
    """
    try:
        import urllib.request
        import urllib.error
        
        # Get token for IMDSv2 if available
        token = None
        try:
            token_request = urllib.request.Request(
                'http://169.254.169.254/latest/api/token',
                headers={'X-aws-ec2-metadata-token-ttl-seconds': '21600'},
                method='PUT'
            )
            token_response = urllib.request.urlopen(token_request, timeout=2)
            if token_response.status == 200:
                token = token_response.read().decode('utf-8')
        except:
            pass  # Fall back to IMDSv1
        
        headers = {}
        if token:
            headers['X-aws-ec2-metadata-token'] = token
        
        # Method 1: Try placement/region endpoint
        try:
            region_request = urllib.request.Request(
                'http://169.254.169.254/latest/meta-data/placement/region',
                headers=headers
            )
            region_response = urllib.request.urlopen(region_request, timeout=2)
            if region_response.status == 200:
                return region_response.read().decode('utf-8').strip()
        except:
            # Method 2: Try availability zone and extract region
            try:
                az_request = urllib.request.Request(
                    'http://169.254.169.254/latest/meta-data/placement/availability-zone',
                    headers=headers
                )
                az_response = urllib.request.urlopen(az_request, timeout=2)
                if az_response.status == 200:
                    az = az_response.read().decode('utf-8').strip()
                    # Extract region from AZ (e.g., "us-east-1a" -> "us-east-1")
                    return az[:-1] if len(az) > 1 else None
            except:
                pass
    except Exception as e:
        logger.debug(f"Error getting EC2 region: {str(e)}")
    return None


def get_aws_region():
    """Get AWS region, preferring EC2 instance region if available."""
    if is_ec2_instance():
        ec2_region = get_ec2_region()
        if ec2_region:
            logger.info(f"Using EC2 instance region: {ec2_region}")
            return ec2_region
    
    # Fallback to configured region
    return getattr(settings, 'AWS_REGION', 'us-east-1')


def create_boto3_client(service_name, region_name=None, use_iam_role=True, refresh_on_error=True):
    """Create a boto3 client that uses IAM role when available.
    
    Args:
        service_name: Name of the AWS service (e.g., 'bedrock-runtime', 'bedrock', 'transcribe')
        region_name: AWS region (if None, will auto-detect from EC2 or use settings)
        use_iam_role: If True, prefer IAM role over explicit credentials when on EC2
        refresh_on_error: If True, automatically refresh credentials on credential errors
    
    Returns:
        boto3 client configured to use IAM role when available
    """
    # Determine region
    if region_name is None:
        region_name = get_aws_region()
    
    # If on EC2 and use_iam_role is True, ensure IAM role is used
    if is_ec2_instance() and use_iam_role:
        # On EC2, boto3 should automatically use IAM role from instance metadata
        # We don't need to clear env vars - boto3's default credential chain will
        # prioritize IAM role over env vars when on EC2
        try:
            # Create session - boto3 will automatically use IAM role from instance metadata
            # The credential chain on EC2 is: IAM role > env vars > credentials file
            session = boto3.Session(region_name=region_name)
            
            # Verify we're using IAM role credentials
            try:
                credentials = session.get_credentials()
                if credentials:
                    # Log credential source for debugging
                    if hasattr(credentials, 'method') and credentials.method == 'iam-role':
                        logger.info(f"Using IAM role credentials for {service_name} in region: {region_name}")
                    else:
                        logger.debug(f"Using credentials via {getattr(credentials, 'method', 'unknown')} method")
                    
                    # Try to get caller identity to verify credentials work
                    sts_client = session.client('sts', region_name=region_name)
                    identity = sts_client.get_caller_identity()
                    logger.info(f"AWS Identity: {identity.get('Arn', 'Unknown')} (Account: {identity.get('Account', 'Unknown')})")
            except Exception as e:
                logger.warning(f"Could not verify IAM role credentials: {str(e)}")
                # Continue anyway - the actual service call will fail if credentials are invalid
            
            client = session.client(service_name, region_name=region_name)
            logger.info(f"Created {service_name} client using IAM role (EC2 instance) in region: {region_name}")
            return client
        except Exception as e:
            logger.error(f"Failed to create {service_name} client with IAM role: {str(e)}")
            # Fall back to default credential chain
            logger.info("Falling back to default credential chain")
            client = boto3.client(service_name, region_name=region_name)
            return client
    else:
        # Not on EC2 or use_iam_role is False - use default credential chain
        # This will still use IAM role if available, but also check env vars and credentials file
        client = boto3.client(service_name, region_name=region_name)
        logger.debug(f"Created {service_name} client using default credential chain in region: {region_name}")
        return client








