"""Context processors for agents_app."""
from .models import SocialLink


def social_links(request):
    """Add active footer social links to template context."""
    links = list(SocialLink.objects.filter(is_active=True).order_by('order', 'name'))
    return {'social_links': links}
