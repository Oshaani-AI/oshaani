"""
URL configuration for oshani project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.contrib.sitemaps.views import sitemap
from django.views.generic import TemplateView, RedirectView
from django.http import HttpResponse
from agents_app.sitemaps import StaticViewSitemap, PublicAgentSitemap
from blog_app.sitemaps import BlogPostSitemap, BlogCategorySitemap, BlogTagSitemap
from agents_app.views_chat import (
    chat_home, send_chat_message, get_chat_task_status, get_conversation, get_conversations_list, 
    upload_chat_file, submit_feedback, get_conversation_messages_paginated, download_conversation,
    get_conversation_tool_calls, delete_conversation
)
from agents_app.views import serve_media_file
from agents_app.views_health import health_check
from agents_app.views_intro import intro_page
import agents_app.views_linkedin
import agents_app.views_google
from agents_app import views_auth_api
from agents_app.views_dashboard import social_media_oauth_callback

# Sitemaps configuration
sitemaps = {
    'static': StaticViewSitemap,
    'public-agents': PublicAgentSitemap,
    'blog-posts': BlogPostSitemap,
    'blog-categories': BlogCategorySitemap,
    'blog-tags': BlogTagSitemap,
}


def robots_txt(request):
    """Serve robots.txt for SEO - optimized for search engine crawlers."""
    # Use request to build absolute sitemap URL (works for staging, production)
    scheme = request.scheme
    host = request.get_host()
    sitemap_url = f"{scheme}://{host}/sitemap.xml"

    content = f"""# robots.txt for {host}
# https://www.robotstxt.org/

User-agent: *
# Public pages - allow indexing
Allow: /
Allow: /intro/
Allow: /privacy/
Allow: /terms/
Allow: /license/
Allow: /sop/
Allow: /blog/
Allow: /blog/post/
Allow: /agent/
Allow: /dashboard/agents/public/
Allow: /media/blog/

# Private areas - disallow indexing
Disallow: /admin/
Disallow: /api/
Disallow: /accounts/
Disallow: /auth/
Disallow: /dashboard/
Disallow: /media/
Disallow: /static/admin/
Disallow: /oauth/

# Sitemap (required for SEO)
Sitemap: {sitemap_url}
"""
    return HttpResponse(content, content_type='text/plain')


urlpatterns = [
    # SEO: robots.txt and sitemap.xml
    path('robots.txt', robots_txt, name='robots_txt'),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    
    path('health/', health_check, name='health_check'),
    path('intro/', intro_page, name='intro'),
    path('', auth_views.LoginView.as_view(
        template_name='registration/login.html',
        redirect_authenticated_user=True,
    ), name='home'),
    # Legal pages
    path('privacy/', TemplateView.as_view(template_name='legal/privacy_policy.html'), name='privacy_policy'),
    path('terms/', TemplateView.as_view(template_name='legal/terms_conditions.html'), name='terms_conditions'),
    path('license/', TemplateView.as_view(template_name='legal/license.html'), name='license'),
    path('agent/<slug:slug>/', chat_home, name='agent_chat'),
    path('chat/', chat_home, name='chat_home'),
    path('sop/', RedirectView.as_view(url='https://oshaani.com/sop/', permanent=False), name='system_sop'),
    path('sop/download/', RedirectView.as_view(url='https://oshaani.com/sop/download/', permanent=False), name='download_sop_pdf'),
    path('api/chat/send/', send_chat_message, name='send_chat_message'),
    path('api/chat/task-status/<str:task_id>/', get_chat_task_status, name='get_chat_task_status'),
    path('api/chat/conversation/<str:conversation_id>/', get_conversation, name='get_conversation'),
    path('api/chat/conversation/<str:conversation_id>/messages/', get_conversation_messages_paginated, name='get_conversation_messages_paginated'),
    path('api/chat/conversation/<str:conversation_id>/tool-calls/', get_conversation_tool_calls, name='get_conversation_tool_calls'),
    path('api/chat/conversation/<str:conversation_id>/download/', download_conversation, name='download_conversation'),
    path('api/chat/conversation/<str:conversation_id>/delete/', delete_conversation, name='delete_conversation'),
    path('api/chat/conversations/', get_conversations_list, name='get_conversations_list'),
    path('api/chat/upload-file/', upload_chat_file, name='upload_chat_file'),
    path('api/chat/feedback/', submit_feedback, name='submit_feedback'),
    path('admin/', admin.site.urls),
    path('api/', include('agents_app.urls')),
    path('api/connectors/', include(('connectors.urls', 'connectors'), namespace='api_connectors')),
    # Blog URLs (public pages and API)
    path('blog/', include('blog_app.urls')),
    # More specific dashboard paths MUST come before the catch-all dashboard/ path
    path('dashboard/', include('agents_app.urls_dashboard')),
    path('accounts/login/', auth_views.LoginView.as_view(
        template_name='registration/login.html',
        redirect_authenticated_user=True,
    ), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('auth/login/', auth_views.LoginView.as_view(
        template_name='registration/login.html',
        redirect_authenticated_user=True,
    ), name='auth_login'),
    path('auth/', include('django.contrib.auth.urls')),
    # REST API Auth endpoints (for SPA)
    path('api/auth/login/', views_auth_api.api_login, name='api_login'),
    path('api/auth/logout/', views_auth_api.api_logout, name='api_logout'),
    path('api/auth/csrf-token/', views_auth_api.api_csrf_token, name='api_csrf_token'),
    path('api/auth/complete-onboarding-tour/', views_auth_api.complete_onboarding_tour, name='api_complete_onboarding_tour'),
    path('api/auth/linkedin/url/', views_auth_api.api_linkedin_oauth_url, name='api_linkedin_oauth_url'),
    path('api/auth/linkedin/callback/', views_auth_api.api_linkedin_oauth_callback, name='api_linkedin_oauth_callback'),
    # Also allow LinkedIn callback on traditional route for compatibility
    path('accounts/linkedin/callback-api/', views_auth_api.api_linkedin_oauth_callback, name='api_linkedin_oauth_callback_alt'),
    # LinkedIn OAuth URLs (for traditional form-based auth)
    path('accounts/linkedin/login/', agents_app.views_linkedin.linkedin_oauth_login, name='linkedin_oauth_login'),
    path('accounts/linkedin/callback/', agents_app.views_linkedin.linkedin_oauth_callback, name='linkedin_oauth_callback'),
    # Google OAuth URLs (for REST API)
    path('api/auth/google/url/', views_auth_api.api_google_oauth_url, name='api_google_oauth_url'),
    path('api/auth/google/callback/', views_auth_api.api_google_oauth_callback, name='api_google_oauth_callback'),
    # Google OAuth URLs (for traditional form-based auth)
    path('accounts/google/login/', agents_app.views_google.google_oauth_login, name='google_oauth_login'),
    path('accounts/google/callback/', agents_app.views_google.google_oauth_callback, name='google_oauth_callback'),
    # Social Media Publishing OAuth Callbacks (agent_id passed via state parameter, not URL)
    # Callback URLs to register with OAuth providers:
    # - LinkedIn: https://oshaani.com/oauth/social-media/linkedin/callback/
    # - Facebook: https://oshaani.com/oauth/social-media/facebook/callback/
    # - X (formerly Twitter): https://oshaani.com/oauth/social-media/twitter/callback/
    # - Google: https://oshaani.com/oauth/social-media/google/callback/
    # - Instagram: https://oshaani.com/oauth/social-media/instagram/callback/
    path('oauth/social-media/<str:platform>/callback/', social_media_oauth_callback, name='social_media_oauth_callback'),
    # Serve media files with authentication (works even when DEBUG=False)
    path('media/<path:file_path>', serve_media_file, name='serve_media_file'),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# Custom 404 handler (Django resolves this dotted path at module level)
handler404 = 'agents_app.views.handler404'
