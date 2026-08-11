import logging
import time
from typing import Any
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from .credentials import get_valid_credentials
from apps.integrations.models import CalendarConnection
from .exceptions import TerminalGoogleApiError, TransientGoogleApiError

logger = logging.getLogger(__name__)

class GoogleCalendarClient:
    def __init__(self, connection: CalendarConnection):
        self.connection = connection
        self.credentials = get_valid_credentials(connection)
        self.service = build("calendar", "v3", credentials=self.credentials, cache_discovery=False)

    def _execute_with_retry(self, request_method: Any, *args, **kwargs) -> Any:
        max_attempts = 5
        base_delay = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"Executing Google API call for connection_id={self.connection.id}")
                return request_method(*args, **kwargs).execute()
            except HttpError as e:
                if e.resp.status in [429, 500, 502, 503, 504]:
                    if attempt == max_attempts:
                        logger.error(f"Google API transient error max retries reached for connection_id={self.connection.id}. Status: {e.resp.status}")
                        raise TransientGoogleApiError(f"API Error {e.resp.status}: {e.reason}")
                    
                    retry_after = e.resp.get("Retry-After")
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            delay = base_delay * (2 ** (attempt - 1))
                    else:
                        delay = base_delay * (2 ** (attempt - 1))
                    
                    logger.warning(f"Google API transient error {e.resp.status} for connection_id={self.connection.id}. Retrying in {delay}s...")
                    time.sleep(delay)
                elif e.resp.status in [400, 401, 403, 404]:
                    # Check for invalid grant specifically
                    if e.resp.status == 401 and "invalid_grant" in str(e.content).lower():
                        from apps.integrations.tasks import handle_terminal_connection_error
                        handle_terminal_connection_error(self.connection.id, str(e.content))
                        raise TerminalGoogleApiError(f"Terminal error invalid_grant: {e.reason}")
                    
                    # Log other 4xx at error with response body, but do not deactivate connection
                    logger.error(f"Google API client error {e.resp.status} for connection_id={self.connection.id}. Body: {e.content}")
                    raise TransientGoogleApiError(f"API Error {e.resp.status}: {e.reason}")
                else:
                    raise TransientGoogleApiError(f"Unexpected API Error: {e.reason}")
            except Exception as e:
                # Catch generic exceptions (network, timeout)
                if attempt == max_attempts:
                    logger.error(f"Google API network/timeout error max retries reached for connection_id={self.connection.id}. Error: {e}")
                    raise TransientGoogleApiError(f"Network error: {e}")
                
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(f"Google API network error for connection_id={self.connection.id}. Retrying in {delay}s... Error: {e}")
                time.sleep(delay)

    def get_events(self, calendar_id: str, time_min: str, time_max: str) -> list[dict]:
        """
        Example API method wrapped with retry logic.
        """
        # Note: timeout must be passed at the transport layer, but google-api-python-client doesn't expose it directly in execute()
        # The recommended way is setting a global socket timeout or customizing the http object.
        return self._execute_with_retry(
            self.service.events().list,
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True
        )
