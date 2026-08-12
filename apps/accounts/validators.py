import zoneinfo

from django.core.exceptions import ValidationError


def validate_timezone(value):
    if value not in zoneinfo.available_timezones():
        raise ValidationError(f"'{value}' is not a valid IANA timezone name.")


RESERVED_SLUGS = frozenset(
    [
        "app",
        "api",
        "admin",
        "static",
        "media",
        "cdn",
        "www",
        "login",
        "logout",
        "signup",
        "signin",
        "register",
        "help",
        "support",
        "pricing",
        "about",
        "terms",
        "privacy",
        "legal",
        "blog",
        "docs",
        "settings",
        "account",
        "accounts",
        "booking",
        "bookings",
        "event",
        "events",
        "team",
        "teams",
        "dashboard",
        "onboarding",
        "auth",
        "oauth",
        "webhook",
        "webhooks",
        "health",
        "robots",
        "sitemap",
        "favicon",
        "embed",
        "new",
        "edit",
        "delete",
        "demo",
    ]
)


def validate_slug(value):
    if not value:
        return

    value_lower = value.lower()

    if value_lower in RESERVED_SLUGS:
        raise ValidationError(f"'{value}' is a reserved slug and cannot be used.")

    if len(value) < 3:
        raise ValidationError("Slug must be at least 3 characters long.")

    if value.isdigit():
        raise ValidationError("Slug cannot be purely numeric.")
