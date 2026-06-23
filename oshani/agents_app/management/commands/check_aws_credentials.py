"""Management command to check AWS credentials and Bedrock access."""
from django.core.management.base import BaseCommand
from django.conf import settings
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check AWS credentials and Bedrock access'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Checking AWS credentials and Bedrock access...\n'))
        
        # Check environment variables
        self.stdout.write('1. Checking environment variables:')
        aws_access_key = getattr(settings, 'AWS_ACCESS_KEY_ID', '')
        aws_secret_key = getattr(settings, 'AWS_SECRET_ACCESS_KEY', '')
        aws_region = getattr(settings, 'AWS_REGION', 'us-east-1')
        
        if aws_access_key:
            if aws_access_key.startswith('your_'):
                self.stdout.write(self.style.WARNING(f'  AWS_ACCESS_KEY_ID: Placeholder value detected'))
            else:
                self.stdout.write(self.style.SUCCESS(f'  AWS_ACCESS_KEY_ID: Set (length: {len(aws_access_key)})'))
        else:
            self.stdout.write(self.style.WARNING('  AWS_ACCESS_KEY_ID: Not set'))
        
        if aws_secret_key:
            if aws_secret_key.startswith('your_'):
                self.stdout.write(self.style.WARNING(f'  AWS_SECRET_ACCESS_KEY: Placeholder value detected'))
            else:
                self.stdout.write(self.style.SUCCESS(f'  AWS_SECRET_ACCESS_KEY: Set (length: {len(aws_secret_key)})'))
        else:
            self.stdout.write(self.style.WARNING('  AWS_SECRET_ACCESS_KEY: Not set'))
        
        self.stdout.write(self.style.SUCCESS(f'  AWS_REGION: {aws_region}\n'))
        
        # Check if running on EC2
        self.stdout.write('2. Checking EC2 instance metadata:')
        try:
            import urllib.request
            import urllib.error
            
            # Try IMDSv2 first (token-based)
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
                    self.stdout.write(self.style.SUCCESS('  EC2 instance detected (IMDSv2)'))
            except Exception:
                # Fall back to IMDSv1
                try:
                    response = urllib.request.urlopen('http://169.254.169.254/latest/meta-data/', timeout=2)
                    if response.status == 200:
                        self.stdout.write(self.style.SUCCESS('  EC2 instance detected (IMDSv1)'))
                except Exception:
                    self.stdout.write(self.style.WARNING('  Not running on EC2 or metadata service unavailable'))
                    self.stdout.write('')
                    return
            
            # Get region
            headers = {}
            if token:
                headers['X-aws-ec2-metadata-token'] = token
            
            try:
                region_request = urllib.request.Request(
                    'http://169.254.169.254/latest/meta-data/placement/region',
                    headers=headers
                )
                region_response = urllib.request.urlopen(region_request, timeout=2)
                if region_response.status == 200:
                    ec2_region = region_response.read().decode('utf-8').strip()
                    self.stdout.write(self.style.SUCCESS(f'  EC2 region: {ec2_region}'))
            except Exception:
                pass
            
            # Get IAM role name
            try:
                role_request = urllib.request.Request(
                    'http://169.254.169.254/latest/meta-data/iam/security-credentials/',
                    headers=headers
                )
                role_response = urllib.request.urlopen(role_request, timeout=2)
                if role_response.status == 200:
                    role_name = role_response.read().decode('utf-8').strip()
                    self.stdout.write(self.style.SUCCESS(f'  IAM role: {role_name}'))
            except Exception:
                pass
            
            self.stdout.write('')
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'  Error checking EC2 metadata: {str(e)}'))
            self.stdout.write('')
        
        # Test AWS credentials
        self.stdout.write('\n3. Testing AWS credentials:')
        try:
            # Try to get caller identity
            session = boto3.Session(region_name=aws_region)
            sts_client = session.client('sts')
            identity = sts_client.get_caller_identity()
            
            self.stdout.write(self.style.SUCCESS(f'  Account ID: {identity.get("Account")}'))
            self.stdout.write(self.style.SUCCESS(f'  User ARN: {identity.get("Arn")}'))
            self.stdout.write(self.style.SUCCESS('  Credentials are valid'))
        except NoCredentialsError:
            self.stdout.write(self.style.ERROR('  No credentials found'))
            self.stdout.write(self.style.WARNING('  Solutions:'))
            self.stdout.write('    - Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables')
            self.stdout.write('    - Configure AWS credentials file (~/.aws/credentials)')
            self.stdout.write('    - Attach an IAM role to your EC2 instance')
            return
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            error_message = e.response.get('Error', {}).get('Message', '')
            self.stdout.write(self.style.ERROR(f'  Credential error: {error_code}'))
            self.stdout.write(self.style.ERROR(f'  Message: {error_message}'))
            if error_code in ('InvalidClientTokenId', 'UnrecognizedClientException'):
                self.stdout.write(self.style.WARNING('  Solutions:'))
                self.stdout.write('    - Verify AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are correct')
                self.stdout.write('    - Check if credentials have expired')
                self.stdout.write('    - If using IAM role, ensure it has proper permissions')
            return
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  Error: {str(e)}'))
            return
        
        # Test Bedrock access
        self.stdout.write('\n4. Testing Bedrock access:')
        try:
            bedrock_runtime = session.client('bedrock-runtime', region_name=aws_region)
            
            # Try to list foundation models (requires bedrock:ListFoundationModels permission)
            bedrock_client = session.client('bedrock', region_name=aws_region)
            try:
                response = bedrock_client.list_foundation_models()
                model_count = len(response.get('modelSummaries', []))
                self.stdout.write(self.style.SUCCESS(f'  Can list foundation models ({model_count} models available)'))
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                if error_code == 'AccessDeniedException':
                    self.stdout.write(self.style.WARNING('  Cannot list foundation models (AccessDenied)'))
                    self.stdout.write(self.style.WARNING('  This is OK if you only need to invoke models'))
                else:
                    self.stdout.write(self.style.WARNING(f'  Cannot list foundation models: {error_code}'))
            
            # Try to invoke a model (this is what actually fails with UnrecognizedClientException)
            # We'll use a simple test with Titan Embeddings
            import json
            test_body = json.dumps({"inputText": "test"})
            try:
                response = bedrock_runtime.invoke_model(
                    modelId="amazon.titan-embed-text-v1",
                    body=test_body,
                    contentType="application/json",
                    accept="application/json"
                )
                self.stdout.write(self.style.SUCCESS('  Can invoke Bedrock models'))
                self.stdout.write(self.style.SUCCESS('  Bedrock access is working correctly!'))
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                error_message = e.response.get('Error', {}).get('Message', '')
                if error_code == 'UnrecognizedClientException':
                    self.stdout.write(self.style.ERROR(f'  Cannot invoke Bedrock models: {error_code}'))
                    self.stdout.write(self.style.ERROR(f'  Message: {error_message}'))
                    self.stdout.write(self.style.WARNING('\n  This error typically means:'))
                    self.stdout.write('    - IAM role credentials are invalid or expired')
                    self.stdout.write('    - IAM role does not have bedrock:InvokeModel permission')
                    self.stdout.write('    - Bedrock service is not enabled in this region')
                    self.stdout.write('    - The model is not available in this region')
                    self.stdout.write(self.style.WARNING('\n  Solutions:'))
                    self.stdout.write('    1. Verify IAM role "Bedrock-admin" has bedrock:InvokeModel permission')
                    self.stdout.write('    2. Check if Bedrock is enabled in AWS console for region: ' + aws_region)
                    self.stdout.write('    3. Some models may not be available in eu-north-1')
                    self.stdout.write('    4. Try using us-east-1 or us-west-2 if model is not available')
                    self.stdout.write('    5. Ensure the model is enabled in Bedrock console')
                    self.stdout.write('    6. Check IAM role trust policy allows the EC2 instance')
                elif error_code == 'AccessDeniedException':
                    self.stdout.write(self.style.ERROR(f'  Access denied: {error_message}'))
                    self.stdout.write(self.style.WARNING('  Ensure IAM role/user has bedrock:InvokeModel permission'))
                else:
                    self.stdout.write(self.style.ERROR(f'  Error invoking model: {error_code}'))
                    self.stdout.write(self.style.ERROR(f'  Message: {error_message}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  Error testing Bedrock: {str(e)}'))
        
        self.stdout.write('\n' + self.style.SUCCESS('Diagnostic complete!'))

