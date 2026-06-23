"""
Django management command to create a secure S3 bucket for Bedrock video generation output.

This command creates an S3 bucket with:
- Server-side encryption (SSE-S3)
- Versioning enabled
- Lifecycle policy to delete old files after 7 days
- Block public access enabled
- Bucket policy allowing Bedrock service access
- Optional: Enable bucket logging
"""
import json
import logging
from botocore.exceptions import ClientError
from django.core.management.base import BaseCommand
from agents_app.aws_utils import get_aws_region, create_boto3_client

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Create a secure S3 bucket for Bedrock video generation output'

    def add_arguments(self, parser):
        parser.add_argument(
            '--bucket-name',
            type=str,
            help='Custom bucket name (must be globally unique). If not provided, will generate based on account ID.',
        )
        parser.add_argument(
            '--region',
            type=str,
            help='AWS region for the bucket (default: uses AWS_REGION setting or auto-detects)',
        )
        parser.add_argument(
            '--retention-days',
            type=int,
            default=7,
            help='Number of days to retain video files before deletion (default: 7)',
        )
        parser.add_argument(
            '--enable-logging',
            action='store_true',
            help='Enable S3 bucket access logging',
        )
        parser.add_argument(
            '--skip-policy',
            action='store_true',
            help='Skip creating bucket policy (useful if you want to set it manually)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually creating the bucket',
        )

    def handle(self, *args, **options):
        bucket_name = options.get('bucket_name')
        region = options.get('region') or get_aws_region()
        retention_days = options.get('retention_days', 7)
        enable_logging = options.get('enable_logging', False)
        skip_policy = options.get('skip_policy', False)
        dry_run = options.get('dry_run', False)

        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write(self.style.SUCCESS('Creating Secure S3 Bucket for Bedrock Video Output'))
        self.stdout.write(self.style.SUCCESS('=' * 70))

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))

        try:
            # Create S3 client
            s3_client = create_boto3_client('s3', region_name=region)
            sts_client = create_boto3_client('sts', region_name=region)

            # Get account ID
            try:
                identity = sts_client.get_caller_identity()
                account_id = identity.get('Account')
                arn = identity.get('Arn', 'Unknown')
                self.stdout.write(f"Account ID: {account_id}")
                self.stdout.write(f"AWS Identity: {arn}")
            except Exception as e:
                error_msg = str(e)
                self.stdout.write(self.style.ERROR(f"Failed to get account ID: {error_msg}"))
                self.stdout.write("")
                self.stdout.write("Troubleshooting:")
                self.stdout.write("1. Ensure AWS credentials are configured (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)")
                self.stdout.write("2. If using IAM role, ensure the instance has an IAM role attached")
                self.stdout.write("3. Verify the IAM role has S3 and STS permissions")
                self.stdout.write("")
                self.stdout.write("You can also manually create the bucket and set BEDROCK_VIDEO_OUTPUT_BUCKET:")
                self.stdout.write("  aws s3 mb s3://bedrock-video-output-<account-id> --region <region>")
                return

            # Generate bucket name if not provided
            if not bucket_name:
                bucket_name = f"bedrock-video-output-{account_id}"
                self.stdout.write(f"Generated bucket name: {bucket_name}")

            self.stdout.write(f"Region: {region}")
            self.stdout.write(f"Bucket name: {bucket_name}")
            self.stdout.write(f"Retention: {retention_days} days")
            self.stdout.write("")

            if dry_run:
                self.stdout.write(self.style.WARNING("Would create bucket with the following configuration:"))
                self.stdout.write(f"  - Name: {bucket_name}")
                self.stdout.write(f"  - Region: {region}")
                self.stdout.write(f"  - Encryption: SSE-S3")
                self.stdout.write(f"  - Versioning: Enabled")
                self.stdout.write(f"  - Public access: Blocked")
                self.stdout.write(f"  - Lifecycle: Delete after {retention_days} days")
                return

            # Check if bucket already exists
            try:
                s3_client.head_bucket(Bucket=bucket_name)
                self.stdout.write(self.style.WARNING(f"Bucket '{bucket_name}' already exists"))
                
                # Ask if we should update it
                self.stdout.write("Updating bucket configuration...")
                self._update_bucket_configuration(
                    s3_client, bucket_name, region, retention_days, enable_logging, skip_policy, account_id
                )
                return
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                if error_code != '404':
                    raise

            # Create bucket
            self.stdout.write("Creating bucket...")
            try:
                # Note: Bucket names must be globally unique and DNS-compliant
                # For us-east-1, LocationConstraint is not needed
                if region == 'us-east-1':
                    s3_client.create_bucket(Bucket=bucket_name)
                else:
                    s3_client.create_bucket(
                        Bucket=bucket_name,
                        CreateBucketConfiguration={'LocationConstraint': region}
                    )
                self.stdout.write(self.style.SUCCESS(f"✓ Bucket '{bucket_name}' created successfully"))
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                if error_code == 'BucketAlreadyExists':
                    self.stdout.write(self.style.WARNING(f"Bucket '{bucket_name}' already exists (owned by another account)"))
                    return
                elif error_code == 'BucketAlreadyOwnedByYou':
                    self.stdout.write(self.style.WARNING(f"Bucket '{bucket_name}' already exists"))
                    self._update_bucket_configuration(
                        s3_client, bucket_name, region, retention_days, enable_logging, skip_policy, account_id
                    )
                    return
                else:
                    raise

            # Configure bucket settings
            self._configure_bucket(
                s3_client, bucket_name, region, retention_days, enable_logging, skip_policy, account_id
            )

            # Output environment variable
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS('=' * 70))
            self.stdout.write(self.style.SUCCESS('Bucket created successfully!'))
            self.stdout.write(self.style.SUCCESS('=' * 70))
            self.stdout.write("")
            self.stdout.write("Add this to your environment variables or .env file:")
            self.stdout.write(self.style.WARNING(f"BEDROCK_VIDEO_OUTPUT_BUCKET={bucket_name}"))
            self.stdout.write("")
            self.stdout.write("Or update settings.py:")
            self.stdout.write(self.style.WARNING(f"BEDROCK_VIDEO_OUTPUT_BUCKET = '{bucket_name}'"))
            self.stdout.write("")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {str(e)}"))
            logger.error(f"Failed to create bucket: {str(e)}", exc_info=True)
            raise

    def _configure_bucket(self, s3_client, bucket_name, region, retention_days, enable_logging, skip_policy, account_id):
        """Configure bucket with security settings."""
        
        # 1. Enable encryption (SSE-S3)
        self.stdout.write("Configuring server-side encryption...")
        try:
            s3_client.put_bucket_encryption(
                Bucket=bucket_name,
                ServerSideEncryptionConfiguration={
                    'Rules': [
                        {
                            'ApplyServerSideEncryptionByDefault': {
                                'SSEAlgorithm': 'AES256'
                            }
                        }
                    ]
                }
            )
            self.stdout.write(self.style.SUCCESS("✓ Encryption enabled (SSE-S3)"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Warning: Failed to enable encryption: {str(e)}"))

        # 2. Block public access
        self.stdout.write("Blocking public access...")
        try:
            s3_client.put_public_access_block(
                Bucket=bucket_name,
                PublicAccessBlockConfiguration={
                    'BlockPublicAcls': True,
                    'IgnorePublicAcls': True,
                    'BlockPublicPolicy': True,
                    'RestrictPublicBuckets': True
                }
            )
            self.stdout.write(self.style.SUCCESS("✓ Public access blocked"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Warning: Failed to block public access: {str(e)}"))

        # 3. Enable versioning
        self.stdout.write("Enabling versioning...")
        try:
            s3_client.put_bucket_versioning(
                Bucket=bucket_name,
                VersioningConfiguration={'Status': 'Enabled'}
            )
            self.stdout.write(self.style.SUCCESS("✓ Versioning enabled"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Warning: Failed to enable versioning: {str(e)}"))

        # 4. Set lifecycle policy to delete old files
        self.stdout.write(f"Setting lifecycle policy (delete after {retention_days} days)...")
        try:
            lifecycle_config = {
                'Rules': [
                    {
                        'ID': 'DeleteOldVideos',  # AWS API expects uppercase 'ID'
                        'Status': 'Enabled',
                        'Prefix': 'videos/',
                        'Expiration': {
                            'Days': retention_days
                        },
                        'NoncurrentVersionExpiration': {
                            'NoncurrentDays': retention_days
                        }
                    }
                ]
            }
            s3_client.put_bucket_lifecycle_configuration(
                Bucket=bucket_name,
                LifecycleConfiguration=lifecycle_config
            )
            self.stdout.write(self.style.SUCCESS(f"✓ Lifecycle policy set (delete after {retention_days} days)"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Warning: Failed to set lifecycle policy: {str(e)}"))

        # 5. Set bucket policy for Bedrock access
        if not skip_policy:
            self.stdout.write("Setting bucket policy for Bedrock access...")
            try:
                bucket_policy = {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "AllowBedrockServiceAccess",
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "bedrock.amazonaws.com"
                            },
                            "Action": [
                                "s3:PutObject",
                                "s3:GetObject"
                            ],
                            "Resource": f"arn:aws:s3:::{bucket_name}/*",
                            "Condition": {
                                "StringEquals": {
                                    "aws:SourceAccount": account_id
                                }
                            }
                        },
                        {
                            "Sid": "AllowBedrockServiceList",
                            "Effect": "Allow",
                            "Principal": {
                                "Service": "bedrock.amazonaws.com"
                            },
                            "Action": [
                                "s3:ListBucket"
                            ],
                            "Resource": f"arn:aws:s3:::{bucket_name}",
                            "Condition": {
                                "StringEquals": {
                                    "aws:SourceAccount": account_id
                                }
                            }
                        },
                        {
                            "Sid": "AllowAccountAccess",
                            "Effect": "Allow",
                            "Principal": {
                                "AWS": f"arn:aws:iam::{account_id}:root"
                            },
                            "Action": [
                                "s3:GetObject",
                                "s3:PutObject",
                                "s3:DeleteObject",
                                "s3:ListBucket"
                            ],
                            "Resource": [
                                f"arn:aws:s3:::{bucket_name}",
                                f"arn:aws:s3:::{bucket_name}/*"
                            ]
                        }
                    ]
                }
                s3_client.put_bucket_policy(
                    Bucket=bucket_name,
                    Policy=json.dumps(bucket_policy)
                )
                self.stdout.write(self.style.SUCCESS("✓ Bucket policy set (Bedrock + Account access)"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Warning: Failed to set bucket policy: {str(e)}"))

        # 6. Enable logging (optional)
        if enable_logging:
            self.stdout.write("Enabling bucket logging...")
            try:
                log_bucket_name = f"{bucket_name}-logs"
                # Try to create log bucket if it doesn't exist
                try:
                    if region == 'us-east-1':
                        s3_client.create_bucket(Bucket=log_bucket_name)
                    else:
                        s3_client.create_bucket(
                            Bucket=log_bucket_name,
                            CreateBucketConfiguration={'LocationConstraint': region}
                        )
                    s3_client.put_bucket_encryption(
                        Bucket=log_bucket_name,
                        ServerSideEncryptionConfiguration={
                            'Rules': [{'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]
                        }
                    )
                except ClientError as e:
                    if e.response.get('Error', {}).get('Code') not in ['BucketAlreadyExists', 'BucketAlreadyOwnedByYou']:
                        raise

                s3_client.put_bucket_logging(
                    Bucket=bucket_name,
                    BucketLoggingStatus={
                        'LoggingEnabled': {
                            'TargetBucket': log_bucket_name,
                            'TargetPrefix': 'access-logs/'
                        }
                    }
                )
                self.stdout.write(self.style.SUCCESS(f"✓ Logging enabled (logs to {log_bucket_name})"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Warning: Failed to enable logging: {str(e)}"))

    def _update_bucket_configuration(self, s3_client, bucket_name, region, retention_days, enable_logging, skip_policy, account_id):
        """Update existing bucket configuration."""
        self.stdout.write("Updating bucket configuration...")
        self._configure_bucket(
            s3_client, bucket_name, region, retention_days, enable_logging, skip_policy, account_id
        )
        self.stdout.write(self.style.SUCCESS("✓ Bucket configuration updated"))
