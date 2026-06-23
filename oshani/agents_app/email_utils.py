"""Email utility with SMTP and AWS SES fallback."""
import logging
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)


def send_email_with_fallback(subject, message, from_email, recipient_list, 
                             html_message=None, fail_silently=False):
    """
    Send email using SMTP first, fallback to AWS SES if SMTP fails.
    
    Args:
        subject: Email subject
        message: Plain text message
        from_email: Sender email address
        recipient_list: List of recipient email addresses
        html_message: Optional HTML message
        fail_silently: If True, don't raise exceptions
    
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    # Try SMTP first
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=recipient_list,
            html_message=html_message,
            fail_silently=False  # We want to catch exceptions to try SES
        )
        logger.debug(f"Email sent successfully via SMTP to {recipient_list}")
        return True
    except Exception as smtp_error:
        logger.warning(f"SMTP email failed: {str(smtp_error)}. Attempting AWS SES fallback...")
        
        # Check if AssumeRole is configured - only use AssumeRole for SES
        aws_role_arn = getattr(settings, 'AWS_ROLE_ARN', '').strip()
        
        # Only use SES if AssumeRole is configured
        if not aws_role_arn:
            logger.warning("AWS_ROLE_ARN is not configured. Skipping SES fallback. Configure AWS_ROLE_ARN to use AssumeRole for SES email sending.")
            if not fail_silently:
                raise Exception(f"SMTP failed and AWS SES cannot be used (AWS_ROLE_ARN not configured). SMTP error: {str(smtp_error)}")
            return False
        
        # Fallback to AWS SES
        try:
            return send_email_via_ses(subject, message, from_email, recipient_list, 
                                     html_message=html_message, fail_silently=fail_silently)
        except Exception as ses_error:
            logger.error(f"AWS SES email also failed: {str(ses_error)}")
            if not fail_silently:
                raise
            return False


def _get_ses_client_with_assume_role(aws_region):
    """
    Get SES client using AssumeRole. Only AssumeRole is supported for email sending.
    
    Args:
        aws_region: AWS region for SES
        
    Returns:
        boto3 SES client with assumed role credentials
        
    Raises:
        Exception: If AWS_ROLE_ARN is not configured or AssumeRole fails
    """
    import boto3
    from botocore.exceptions import ClientError
    
    # Check if AssumeRole is configured - required for email sending
    aws_role_arn = getattr(settings, 'AWS_ROLE_ARN', '').strip()
    
    if not aws_role_arn:
        raise Exception("AWS_ROLE_ARN is required for email sending. Configure AWS_ROLE_ARN in settings.")
    
    # Use AssumeRole - always use default credential chain (IAM role on EC2) to assume role first
    logger.info(f"Using AWS AssumeRole for SES: {aws_role_arn}")
    logger.info("Using default AWS credential chain to assume role (will use IAM role if on EC2)")
    
    # Temporarily clear any invalid credentials from environment to ensure IAM role is used
    # Check if environment has placeholder/invalid credentials
    import os
    aws_access_key_id = getattr(settings, 'AWS_ACCESS_KEY_ID', '').strip()
    aws_secret_access_key = getattr(settings, 'AWS_SECRET_ACCESS_KEY', '').strip()
    
    # Check if credentials are placeholders
    placeholder_patterns = ['your_', 'placeholder', 'example', 'changeme', 'xxx', '***']
    is_placeholder = False
    if aws_access_key_id:
        is_placeholder = is_placeholder or any(pattern in aws_access_key_id.lower() for pattern in placeholder_patterns)
    if aws_secret_access_key:
        is_placeholder = is_placeholder or any(pattern in aws_secret_access_key.lower() for pattern in placeholder_patterns)
    
    # If placeholder credentials are in environment, temporarily remove them to use IAM role
    old_access_key = os.environ.pop('AWS_ACCESS_KEY_ID', None) if is_placeholder else None
    old_secret_key = os.environ.pop('AWS_SECRET_ACCESS_KEY', None) if is_placeholder else None
    old_session_token = os.environ.pop('AWS_SESSION_TOKEN', None) if is_placeholder else None
    
    try:
        # Create STS client using default credential chain (IAM role, env vars, credentials file, etc.)
        # After clearing invalid credentials, boto3 will use IAM role on EC2
        session = boto3.Session(region_name=aws_region)
        sts_client = session.client('sts', region_name=aws_region)
        
        # Verify base credentials work and check if we're already using the target role
        try:
            identity = sts_client.get_caller_identity()
            current_identity_arn = identity.get('Arn', '')
            logger.info(f"Base credentials verified. Identity: {current_identity_arn} (Account: {identity.get('Account', 'Unknown')})")
            
            # Check if current role is the same as target role (extract role name from ARN)
            # Current identity ARN format: arn:aws:sts::ACCOUNT:assumed-role/ROLE_NAME/SESSION_NAME
            # Target role ARN format: arn:aws:iam::ACCOUNT:role/ROLE_NAME
            if current_identity_arn:
                # Extract role name from current identity (assumed-role format)
                current_role_name = None
                if ':assumed-role/' in current_identity_arn:
                    # Format: arn:aws:sts::ACCOUNT:assumed-role/ROLE_NAME/SESSION_NAME
                    parts = current_identity_arn.split(':assumed-role/')
                    if len(parts) > 1:
                        current_role_name = parts[1].split('/')[0]
                elif ':role/' in current_identity_arn:
                    # Format: arn:aws:iam::ACCOUNT:role/ROLE_NAME
                    parts = current_identity_arn.split(':role/')
                    if len(parts) > 1:
                        current_role_name = parts[1].split('/')[0]
                
                # Extract role name from target role ARN
                target_role_name = None
                if ':role/' in aws_role_arn:
                    # Format: arn:aws:iam::ACCOUNT:role/ROLE_NAME
                    parts = aws_role_arn.split(':role/')
                    if len(parts) > 1:
                        target_role_name = parts[1].split('/')[0]
                
                # If we're already using the target role, skip AssumeRole and use current credentials
                if current_role_name and target_role_name and current_role_name == target_role_name:
                    logger.info(f"Current IAM role '{current_role_name}' matches target role '{target_role_name}'. Skipping AssumeRole and using current credentials.")
                    # Use current credentials directly - create SES client with default credential chain
                    ses_client = boto3.client('ses', region_name=aws_region)
                    return ses_client
                elif current_role_name and target_role_name:
                    logger.info(f"Current IAM role '{current_role_name}' differs from target role '{target_role_name}'. Proceeding with AssumeRole.")
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"Base credentials invalid for STS. Error: {error_code} - {error_msg}")
            logger.error("Please check:")
            logger.error("  1. If on EC2: Ensure instance has IAM role attached with sts:AssumeRole permission")
            logger.error("  2. Verify IAM role has permission to assume target role: " + aws_role_arn)
            logger.error("  3. Check if environment variables AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY are set to invalid values")
            logger.error("  4. Verify credentials file (~/.aws/credentials) if not using IAM role")
            raise Exception(f"Base AWS credentials are invalid. Cannot assume role. Error: {error_msg}")
        
        # Assume the role (only if current role is different from target role)
        assume_role_params = {
            'RoleArn': aws_role_arn,
            'RoleSessionName': getattr(settings, 'AWS_ROLE_SESSION_NAME', 'django-ses-session')
        }
        
        # Add ExternalId if configured (for cross-account access security)
        aws_role_external_id = getattr(settings, 'AWS_ROLE_EXTERNAL_ID', '').strip()
        if aws_role_external_id:
            assume_role_params['ExternalId'] = aws_role_external_id
        
        try:
            assumed_role = sts_client.assume_role(**assume_role_params)
            credentials = assumed_role['Credentials']
            
            logger.info(f"Successfully assumed role {aws_role_arn}. Session expires at {credentials['Expiration']}")
            
            # Create SES client with assumed role credentials
            ses_client = boto3.client(
                'ses',
                region_name=aws_region,
                aws_access_key_id=credentials['AccessKeyId'],
                aws_secret_access_key=credentials['SecretAccessKey'],
                aws_session_token=credentials['SessionToken']
            )
            return ses_client
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"Failed to assume role {aws_role_arn}: {error_code} - {error_msg}")
            
            # Provide helpful error messages
            if error_code == 'InvalidClientTokenId':
                logger.error("The security token (base credentials) is invalid.")
                logger.error("Please verify:")
                logger.error("  1. EC2 instance IAM role is properly attached (if using IAM role)")
                logger.error("  2. IAM role has sts:AssumeRole permission")
                logger.error("  3. Check if environment variables AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY are set to invalid values")
            elif error_code == 'AccessDenied':
                logger.error(f"Access denied when assuming role {aws_role_arn}")
                logger.error("This may occur if:")
                logger.error("  1. The current IAM role is the same as the target role (roles cannot assume themselves)")
                logger.error("  2. Base credentials (IAM role) don't have sts:AssumeRole permission")
                logger.error("  3. Trust relationship on target role doesn't allow your base identity to assume it")
                logger.error("  4. ExternalId is incorrect (if using cross-account access)")
                logger.error(f"Current identity: {current_identity_arn if 'current_identity_arn' in locals() else 'Unknown'}")
            
            raise Exception(f"Failed to assume AWS role {aws_role_arn}: {error_msg} (Error code: {error_code})")
    finally:
        # Restore environment variables if they were removed (only if they weren't placeholders)
        if old_access_key and not old_access_key.startswith('your_'):
            os.environ['AWS_ACCESS_KEY_ID'] = old_access_key
        if old_secret_key and not (old_secret_key and old_secret_key.startswith('your_')):
            os.environ['AWS_SECRET_ACCESS_KEY'] = old_secret_key
        if old_session_token:
            os.environ['AWS_SESSION_TOKEN'] = old_session_token


def send_email_via_ses(subject, message, from_email, recipient_list, 
                      html_message=None, fail_silently=False):
    """
    Send email using AWS SES.
    
    Args:
        subject: Email subject
        message: Plain text message
        from_email: Sender email address (must be verified in SES)
        recipient_list: List of recipient email addresses
        html_message: Optional HTML message
        fail_silently: If True, don't raise exceptions
    
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        from botocore.exceptions import ClientError, BotoCoreError
        
        # Get AWS region from settings or use default
        aws_region = getattr(settings, 'AWS_SES_REGION', getattr(settings, 'AWS_REGION', 'us-east-1'))
        
        # Get SES client (with AssumeRole support if configured)
        ses_client = _get_ses_client_with_assume_role(aws_region)
        
        # Prepare email destination
        destination = {
            'ToAddresses': recipient_list
        }
        
        # Prepare message
        message_body = {
            'Text': {
                'Data': message,
                'Charset': 'UTF-8'
            }
        }
        
        if html_message:
            message_body['Html'] = {
                'Data': html_message,
                'Charset': 'UTF-8'
            }
        
        message_dict = {
            'Subject': {
                'Data': subject,
                'Charset': 'UTF-8'
            },
            'Body': message_body
        }
        
        # CRITICAL: Always use the verified sender email from settings
        # This ensures we never send from unverified email addresses
        verified_from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'support@oshaani.com')
        if from_email != verified_from_email:
            logger.warning(f"[SES] Overriding sender email from '{from_email}' to verified email '{verified_from_email}'")
            logger.info(f"[SES] This ensures AWS SES email sending uses only verified sender addresses")
            from_email = verified_from_email
        
        logger.info(f"[SES] Sending email from verified sender: {from_email} to recipients: {recipient_list}")
        
        # Send email
        response = ses_client.send_email(
            Source=from_email,
            Destination=destination,
            Message=message_dict
        )
        
        logger.info(f"Email sent successfully via AWS SES to {recipient_list}. MessageId: {response.get('MessageId')}")
        return True
        
    except ImportError:
        error_msg = "boto3 is not installed. Cannot use AWS SES fallback."
        logger.error(error_msg)
        if not fail_silently:
            raise ImportError(error_msg)
        return False
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        error_msg = e.response.get('Error', {}).get('Message', str(e))
        logger.error(f"AWS SES error ({error_code}): {error_msg}")
        
        # Common SES errors
        if error_code == 'InvalidClientTokenId':
            logger.error("AWS credentials are invalid or missing. Please check:")
            logger.error("  1. AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables")
            logger.error("  2. If on EC2, ensure IAM role has SES permissions")
            logger.error("  3. Verify credentials are correct and not expired")
        elif error_code == 'MessageRejected':
            error_msg_full = e.response.get('Error', {}).get('Message', '')
            logger.error("=" * 80)
            logger.error("AWS SES EMAIL VERIFICATION ERROR")
            logger.error("=" * 80)
            logger.error("Email address is not verified in AWS SES.")
            logger.error("AWS SES requires email addresses to be verified before sending.")
            logger.error(f"Error details: {error_msg_full}")
            logger.error("")
            logger.error("CURRENT CONFIGURATION:")
            logger.error(f"  Sender email: {from_email}")
            logger.error(f"  Recipient emails: {', '.join(recipient_list)}")
            logger.error(f"  AWS Region: {aws_region}")
            logger.error("")
            
            # Try to automatically verify recipient emails if they're not verified
            # This is helpful in sandbox mode where recipients must be verified
            try:
                logger.info("Attempting to automatically verify recipient email addresses...")
                for recipient_email in recipient_list:
                    try:
                        # Check if email is already verified
                        verified_emails = ses_client.list_verified_email_addresses()
                        if recipient_email in verified_emails.get('VerifiedEmailAddresses', []):
                            logger.info(f"Recipient {recipient_email} is already verified")
                            continue
                        
                        # Attempt to verify the email
                        logger.info(f"Sending verification email to {recipient_email}...")
                        verify_response = ses_client.verify_email_identity(EmailAddress=recipient_email)
                        logger.info(f"Verification email sent to {recipient_email}. Response: {verify_response.get('ResponseMetadata', {}).get('RequestId', 'N/A')}")
                        logger.warning(f"Recipient {recipient_email} needs to verify their email address.")
                        logger.warning(f"A verification email has been sent to {recipient_email}.")
                        logger.warning(f"Once they click the verification link, emails can be sent to this address.")
                    except ClientError as verify_error:
                        verify_error_code = verify_error.response.get('Error', {}).get('Code', 'Unknown')
                        if verify_error_code == 'AlreadyExists':
                            logger.info(f"Recipient {recipient_email} verification is already in progress")
                        else:
                            logger.warning(f"Could not verify {recipient_email}: {verify_error.response.get('Error', {}).get('Message', str(verify_error))}")
            except Exception as auto_verify_error:
                logger.warning(f"Automatic email verification failed (non-critical): {str(auto_verify_error)}")
            
            logger.error("")
            logger.error("SOLUTION OPTIONS:")
            logger.error("")
            logger.error("OPTION 1: Verify the email address in AWS SES (Recommended)")
            logger.error("  1. Go to AWS SES Console: https://console.aws.amazon.com/ses/")
            logger.error(f"  2. Make sure you're in region: {aws_region}")
            logger.error("  3. Navigate to 'Verified identities' in the left menu")
            logger.error("  4. Click 'Create identity'")
            logger.error("  5. Select 'Email address' and enter the unverified email")
            logger.error("  6. Click 'Create identity'")
            logger.error("  7. Check the email inbox and click the verification link")
            logger.error("")
            logger.error("OPTION 2: Request Production Access (Allows sending to any email)")
            logger.error("  1. Go to AWS SES Console: https://console.aws.amazon.com/ses/")
            logger.error("  2. Navigate to 'Account dashboard'")
            logger.error("  3. Click 'Request production access'")
            logger.error("  4. Fill out the form and submit")
            logger.error("  5. Production access typically takes 24-48 hours to be approved")
            logger.error("")
            logger.error("IMPORTANT NOTES:")
            logger.error("  - In SES sandbox mode, you must verify BOTH sender AND recipient addresses")
            logger.error("  - With production access, you can send to any email address")
            logger.error("  - Sender email is automatically set to verified email: " + from_email)
            logger.error("=" * 80)
        elif error_code == 'MailFromDomainNotVerified':
            logger.error("Sender domain not verified in AWS SES. Verify the domain in SES console.")
        elif error_code == 'ConfigurationSetDoesNotExist':
            logger.error("SES configuration set does not exist. Check AWS_SES_CONFIGURATION_SET in settings.")
        
        if not fail_silently:
            raise
        return False
    except BotoCoreError as e:
        logger.error(f"AWS SES connection error: {str(e)}")
        if not fail_silently:
            raise
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending email via AWS SES: {str(e)}", exc_info=True)
        if not fail_silently:
            raise
        return False



