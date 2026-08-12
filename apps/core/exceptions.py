class KairosError(Exception):
    """Base exception for all Kairos custom errors."""

    status_code = 400


class SlotUnavailable(KairosError):
    status_code = 409


class AlreadyCancelled(KairosError):
    status_code = 400


class InvalidTransition(KairosError):
    status_code = 400
