"""URL configuration for blog app."""
from django.shortcuts import redirect
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    BlogPostViewSet, BlogCategoryViewSet, BlogTagViewSet, BlogCommentViewSet,
)

OSHAANI_BLOG_URL = 'https://oshaani.com/blog/'


def redirect_to_oshaani_blog(request, subpath=''):
    target = OSHAANI_BLOG_URL if not subpath else f'{OSHAANI_BLOG_URL}{subpath}'
    query = request.META.get('QUERY_STRING')
    if query:
        target = f'{target}?{query}'
    return redirect(target)

app_name = 'blog'

# API Router
router = DefaultRouter()
router.register(r'posts', BlogPostViewSet, basename='post')
router.register(r'categories', BlogCategoryViewSet, basename='category')
router.register(r'tags', BlogTagViewSet, basename='tag')
router.register(r'comments', BlogCommentViewSet, basename='comment')

urlpatterns = [
    # Public pages → production blog
    path('', lambda request: redirect_to_oshaani_blog(request), name='list'),
    path('post/<slug:slug>/', lambda request, slug: redirect_to_oshaani_blog(request, f'post/{slug}/'), name='post_detail'),
    path('category/<slug:slug>/', lambda request, slug: redirect_to_oshaani_blog(request, f'category/{slug}/'), name='category_detail'),
    path('tag/<slug:slug>/', lambda request, slug: redirect_to_oshaani_blog(request, f'tag/{slug}/'), name='tag_detail'),

    # API endpoints (local admin / integrations)
    path('api/', include(router.urls)),
]
