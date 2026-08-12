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

def custom_404(request, exception=None):
    return render(request, '404.html', status=404)

def custom_500(request):
    return render(request, '500.html', status=500)

import json
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import logging

logger = logging.getLogger(__name__)

@csrf_exempt
@require_POST
def resend_webhook(request):
    """
    Webhook endpoint for Resend events (bounces, complaints).
    """
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
        
    event_type = payload.get("type")
    
    if event_type in ["email.bounced", "email.complained"]:
        data = payload.get("data", {})
        to_email = None
        
        # Resend payload structure for bounce:
        # data -> email -> to -> [email]
        # or data -> to -> [email]
        
        if "to" in data:
            to_email = data["to"][0]
            
        message_id = data.get("email_id") or payload.get("created_at")
        
        if to_email:
            from apps.core.models import EmailEvent, BouncedEmail
            
            # Log the event
            EmailEvent.objects.create(
                recipient=to_email,
                event_type=event_type,
                message_id=message_id,
                payload=payload
            )
            
            # Add to suppression list if it's a hard bounce or complaint
            # Resend categorizes bounces. Let's assume all webhook bounces are hard or worth suppressing.
            BouncedEmail.objects.get_or_create(
                email=to_email,
                defaults={"reason": f"Resend event: {event_type}"}
            )
            logger.info(f"Added {to_email} to suppression list due to {event_type}")
            
    return JsonResponse({"status": "ok"})
