import json
import logging
from urllib.parse import urlencode
import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib import messages
from django.utils import timezone
from .models import CalendarConnection

logger = logging.getLogger(__name__)
signer = TimestampSigner()

# -----------------
# Configuration
# -----------------
GOOGLE_OAUTH_CLIENT_ID = getattr(settings, 'GOOGLE_OAUTH_CLIENT_ID', '')
GOOGLE_OAUTH_CLIENT_SECRET = getattr(settings, 'GOOGLE_OAUTH_CLIENT_SECRET', '')
# Note: In production, configure Google Console with the absolute URL for the callback
# e.g. https://yourdomain.com/dashboard/integrations/google/callback/

REQUIRED_SCOPES = [
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/calendar.readonly'
]

@login_required
def dashboard(request):
    connections = request.user.calendar_connections.all()
    
    # Identify which providers are connected
    connected_providers = {conn.provider: conn for conn in connections}

    context = {
        'google_conn': connected_providers.get('google'),
        'microsoft_conn': connected_providers.get('microsoft'),
        'caldav_conn': connected_providers.get('caldav'),
        'apple_conn': connected_providers.get('apple'),
    }
    return render(request, 'integrations/dashboard.html', context)


@login_required
def google_connect(request):
    if not GOOGLE_OAUTH_CLIENT_ID:
        messages.error(request, "Google OAuth is not configured on this server.")
        return redirect('integrations:dashboard')

    # Signed state to prevent CSRF, expires in 10 minutes
    # It carries the user ID so we can verify the callback is for the same user
    state = signer.sign(str(request.user.id))

    redirect_uri = request.build_absolute_uri(reverse('integrations:google_callback'))
    
    params = {
        'client_id': GOOGLE_OAUTH_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': ' '.join(REQUIRED_SCOPES),
        'access_type': 'offline',
        'prompt': 'consent', # Google only returns refresh_token on FIRST auth unless forced
        'state': state,
    }
    
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    return redirect(auth_url)


@login_required
def google_callback(request):
    error = request.GET.get('error')
    if error:
        messages.error(request, f"Google returned an error: {error}")
        return redirect('integrations:dashboard')

    state = request.GET.get('state')
    code = request.GET.get('code')
    
    if not state or not code:
        messages.error(request, "Invalid callback request.")
        return redirect('integrations:dashboard')

    try:
        # Verify the state signature and ensure it belongs to the current user
        # Max age: 10 minutes (600 seconds)
        original_user_id = signer.unsign(state, max_age=600)
        if str(request.user.id) != original_user_id:
            raise BadSignature("User mismatch")
    except (BadSignature, SignatureExpired):
        messages.error(request, "Invalid or expired state parameter. Please try again.")
        return redirect('integrations:dashboard')

    redirect_uri = request.build_absolute_uri(reverse('integrations:google_callback'))
    
    # Exchange code for token
    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        'client_id': GOOGLE_OAUTH_CLIENT_ID,
        'client_secret': GOOGLE_OAUTH_CLIENT_SECRET,
        'code': code,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }
    
    token_response = requests.post(token_url, data=token_data)
    if not token_response.ok:
        logger.error(f"Failed to fetch token: {token_response.text}")
        messages.error(request, "Failed to connect to Google.")
        return redirect('integrations:dashboard')
        
    token_json = token_response.json()
    access_token = token_json.get('access_token')
    refresh_token = token_json.get('refresh_token')
    expires_in = token_json.get('expires_in', 3600)
    granted_scopes = token_json.get('scope', '').split(' ')
    
    # Verify scopes
    missing_scopes = [s for s in REQUIRED_SCOPES if s not in granted_scopes]
    if missing_scopes:
        # User unticked a permission
        messages.error(
            request, 
            "You did not grant all required permissions. "
            "Please reconnect and ensure all checkboxes are checked."
        )
        return redirect('integrations:dashboard')

    # Fetch primary calendar for identity (fewer scopes required than userinfo)
    calendar_api_url = "https://www.googleapis.com/calendar/v3/calendars/primary"
    headers = {'Authorization': f"Bearer {access_token}"}
    profile_response = requests.get(calendar_api_url, headers=headers)
    
    if not profile_response.ok:
        logger.error(f"Failed to fetch primary calendar: {profile_response.text}")
        messages.error(request, "Failed to identify Google account.")
        return redirect('integrations:dashboard')
        
    profile_json = profile_response.json()
    # The primary calendar's 'id' is always the account's primary email address
    external_account_id = profile_json.get('id')
    external_account_email = profile_json.get('id')

    if not external_account_id:
        messages.error(request, "Could not identify Google account.")
        return redirect('integrations:dashboard')

    # Store or update connection
    connection, created = CalendarConnection.objects.get_or_create(
        user=request.user,
        provider='google',
        external_account_id=external_account_id,
        defaults={
            'external_account_email': external_account_email,
        }
    )
    
    # If it existed, the email might have changed, but id is stable
    connection.external_account_email = external_account_email
    connection.access_token = access_token
    # Only update refresh_token if it was returned (sometimes it isn't if prompt=consent is somehow skipped)
    if refresh_token:
        connection.refresh_token = refresh_token
    connection.token_expires_at = timezone.now() + timezone.timedelta(seconds=expires_in)
    connection.scopes = granted_scopes
    connection.is_active = True
    connection.last_error = ""
    connection.last_error_at = None
    connection.save()
    
    # Sync Calendar List
    calendar_list_url = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
    calendar_list_response = requests.get(calendar_list_url, headers=headers)
    if calendar_list_response.ok:
        from apps.integrations.models import SelectedCalendar
        calendars = calendar_list_response.json().get('items', [])
        for cal in calendars:
            is_primary = cal.get('primary', False)
            SelectedCalendar.objects.update_or_create(
                connection=connection,
                external_calendar_id=cal['id'],
                defaults={
                    'name': cal.get('summaryOverride') or cal.get('summary', 'Unknown Calendar'),
                    'summary': cal.get('description', ''),
                    'background_color': cal.get('backgroundColor', ''),
                    'is_busy_source': True,
                    'is_write_target': is_primary,
                }
            )
            
    # Trigger initial busy sync
    from apps.integrations.tasks import sync_busy_time
    sync_busy_time.delay(connection.id)
    
    messages.success(request, f"Successfully connected Google Calendar for {external_account_email}")
    return redirect('integrations:dashboard')


@login_required
def google_disconnect(request):
    if request.method == 'POST':
        connections = request.user.calendar_connections.filter(provider='google', is_active=True)
        for connection in connections:
            if connection.access_token or connection.refresh_token:
                revoke_token = connection.refresh_token or connection.access_token
                if revoke_token:
                    # Best effort revocation
                    requests.post(
                        "https://oauth2.googleapis.com/revoke",
                        data={'token': revoke_token},
                        headers={'Content-type': 'application/x-www-form-urlencoded'}
                    )
            
            # Deactivate instead of hard-delete to prevent orphaning related bookings
            connection.is_active = False
            connection.access_token = ""
            connection.refresh_token = ""
            connection.save()
            
        messages.success(request, "Google Calendar disconnected.")
    return redirect('integrations:dashboard')
