class GoogleApiError(Exception):
    pass

class TerminalGoogleApiError(GoogleApiError):
    pass

class TransientGoogleApiError(GoogleApiError):
    pass
