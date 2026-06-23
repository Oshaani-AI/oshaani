"""Health check views for system monitoring."""
import subprocess
import socket
from django.http import JsonResponse
from django.db import connection
from django.conf import settings
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)


def health_check(request):
    """
    Comprehensive health check endpoint that verifies all required systems.
    
    Returns JSON response with status of:
    - MySQL database
    - Redis
    - Celery
    - Daphne (ASGI server)
    - Nginx
    - Qdrant (if configured)
    """
    health_status = {
        'status': 'healthy',
        'timestamp': None,
        'services': {}
    }
    
    from django.utils import timezone
    health_status['timestamp'] = timezone.now().isoformat()
    
    overall_healthy = True
    
    # Check MySQL Database
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        health_status['services']['mysql'] = {
            'status': 'healthy',
            'message': 'Database connection successful'
        }
    except Exception as e:
        logger.error(f"MySQL health check failed: {str(e)}")
        health_status['services']['mysql'] = {
            'status': 'unhealthy',
            'message': f'Database connection failed: {str(e)}'
        }
        overall_healthy = False
    
    # Check Redis
    try:
        cache.set('health_check', 'ok', 10)
        result = cache.get('health_check')
        if result == 'ok':
            health_status['services']['redis'] = {
                'status': 'healthy',
                'message': 'Redis connection successful'
            }
        else:
            raise Exception("Redis get/set test failed")
    except Exception as e:
        logger.error(f"Redis health check failed: {str(e)}")
        health_status['services']['redis'] = {
            'status': 'unhealthy',
            'message': f'Redis connection failed: {str(e)}'
        }
        overall_healthy = False
    
    # Check Celery
    try:
        from oshani.celery import app as celery_app
        # Try to inspect active workers
        inspect = celery_app.control.inspect(timeout=2)
        active_workers = inspect.active()
        
        if active_workers:
            worker_count = len(active_workers)
            health_status['services']['celery'] = {
                'status': 'healthy',
                'message': f'Celery is running with {worker_count} active worker(s)',
                'workers': worker_count
            }
        else:
            # Workers might be idle, check if we can connect to broker
            try:
                # Try to ping workers to check broker connection
                ping_result = inspect.ping(timeout=2)
                if ping_result:
                    health_status['services']['celery'] = {
                        'status': 'healthy',
                        'message': 'Celery broker is accessible but no active workers found',
                        'workers': 0
                    }
                else:
                    raise Exception("Celery broker connection failed: no response from ping")
            except Exception:
                # Fallback: check Redis broker connection directly
                try:
                    import redis
                    broker_url = getattr(settings, 'CELERY_BROKER_URL', 'redis://localhost:6379/0')
                    r = redis.from_url(broker_url, socket_connect_timeout=2)
                    r.ping()
                    health_status['services']['celery'] = {
                        'status': 'degraded',
                        'message': 'Celery broker is accessible but workers may not be running',
                        'workers': 0
                    }
                except Exception as broker_error:
                    raise Exception(f"Celery broker connection failed: {str(broker_error)}")
    except Exception as e:
        logger.error(f"Celery health check failed: {str(e)}")
        health_status['services']['celery'] = {
            'status': 'unhealthy',
            'message': f'Celery check failed: {str(e)}'
        }
        overall_healthy = False
    
    # Check Daphne (ASGI server) - check if port 8000 is listening
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', 8000))
        sock.close()
        
        if result == 0:
            health_status['services']['daphne'] = {
                'status': 'healthy',
                'message': 'Daphne ASGI server is listening on port 8000'
            }
        else:
            # Also check via systemd service status
            try:
                result = subprocess.run(
                    ['systemctl', 'is-active', '--quiet', 'daphne.service'],
                    timeout=2,
                    capture_output=True
                )
                if result.returncode == 0:
                    health_status['services']['daphne'] = {
                        'status': 'healthy',
                        'message': 'Daphne service is active (port check failed but service is running)'
                    }
                else:
                    raise Exception("Daphne service is not active")
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
                raise Exception(f"Daphne is not listening on port 8000: {str(e)}")
    except Exception as e:
        logger.error(f"Daphne health check failed: {str(e)}")
        health_status['services']['daphne'] = {
            'status': 'unhealthy',
            'message': f'Daphne check failed: {str(e)}'
        }
        overall_healthy = False
    
    # Check Nginx
    try:
        # Check if nginx is listening on port 80 or 443
        sock80 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock80.settimeout(2)
        result80 = sock80.connect_ex(('127.0.0.1', 80))
        sock80.close()
        
        sock443 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock443.settimeout(2)
        result443 = sock443.connect_ex(('127.0.0.1', 443))
        sock443.close()
        
        if result80 == 0 or result443 == 0:
            health_status['services']['nginx'] = {
                'status': 'healthy',
                'message': 'Nginx is listening on port 80 or 443'
            }
        else:
            # Check via systemd
            try:
                result = subprocess.run(
                    ['systemctl', 'is-active', '--quiet', 'nginx'],
                    timeout=2,
                    capture_output=True
                )
                if result.returncode == 0:
                    health_status['services']['nginx'] = {
                        'status': 'healthy',
                        'message': 'Nginx service is active (port check failed but service is running)'
                    }
                else:
                    raise Exception("Nginx service is not active")
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
                raise Exception(f"Nginx is not listening on expected ports: {str(e)}")
    except Exception as e:
        logger.error(f"Nginx health check failed: {str(e)}")
        health_status['services']['nginx'] = {
            'status': 'unhealthy',
            'message': f'Nginx check failed: {str(e)}'
        }
        overall_healthy = False
    
    # Check Qdrant (if configured)
    try:
        qdrant_url = getattr(settings, 'QDRANT_URL', None)
        if qdrant_url:
            import urllib.parse
            from urllib.request import urlopen
            
            parsed = urllib.parse.urlparse(qdrant_url)
            host = parsed.hostname or 'localhost'
            port = parsed.port or 6333
            
            # Try to connect to Qdrant
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            
            if result == 0:
                # Try to get health endpoint if available
                try:
                    health_url = f"{qdrant_url}/healthz" if not qdrant_url.endswith('/') else f"{qdrant_url}healthz"
                    response = urlopen(health_url, timeout=2)
                    if response.getcode() == 200:
                        health_status['services']['qdrant'] = {
                            'status': 'healthy',
                            'message': 'Qdrant is accessible and healthy'
                        }
                    else:
                        health_status['services']['qdrant'] = {
                            'status': 'healthy',
                            'message': f'Qdrant is accessible on {host}:{port}'
                        }
                except Exception:
                    health_status['services']['qdrant'] = {
                        'status': 'healthy',
                        'message': f'Qdrant is accessible on {host}:{port}'
                    }
            else:
                raise Exception(f"Qdrant is not accessible on {host}:{port}")
        else:
            health_status['services']['qdrant'] = {
                'status': 'not_configured',
                'message': 'Qdrant is not configured'
            }
    except Exception as e:
        logger.error(f"Qdrant health check failed: {str(e)}")
        health_status['services']['qdrant'] = {
            'status': 'unhealthy',
            'message': f'Qdrant check failed: {str(e)}'
        }
        # Don't mark overall as unhealthy if Qdrant fails (it's optional)
    
    # Set overall status
    health_status['status'] = 'healthy' if overall_healthy else 'unhealthy'
    
    # Return appropriate HTTP status code
    http_status = 200 if overall_healthy else 503
    
    return JsonResponse(health_status, status=http_status)

