"""Welcome email functionality for new users."""
import logging
from django.conf import settings
from .email_utils import send_email_with_fallback

logger = logging.getLogger(__name__)

SUPPORT_EMAIL = 'support@oshaani.com'


def send_welcome_email(user, is_new_user=True):
    """
    Send welcome email to user and support team.
    
    Args:
        user: User instance
        is_new_user: True if this is a new registration, False if first login
    """
    try:
        user_email = user.email
        if not user_email:
            logger.warning(f"Cannot send welcome email to user {user.username}: no email address")
            return False
        
        user_name = user.get_full_name() or user.first_name or user.username
        
        # Email subject
        subject = f"Welcome to Oshani AI Agents, {user_name}!"
        
        # Plain text email content
        plain_message = f"""
Hello {user_name},

Welcome to Oshani AI Agents! We're excited to have you on board.

Your account has been successfully {'created' if is_new_user else 'activated'}:
- Username: {user.username}
- Email: {user.email}

What's Next?
- Create your first AI agent
- Connect external services (JIRA, Confluence, GitLab, etc.)
- Start building intelligent workflows

Need Help?
- Visit our documentation: https://oshaani.com/dashboard/documentation/
- Contact support: {SUPPORT_EMAIL}
- Check out our guides and tutorials

We're here to help you succeed!

Best regards,
The Oshani Team
"""
        
        # HTML email content
        html_message = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .header {{
            background-color: #4a90e2;
            color: white;
            padding: 20px;
            text-align: center;
            border-radius: 5px 5px 0 0;
        }}
        .content {{
            background-color: #f9f9f9;
            padding: 30px;
            border-radius: 0 0 5px 5px;
        }}
        .button {{
            display: inline-block;
            padding: 12px 30px;
            background-color: #4a90e2;
            color: white;
            text-decoration: none;
            border-radius: 5px;
            margin: 20px 0;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #ddd;
            font-size: 12px;
            color: #666;
        }}
        .info-box {{
            background-color: #e8f4f8;
            padding: 15px;
            border-left: 4px solid #4a90e2;
            margin: 20px 0;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Welcome to Oshani AI Agents!</h1>
    </div>
    <div class="content">
        <p>Hello {user_name},</p>
        
        <p>Welcome to Oshani AI Agents! We're excited to have you on board.</p>
        
        <div class="info-box">
            <strong>Your account has been successfully {'created' if is_new_user else 'activated'}:</strong><br>
            • Username: <strong>{user.username}</strong><br>
            • Email: <strong>{user.email}</strong>
        </div>
        
        <h2>What's Next?</h2>
        <ul>
            <li>Create your first AI agent</li>
            <li>Connect external services (JIRA, Confluence, GitLab, GitHub, etc.)</li>
            <li>Start building intelligent workflows</li>
            <li>Train agents with your data</li>
        </ul>
        
        <div style="text-align: center;">
            <a href="https://oshaani.com/dashboard/" class="button">Get Started</a>
        </div>
        
        <h2>Need Help?</h2>
        <ul>
            <li>Visit our <a href="https://oshaani.com/dashboard/documentation/">documentation</a></li>
            <li>Contact support: <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a></li>
            <li>Check out our guides and tutorials</li>
        </ul>
        
        <p>We're here to help you succeed!</p>
        
        <p>Best regards,<br>
        The Oshani Team</p>
    </div>
    <div class="footer">
        <p>This email was sent to {user.email}. If you have any questions, please contact us at {SUPPORT_EMAIL}.</p>
    </div>
</body>
</html>
"""
        
        # Send email to user
        user_sent = send_email_with_fallback(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user_email],
            html_message=html_message,
            fail_silently=True
        )
        
        if user_sent:
            logger.info(f"Welcome email sent to user {user.username} ({user_email})")
        else:
            logger.warning(f"Failed to send welcome email to user {user.username} ({user_email})")
        
        # Send notification email to support team
        support_subject = f"New User {'Registration' if is_new_user else 'Login'}: {user.username}"
        support_message = f"""
A new user has {'registered' if is_new_user else 'logged in for the first time'}:

User Details:
- Username: {user.username}
- Email: {user.email}
- Full Name: {user.get_full_name() or 'N/A'}
- First Name: {user.first_name or 'N/A'}
- Last Name: {user.last_name or 'N/A'}
- Date Joined: {user.date_joined.strftime('%Y-%m-%d %H:%M:%S') if user.date_joined else 'N/A'}

Action: {'New Registration' if is_new_user else 'First Login'}
"""
        
        support_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{
            font-family: Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .header {{
            background-color: #28a745;
            color: white;
            padding: 15px;
            text-align: center;
            border-radius: 5px 5px 0 0;
        }}
        .content {{
            background-color: #f9f9f9;
            padding: 20px;
            border-radius: 0 0 5px 5px;
        }}
        .info-row {{
            padding: 8px 0;
            border-bottom: 1px solid #eee;
        }}
        .label {{
            font-weight: bold;
            color: #555;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h2>New User {'Registration' if is_new_user else 'First Login'}</h2>
    </div>
    <div class="content">
        <div class="info-row">
            <span class="label">Username:</span> {user.username}
        </div>
        <div class="info-row">
            <span class="label">Email:</span> {user.email}
        </div>
        <div class="info-row">
            <span class="label">Full Name:</span> {user.get_full_name() or 'N/A'}
        </div>
        <div class="info-row">
            <span class="label">First Name:</span> {user.first_name or 'N/A'}
        </div>
        <div class="info-row">
            <span class="label">Last Name:</span> {user.last_name or 'N/A'}
        </div>
        <div class="info-row">
            <span class="label">Date Joined:</span> {user.date_joined.strftime('%Y-%m-%d %H:%M:%S') if user.date_joined else 'N/A'}
        </div>
        <div class="info-row">
            <span class="label">Action:</span> {'New Registration' if is_new_user else 'First Login'}
        </div>
    </div>
</body>
</html>
"""
        
        support_sent = send_email_with_fallback(
            subject=support_subject,
            message=support_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[SUPPORT_EMAIL],
            html_message=support_html,
            fail_silently=True
        )
        
        if support_sent:
            logger.info(f"Support notification email sent for user {user.username}")
        else:
            logger.warning(f"Failed to send support notification email for user {user.username}")
        
        return user_sent and support_sent
        
    except Exception as e:
        logger.error(f"Error sending welcome email to user {user.username}: {str(e)}", exc_info=True)
        return False













