import hashlib
from apps.integrations.conferencing.base import ConferenceProvider, MeetingDetails
from apps.bookings.models import Booking

class JitsiProvider(ConferenceProvider):
    def create_meeting(self, booking: Booking) -> MeetingDetails:
        # Generate deterministic URL from hashed booking uid
        hash_digest = hashlib.sha256(booking.uid.hex.encode('utf-8')).hexdigest()[:16]
        room_name = f"Kairos-{hash_digest}"
        url = f"https://meet.jit.si/{room_name}"
        return MeetingDetails(
            url=url,
            id=room_name,
            provider_name="Jitsi"
        )

    def update_meeting(self, booking: Booking, reference: 'BookingReference') -> MeetingDetails:
        return MeetingDetails(
            url=reference.meeting_url,
            id=reference.external_event_id,
            provider_name="Jitsi"
        )

    def delete_meeting(self, reference: 'BookingReference') -> None:
        pass

class GoogleMeetProvider(ConferenceProvider):
    def create_meeting(self, booking: Booking) -> MeetingDetails:
        from apps.bookings.models import BookingReference
        cal_ref = BookingReference.objects.filter(booking=booking, kind="calendar_event").first()
        if not cal_ref:
            raise Exception("Cannot create Google Meet without a calendar event.")
            
        from apps.integrations.google.client import GoogleCalendarClient
        import logging
        logger = logging.getLogger(__name__)
        
        client = GoogleCalendarClient(cal_ref.connection)
        
        request_id = f"kairos-{booking.uid.hex}"
        
        try:
            event = client.service.events().patch(
                calendarId=cal_ref.external_calendar_id,
                eventId=cal_ref.external_event_id,
                conferenceDataVersion=1,
                body={
                    'conferenceData': {
                        'createRequest': {
                            'requestId': request_id,
                            'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                        }
                    }
                }
            ).execute()
        except Exception as e:
            logger.error(f"Failed to create Google Meet for booking {booking.uid}: {e}")
            raise
            
        conference_data = event.get('conferenceData', {})
        create_request = conference_data.get('createRequest', {})
        status = create_request.get('status', {}).get('statusCode')
        
        if status == 'pending':
            raise Exception("pending")
            
        if status == 'success':
            for entry_point in conference_data.get('entryPoints', []):
                if entry_point.get('entryPointType') == 'video':
                    return MeetingDetails(
                        url=entry_point.get('uri'),
                        id=conference_data.get('conferenceId', ''),
                        provider_name="Google Meet"
                    )
        
        raise Exception(f"Failed to create Google Meet, status: {status}")

    def update_meeting(self, booking: Booking, reference: 'BookingReference') -> MeetingDetails:
        return MeetingDetails(url="", id="", provider_name="Google Meet")

    def delete_meeting(self, reference: 'BookingReference') -> None:
        pass

class ZoomProvider(ConferenceProvider):
    def create_meeting(self, booking: Booking) -> MeetingDetails:
        raise NotImplementedError("Zoom integration coming soon")

    def update_meeting(self, booking: Booking, reference: 'BookingReference') -> MeetingDetails:
        raise NotImplementedError()

    def delete_meeting(self, reference: 'BookingReference') -> None:
        raise NotImplementedError()

class TeamsProvider(ConferenceProvider):
    def create_meeting(self, booking: Booking) -> MeetingDetails:
        raise NotImplementedError("Microsoft Teams integration coming soon")

    def update_meeting(self, booking: Booking, reference: 'BookingReference') -> MeetingDetails:
        raise NotImplementedError()

    def delete_meeting(self, reference: 'BookingReference') -> None:
        raise NotImplementedError()

PROVIDERS = {
    "jitsi": JitsiProvider(),
    "google_meet": GoogleMeetProvider(),
    "zoom": ZoomProvider(),
    "ms_teams": TeamsProvider()
}
