from django.core.signing import TimestampSigner, BadSignature, SignatureExpired

# Note: Rotating the SECRET_KEY will invalidate every outstanding manage link.
# Production key rotation needs a plan to re-sign active bookings if required.
MANAGE_SALT = "kairos.bookings.manage"

def make_manage_token(booking) -> str:
    """Returns a signed token derived from the booking uid."""
    signer = TimestampSigner(salt=MANAGE_SALT)
    return signer.sign(str(booking.uid))

def verify_manage_token(uid: str, token: str) -> bool:
    """Verifies the token against the uid. Returns False on mismatch/tampering."""
    if not token:
        return False
        
    signer = TimestampSigner(salt=MANAGE_SALT)
    try:
        # NO expiry on manage tokens. A booking three months out must still be cancellable.
        # We use TimestampSigner to record the timestamp but do not enforce max_age.
        original = signer.unsign(token)
        return original == str(uid)
    except (BadSignature, SignatureExpired):
        return False
