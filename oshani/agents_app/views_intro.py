"""Views for intro page."""
from django.shortcuts import render
from django.views.decorators.http import require_http_methods


@require_http_methods(["GET"])
def intro_page(request):
    """Render the intro/landing page (optional marketing page at /intro/)."""
    return render(request, 'intro.html')
