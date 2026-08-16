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

    # Cross-table validation (db constraint is in URLNamespace)
    from apps.accounts.models import User
    from apps.teams.models import Team

    # If the user or team is being saved, it might already own this slug.
    # We can't perfectly check ownership in a validator since we don't have the instance,
    # but the URLNamespace save() process will enforce the db-level constraint.
