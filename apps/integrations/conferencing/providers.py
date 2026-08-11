
from django.db import transaction
from django.utils import timezone
from apps.integrations.models import ConferenceConnection

def get_valid_zoom_credentials(connection_id):
    with transaction.atomic():
        conn = ConferenceConnection.objects.select_for_update().get(id=connection_id)
        if conn.token_expires_at and conn.token_expires_at < timezone.now():
            # mock refresh logic
            conn.access_token = "new_access_token"
            conn.token_expires_at = timezone.now() + timezone.timedelta(hours=1)
            conn.save(update_fields=['access_token', 'token_expires_at'])
        return conn

class ConferenceProvider:
    def create_meeting(self, booking):
        raise NotImplementedError
    def update_meeting(self, booking):
        raise NotImplementedError
    def delete_meeting(self, booking):
        raise NotImplementedError

class ZoomProvider(ConferenceProvider):
    def create_meeting(self, booking):
        # idempotency check
        if hasattr(booking, 'reference'):
            return booking.reference.meeting_url
        
        url = "https://zoom.us/j/1234567890"
        return url

    def update_meeting(self, booking):
        pass

    def delete_meeting(self, booking):
        pass
