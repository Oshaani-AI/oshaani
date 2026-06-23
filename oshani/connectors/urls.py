"""URL configuration for connectors app."""
from django.urls import path
from . import views
from . import views_dashboard

# Note: app_name removed to allow multiple namespaces in main urls.py

urlpatterns = [
    # Dashboard views (HTML pages)
    path('', views_dashboard.connectors_list, name='list_connectors'),
    path('create/', views_dashboard.connectors_list, name='create_connector'),  # Redirects to list page (modal handles creation)
    
    # AJAX endpoints for dashboard operations
    path('ajax/create/', views_dashboard.connector_create_ajax, name='create_connector_ajax'),
    path('ajax/<int:connector_id>/get/', views_dashboard.connector_get_ajax, name='get_connector_ajax'),
    path('ajax/<int:connector_id>/update/', views_dashboard.connector_update_ajax, name='update_connector_ajax'),
    path('ajax/<int:connector_id>/delete/', views_dashboard.connector_delete_ajax, name='delete_connector_ajax'),
    path('ajax/<int:connector_id>/connect/', views_dashboard.connector_connect_ajax, name='connect_connector_ajax'),
    path('ajax/<int:connector_id>/sync/', views_dashboard.connector_sync_ajax, name='sync_connector_ajax'),
    
    # OAuth flow (must come before generic connector_id paths)
    path('<int:connector_id>/oauth/initiate/', views.initiate_oauth, name='initiate_oauth'),
    path('<int:connector_id>/oauth/callback/', views.oauth_callback, name='oauth_callback'),
    
    # Data operations
    path('<int:connector_id>/data/', views.get_connector_data, name='get_connector_data'),
    path('<int:connector_id>/sync/', views.sync_data, name='sync_data'),
    path('<int:connector_id>/sync/<int:sync_id>/status/', views.get_sync_status, name='get_sync_status'),
]

