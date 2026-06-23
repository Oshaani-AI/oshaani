"""URL routing for SPA - catch-all route to serve frontend."""
from django.urls import path
from django.views.generic import TemplateView

# Serve SPA index.html for all non-API routes
# This allows client-side routing to work
urlpatterns = [
    # Catch-all: serve index.html for SPA routing
    # The frontend will handle client-side routing
    path('', TemplateView.as_view(template_name='frontend/index.html'), name='spa_index'),
]


























