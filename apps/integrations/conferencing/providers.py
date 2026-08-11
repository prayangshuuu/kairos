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
        raise NotImplementedError("Google Meet is created via Calendar sync")

    def update_meeting(self, booking: Booking, reference: 'BookingReference') -> MeetingDetails:
        raise NotImplementedError()

    def delete_meeting(self, reference: 'BookingReference') -> None:
        raise NotImplementedError()

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
