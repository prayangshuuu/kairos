from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from encrypted_fields.fields import EncryptedTextField
from cryptography.fernet import Fernet, MultiFernet
from django.core.exceptions import ImproperlyConfigured

class DedicatedKeyEncryptedTextField(EncryptedTextField):
    @property
    def keys(self) -> list[bytes]:
        # Enforce dedicated key
        fernet_keys = getattr(settings, 'FERNET_KEYS', [])
        if not fernet_keys:
            raise ImproperlyConfigured("FERNET_KEYS must be configured for OAuth token encryption.")
        return fernet_keys


class CalendarConnection(models.Model):
    PROVIDER_CHOICES = (
        ("google", "Google"),
        ("microsoft", "Microsoft"),
        ("caldav", "CalDAV"),
        ("apple", "Apple"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="calendar_connections",
    )
    provider = models.CharField(max_length=50, choices=PROVIDER_CHOICES)
    external_account_email = models.EmailField(
        help_text=_("The email address of the connected account")
    )
    external_account_id = models.CharField(
        max_length=255,
        help_text=_("The stable provider account identifier")
    )
    # Note: rotating OAUTH_ENCRYPTION_KEY requires re-authorising every connection
    access_token = DedicatedKeyEncryptedTextField(blank=True, null=True)
    refresh_token = DedicatedKeyEncryptedTextField(blank=True, null=True)
    token_expires_at = models.DateTimeField(blank=True, null=True)
    scopes = models.JSONField(
        default=list,
        blank=True,
        help_text=_("List of granted scopes")
    )
    is_active = models.BooleanField(default=True)
    last_error = models.TextField(blank=True)
    last_error_at = models.DateTimeField(blank=True, null=True)
    last_synced_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "provider", "external_account_id"],
                name="unique_user_provider_account"
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.provider} ({self.external_account_email})"

class NotificationLog(models.Model):
    connection = models.ForeignKey(CalendarConnection, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=100)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["connection", "kind"], name="unique_notification_kind_per_connection")
        ]

    def __str__(self):
        return f"{self.kind} for {self.connection_id}"
