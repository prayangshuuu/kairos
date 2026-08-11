from django.http import HttpResponse, JsonResponse
from django.db import connection

def healthz(request):
    """Liveness probe - no DB access"""
    return HttpResponse("ok", status=200)

def readyz(request):
    """Readiness probe - checks DB and Redis"""
    try:
        # Check DB
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            
        # Check Redis if possible (we can just check cache)
        from django.core.cache import cache
        cache.set("readyz", "ok", timeout=1)
        if cache.get("readyz") != "ok":
            return HttpResponse("Cache unavailable", status=503)
            
        return HttpResponse("ready", status=200)
    except Exception as e:
        return HttpResponse(str(e), status=503)

from django.shortcuts import render

def custom_bad_request(request, exception=None):
    return render(request, "errors/400.html", status=400)

def custom_permission_denied(request, exception=None):
    return render(request, "errors/403.html", status=403)

def custom_page_not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)

def custom_server_error(request, exception=None):
    return render(request, "errors/500.html", status=500)
