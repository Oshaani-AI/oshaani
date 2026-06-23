"""Views for contact form and demo booking."""
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.conf import settings
from django.contrib.auth.models import User
from .email_utils import send_email_with_fallback
import logging
import json

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["POST"])
def contact_form_submit(request):
    """Handle contact form and demo booking submissions."""
    try:
        data = json.loads(request.body) if request.content_type == 'application/json' else request.POST
        
        form_type = data.get('form_type', 'contact')
        name = data.get('name', '').strip()
        email = data.get('email', '').strip()
        company = data.get('company', '').strip()
        phone = data.get('phone', '').strip()
        message = data.get('message', '').strip()
        demo_date = data.get('demoDate', '').strip()
        demo_time = data.get('demoTime', '').strip()
        
        # Validation
        if not name or not email or not message:
            return JsonResponse({
                'success': False,
                'error': 'Name, email, and message are required fields.'
            }, status=400)
        
        # Prepare email content
        if form_type == 'demo':
            subject = f'Demo Request from {name}'
            email_body = f"""
New Demo Request from Oshaani Website

Name: {name}
Email: {email}
Company: {company or 'Not provided'}
Phone: {phone or 'Not provided'}
Preferred Date: {demo_date or 'Not specified'}
Preferred Time: {demo_time or 'Not specified'}

Message:
{message}

---
This email was sent from the Oshaani website contact form.
"""
            html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #00d4ff 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9f9f9; padding: 20px; border-radius: 0 0 8px 8px; }}
        .field {{ margin-bottom: 15px; }}
        .label {{ font-weight: bold; color: #00d4ff; }}
        .message-box {{ background: white; padding: 15px; border-left: 4px solid #00d4ff; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>New Demo Request</h2>
        </div>
        <div class="content">
            <div class="field">
                <span class="label">Name:</span> {name}
            </div>
            <div class="field">
                <span class="label">Email:</span> <a href="mailto:{email}">{email}</a>
            </div>
            <div class="field">
                <span class="label">Company:</span> {company or 'Not provided'}
            </div>
            <div class="field">
                <span class="label">Phone:</span> {phone or 'Not provided'}
            </div>
            <div class="field">
                <span class="label">Preferred Date:</span> {demo_date or 'Not specified'}
            </div>
            <div class="field">
                <span class="label">Preferred Time:</span> {demo_time or 'Not specified'}
            </div>
            <div class="message-box">
                <div class="label">Message:</div>
                <div>{message.replace(chr(10), '<br>')}</div>
            </div>
            <p style="margin-top: 20px; font-size: 12px; color: #999;">
                This email was sent from the Oshaani website contact form.
            </p>
        </div>
    </div>
</body>
</html>
"""
        else:
            subject = f'Contact Form Submission from {name}'
            email_body = f"""
New Contact Form Submission from Oshaani Website

Name: {name}
Email: {email}
Company: {company or 'Not provided'}
Phone: {phone or 'Not provided'}

Message:
{message}

---
This email was sent from the Oshaani website contact form.
"""
            html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #00d4ff 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9f9f9; padding: 20px; border-radius: 0 0 8px 8px; }}
        .field {{ margin-bottom: 15px; }}
        .label {{ font-weight: bold; color: #00d4ff; }}
        .message-box {{ background: white; padding: 15px; border-left: 4px solid #00d4ff; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>New Contact Form Submission</h2>
        </div>
        <div class="content">
            <div class="field">
                <span class="label">Name:</span> {name}
            </div>
            <div class="field">
                <span class="label">Email:</span> <a href="mailto:{email}">{email}</a>
            </div>
            <div class="field">
                <span class="label">Company:</span> {company or 'Not provided'}
            </div>
            <div class="field">
                <span class="label">Phone:</span> {phone or 'Not provided'}
            </div>
            <div class="message-box">
                <div class="label">Message:</div>
                <div>{message.replace(chr(10), '<br>')}</div>
            </div>
            <p style="margin-top: 20px; font-size: 12px; color: #999;">
                This email was sent from the Oshaani website contact form.
            </p>
        </div>
    </div>
</body>
</html>
"""
        
        # Get recipient emails - send to all users with staff status
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'support@oshaani.com')
        
        # Get all staff user emails
        staff_users = User.objects.filter(is_staff=True, is_active=True)
        staff_emails = [user.email for user in staff_users if user.email]
        
        # Fallback to Django ADMINS if no staff users found
        if not staff_emails:
            if hasattr(settings, 'ADMINS') and settings.ADMINS:
                staff_emails = [email for name, email in settings.ADMINS]
        
        # Final fallback to CONTACT_FORM_EMAIL if no staff users or ADMINS configured
        if not staff_emails:
            contact_email = getattr(settings, 'CONTACT_FORM_EMAIL', 'support@oshaani.com')
            staff_emails = [contact_email]
            logger.warning(f"No staff users found. Using fallback email: {contact_email}")
        
        logger.info(f"Sending contact form email to {len(staff_emails)} staff user(s): {', '.join(staff_emails)}")
        
        # Send email
        try:
            success = send_email_with_fallback(
                subject=subject,
                message=email_body,
                from_email=from_email,
                recipient_list=staff_emails,
                html_message=html_body,
                fail_silently=False
            )
            
            if success:
                logger.info(f"Contact form submitted successfully: {form_type} from {name} ({email})")
                return JsonResponse({
                    'success': True,
                    'message': 'Thank you! Your message has been sent successfully.'
                })
            else:
                logger.error(f"Failed to send contact form email: {form_type} from {name} ({email})")
                return JsonResponse({
                    'success': False,
                    'error': 'Failed to send email. Please try again later.'
                }, status=500)
        except Exception as e:
            logger.error(f"Error sending contact form email: {str(e)}", exc_info=True)
            return JsonResponse({
                'success': False,
                'error': 'An error occurred while sending your message. Please try again later.'
            }, status=500)
            
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data.'
        }, status=400)
    except Exception as e:
        logger.error(f"Error processing contact form: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'An error occurred. Please try again later.'
        }, status=500)

