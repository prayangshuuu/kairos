import uuid
from apps.analytics.tasks import record_funnel_event

def track_funnel_event(request, host_id, step, event_type_id=None):
    # Honour Do Not Track
    dnt = request.headers.get('DNT')
    if dnt == '1':
        return None

    # Get or create session ID
    session_id = request.COOKIES.get("booking_session_id")
    if not session_id:
        session_id = uuid.uuid4().hex

    referrer = request.META.get("HTTP_REFERER", "")
    utm_source = request.GET.get("utm_source", "")
    utm_medium = request.GET.get("utm_medium", "")
    utm_campaign = request.GET.get("utm_campaign", "")
    
    # Simple device detection (if we don't have a library, just check if Mobile is in UA)
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    device_type = "mobile" if "Mobile" in user_agent else "desktop"

    # We could get country from Cloudflare headers if present
    country = request.headers.get("CF-IPCountry", "")

    record_funnel_event.delay(
        host_id=host_id,
        session_id=session_id,
        step=step,
        event_type_id=event_type_id,
        referrer=referrer,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        country=country,
        device_type=device_type,
    )
    return session_id

def set_funnel_cookie(response, session_id):
    if session_id:
        # Expire with the browser session (no max_age)
        response.set_cookie(
            "booking_session_id", 
            session_id, 
            samesite='Lax', 
            httponly=True
        )
    return response
