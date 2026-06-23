"""
Custom middleware for the oshani project.
"""
from django.http import HttpResponse
from django.conf import settings


class DisallowedHostMiddleware:
    """
    Middleware to handle requests with invalid Host headers gracefully.
    
    Instead of raising DisallowedHost exception (which gets logged as an error),
    this middleware returns a 405 Method Not Allowed response early in the request cycle.
    
    This prevents log spam from malicious requests targeting the server's IP
    with invalid Host headers.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.allowed_hosts = set(settings.ALLOWED_HOSTS)
        # Handle wildcard
        self.allow_all = '*' in self.allowed_hosts
    
    def __call__(self, request):
        if not self.allow_all:
            host = request.get_host().split(':')[0]  # Remove port if present
            
            # Check if host is in allowed hosts
            # Also check for subdomain wildcard patterns like '.example.com'
            host_valid = False
            
            if host in self.allowed_hosts:
                host_valid = True
            else:
                # Check for wildcard subdomain patterns
                for allowed in self.allowed_hosts:
                    if allowed.startswith('.') and host.endswith(allowed):
                        host_valid = True
                        break
                    # Handle case where allowed host is domain and request is subdomain
                    if allowed.startswith('.') and host == allowed[1:]:
                        host_valid = True
                        break
            
            if not host_valid:
                # Return 405 Method Not Allowed for invalid hosts
                # This prevents the DisallowedHost exception from being logged
                return HttpResponse(
                    "Method Not Allowed",
                    status=405,
                    content_type="text/plain"
                )
        
        return self.get_response(request)
