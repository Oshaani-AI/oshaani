"""Sitemaps for SEO."""
from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Agent


class StaticViewSitemap(Sitemap):
    """Sitemap for static pages."""
    protocol = 'https'

    def items(self):
        return [
            'home',
            'intro',
            'privacy_policy',
            'terms_conditions',
            'system_sop',
            'license',
            'public_agents_list',
            'blog:list',
        ]

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        # Higher priority for main pages
        priorities = {
            'home': 1.0,
            'intro': 1.0,
            'public_agents_list': 0.9,
            'blog:list': 0.8,
            'license': 0.5,
            'privacy_policy': 0.3,
            'terms_conditions': 0.3,
            'system_sop': 0.5,
        }
        return priorities.get(item, 0.5)

    def changefreq(self, item):
        # Legal pages change less frequently
        if item in ['privacy_policy', 'terms_conditions']:
            return 'monthly'
        if item in ['home', 'intro', 'public_agents_list']:
            return 'daily'
        return 'weekly'


class PublicAgentSitemap(Sitemap):
    """Sitemap for individual public agent chat pages."""
    protocol = 'https'

    def items(self):
        """Return all published agents with active public shares."""
        return Agent.objects.filter(
            public_shares__is_active=True,
            status='published',
            slug__isnull=False
        ).exclude(slug='').distinct().order_by('-updated_at')[:500]  # Limit for sitemap performance

    def location(self, agent):
        """Get the URL for the agent chat page."""
        if not agent.slug:
            return None
        return reverse('agent_chat', kwargs={'slug': agent.slug})

    def lastmod(self, agent):
        """Last modification date."""
        return agent.updated_at

    def priority(self, agent):
        """Priority based on popularity (access count)."""
        try:
            share = agent.public_shares.filter(is_active=True).order_by('-access_count').first()
            if share:
                if share.access_count > 100:
                    return 0.9
                elif share.access_count > 50:
                    return 0.8
                elif share.access_count > 10:
                    return 0.7
            return 0.6
        except Exception:
            return 0.6

    def changefreq(self, agent):
        """Change frequency based on activity."""
        try:
            share = agent.public_shares.filter(is_active=True).order_by('-access_count').first()
            if share and share.access_count > 50:
                return 'daily'
            return 'weekly'
        except Exception:
            return 'weekly'
