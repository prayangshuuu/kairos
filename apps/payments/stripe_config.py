"""
Resolves this Kairos instance's own Stripe Connect platform credentials.

Every self-hosted deployment has a different Stripe platform account, so the
in-app settings page (staff-only) is the primary source — values are stored
encrypted in PlatformStripeSettings. The STRIPE_* environment variables remain
supported as a fallback for operators who prefer to configure via .env.
"""

from django.conf import settings


def _load_settings():
    from apps.payments.models import PlatformStripeSettings

    return PlatformStripeSettings.objects.first()


def get_stripe_secret_key() -> str:
    cfg = _load_settings()
    if cfg and cfg.secret_key:
        return cfg.secret_key
    return getattr(settings, "STRIPE_SECRET_KEY", "")


def get_stripe_publishable_key() -> str:
    cfg = _load_settings()
    if cfg and cfg.publishable_key:
        return cfg.publishable_key
    return getattr(settings, "STRIPE_PUBLISHABLE_KEY", "")


def get_stripe_webhook_secret() -> str:
    cfg = _load_settings()
    if cfg and cfg.webhook_secret:
        return cfg.webhook_secret
    return getattr(settings, "STRIPE_CONNECT_WEBHOOK_SECRET", "")


def stripe_is_configured() -> bool:
    return bool(get_stripe_secret_key())
