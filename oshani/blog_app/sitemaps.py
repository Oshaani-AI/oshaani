"""Sitemap configuration for blog app."""
from django.contrib.sitemaps import Sitemap
from django.utils import timezone
from .models import BlogPost, BlogCategory, BlogTag


class BlogPostSitemap(Sitemap):
    """Sitemap for blog posts."""
    changefreq = 'weekly'
    protocol = 'https'
    
    def items(self):
        """Get all published blog posts."""
        return BlogPost.objects.filter(
            status='published',
            published_at__isnull=False,
            published_at__lte=timezone.now()
        ).order_by('-published_at')
    
    def lastmod(self, obj):
        """Last modification date."""
        return obj.updated_at
    
    def location(self, obj):
        """URL location."""
        return obj.get_absolute_url()
    
    def priority(self, obj):
        """Priority based on recency."""
        # Recent posts get higher priority
        days_old = (timezone.now() - obj.published_at).days
        if days_old < 7:
            return 0.9
        elif days_old < 30:
            return 0.8
        elif days_old < 90:
            return 0.7
        return 0.6


class BlogCategorySitemap(Sitemap):
    """Sitemap for blog categories."""
    changefreq = 'monthly'
    priority = 0.6
    protocol = 'https'
    
    def items(self):
        """Get all categories with published posts."""
        return BlogCategory.objects.filter(
            posts__status='published',
            posts__published_at__isnull=False,
            posts__published_at__lte=timezone.now()
        ).distinct().order_by('name')
    
    def location(self, obj):
        """URL location."""
        return obj.get_absolute_url()
    
    def lastmod(self, obj):
        """Last modification date of most recent post in category."""
        latest_post = obj.posts.filter(
            status='published',
            published_at__isnull=False,
            published_at__lte=timezone.now()
        ).order_by('-updated_at').first()
        return latest_post.updated_at if latest_post else None


class BlogTagSitemap(Sitemap):
    """Sitemap for blog tags."""
    changefreq = 'monthly'
    priority = 0.5
    protocol = 'https'
    
    def items(self):
        """Get all tags with published posts."""
        return BlogTag.objects.filter(
            posts__status='published',
            posts__published_at__isnull=False,
            posts__published_at__lte=timezone.now()
        ).distinct().order_by('name')
    
    def location(self, obj):
        """URL location."""
        return obj.get_absolute_url()
    
    def lastmod(self, obj):
        """Last modification date of most recent post with this tag."""
        latest_post = obj.posts.filter(
            status='published',
            published_at__isnull=False,
            published_at__lte=timezone.now()
        ).order_by('-updated_at').first()
        return latest_post.updated_at if latest_post else None
