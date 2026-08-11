import datetime
import logging
from django.utils import timezone
from django.db import transaction
from django.conf import settings
from google.oauth2.credentials import Credentials
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from requests.exceptions import RequestException

from apps.integrations.models import CalendarConnection
from apps.integrations.google.exceptions import TerminalGoogleApiError, TransientGoogleApiError

logger = logging.getLogger(__name__)

# CRITICAL: Never construct Credentials anywhere else in the codebase.
# Every Google API call must go through this function to ensure token lifecycle is managed properly.
def get_valid_credentials(connection: CalendarConnection) -> Credentials:
    """
    Returns valid Google API credentials for the given CalendarConnection.
    
    If the access token expires in more than 5 minutes, it is returned unchanged.
    Otherwise, it is refreshed using the stored refresh token.
    The new access token and expiry are persisted immediately in a transaction.
    If a new refresh token is returned, it is also persisted.
    """
    if not connection.refresh_token:
        raise TerminalGoogleApiError("No refresh token available.")

    # Check if the token expires in MORE than 5 minutes
    if connection.access_token and connection.token_expires_at:
        margin = datetime.timedelta(minutes=5)
        if connection.token_expires_at > timezone.now() + margin:
            return Credentials(
                token=connection.access_token,
                refresh_token=connection.refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
                client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
            )

    try:
        with transaction.atomic():
            # Re-fetch connection with a lock
            locked_conn = CalendarConnection.objects.select_for_update().get(id=connection.id)
            
            # Check again in case another thread just refreshed it
            if locked_conn.access_token and locked_conn.token_expires_at:
                margin = datetime.timedelta(minutes=5)
                if locked_conn.token_expires_at > timezone.now() + margin:
                    # Update our local instance
                    connection.access_token = locked_conn.access_token
                    connection.token_expires_at = locked_conn.token_expires_at
                    connection.refresh_token = locked_conn.refresh_token
                    return Credentials(
                        token=connection.access_token,
                        refresh_token=connection.refresh_token,
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
                        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
                    )

            creds = Credentials(
                token=locked_conn.access_token,
                refresh_token=locked_conn.refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
                client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
            )

            try:
                creds.refresh(Request())
            except RefreshError as e:
                error_msg = str(e).lower()
                if "invalid_grant" in error_msg:
                    # We need to raise this so atomic rolls back token changes
                    # But we handle the terminal error OUTSIDE atomic
                    raise TerminalGoogleApiError(f"invalid_grant: {e}")
                elif "429" in error_msg or "5" in error_msg:
                    raise TransientGoogleApiError(f"Transient error during refresh: {e}")
                else:
                    raise TransientGoogleApiError(f"Network error during refresh: {e}")
            except RequestException as e:
                raise TransientGoogleApiError(f"Network error during refresh: {e}")

            # Update and save the tokens
            locked_conn.access_token = creds.token
            locked_conn.token_expires_at = creds.expiry.replace(tzinfo=datetime.timezone.utc) if creds.expiry else None
            
            if creds.refresh_token and creds.refresh_token != locked_conn.refresh_token:
                locked_conn.refresh_token = creds.refresh_token
                
            locked_conn.save(update_fields=['access_token', 'token_expires_at', 'refresh_token', 'updated_at'])
            
            connection.access_token = locked_conn.access_token
            connection.token_expires_at = locked_conn.token_expires_at
            connection.refresh_token = locked_conn.refresh_token
            
            return creds
    except TerminalGoogleApiError as e:
        from apps.integrations.tasks import handle_terminal_connection_error
        handle_terminal_connection_error(connection.id, str(e))
        raise

