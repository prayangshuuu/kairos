from abc import ABC, abstractmethod
from typing import Any, Optional
from dataclasses import dataclass
from apps.bookings.models import Booking, BookingReference

@dataclass
class MeetingDetails:
    url: str
    id: str
    provider_name: str
    password: Optional[str] = None
    dial_in_numbers: Optional[list[str]] = None

class ConferenceProvider(ABC):
    @abstractmethod
    def create_meeting(self, booking: Booking) -> MeetingDetails:
        pass

    @abstractmethod
    def update_meeting(self, booking: Booking, reference: BookingReference) -> MeetingDetails:
        pass

    @abstractmethod
    def delete_meeting(self, reference: BookingReference) -> None:
        pass
