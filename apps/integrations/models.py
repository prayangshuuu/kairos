from django.conf import settings
from django.contrib.postgres.fields import DateTimeRangeField
from django.contrib.postgres.indexes import GistIndex
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.translation import gettext_lazy as _
from encrypted_fields.fields import EncryptedTextField


class DedicatedKeyEncryptedTextField(EncryptedTextField):
    @property
    def keys(self) -> list[bytes]:
        # Enforce dedicated key
        fernet_keys = getattr(settings, "FERNET_KEYS", [])
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
        null=True,
        blank=True,
    )
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, null=True, blank=True)
    provider = models.CharField(max_length=50, choices=PROVIDER_CHOICES)
    external_account_email = models.EmailField(
        help_text=_("The email address of the connected account")
    )
    external_account_id = models.CharField(
        max_length=255, help_text=_("The stable provider account identifier")
    )
    # Note: rotating OAUTH_ENCRYPTION_KEY requires re-authorising every connection
    access_token = DedicatedKeyEncryptedTextField(blank=True, null=True)
    refresh_token = DedicatedKeyEncryptedTextField(blank=True, null=True)
    token_expires_at = models.DateTimeField(blank=True, null=True)
    scopes = models.JSONField(default=list, blank=True, help_text=_("List of granted scopes"))
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
                name="unique_user_provider_account",
                condition=models.Q(user__isnull=False)
            ),
            models.UniqueConstraint(
                fields=["team", "provider", "external_account_id"],
                name="unique_team_provider_account",
                condition=models.Q(team__isnull=False)
            ),
            models.CheckConstraint(
                condition=models.Q(user__isnull=False, team__isnull=True) | models.Q(user__isnull=True, team__isnull=False),
                name="calendar_connection_user_or_team"
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.provider} ({self.external_account_email})"


class NotificationLog(models.Model):
    connection = models.ForeignKey(
        CalendarConnection, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=100)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "kind"], name="unique_notification_kind_per_connection"
            )
        ]

    def __str__(self):
        return f"{self.kind} for {self.connection_id}"


class SelectedCalendar(models.Model):
    connection = models.ForeignKey(
        CalendarConnection, on_delete=models.CASCADE, related_name="calendars"
    )
    external_calendar_id = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    summary = models.CharField(max_length=255, blank=True)
    background_color = models.CharField(max_length=50, blank=True)
    is_busy_source = models.BooleanField(default=True)
    is_write_target = models.BooleanField(default=False)
    sync_token = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "external_calendar_id"], name="unique_connection_calendar"
            )
        ]

    def save(self, *args, **kwargs):
        from django.db import transaction

        with transaction.atomic():
            if self.is_write_target:
                if self.connection.team:
                    SelectedCalendar.objects.filter(
                        connection__team=self.connection.team, is_write_target=True
                    ).exclude(pk=self.pk).update(is_write_target=False)
                else:
                    SelectedCalendar.objects.filter(
                        connection__user=self.connection.user, connection__team__isnull=True, is_write_target=True
                    ).exclude(pk=self.pk).update(is_write_target=False)
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.external_calendar_id})"


class BusyBlock(models.Model):
    connection = models.ForeignKey(CalendarConnection, on_delete=models.CASCADE)
    calendar = models.ForeignKey(SelectedCalendar, on_delete=models.CASCADE)
    period = DateTimeRangeField()
    external_event_id = models.CharField(max_length=255)
    is_all_day = models.BooleanField(default=False)
    synced_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            GistIndex(fields=["period"], name="busyblock_period_gist"),
            models.Index(fields=["connection", "period"]),
        ]

    def __str__(self):
        return f"BusyBlock {self.period} for {self.calendar_id}"


import uuid


class WatchChannel(models.Model):
    connection = models.ForeignKey(
        CalendarConnection, on_delete=models.CASCADE, related_name="watch_channels"
    )
    calendar = models.ForeignKey(
        SelectedCalendar, on_delete=models.CASCADE, related_name="watch_channels"
    )
    channel_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    resource_id = models.CharField(max_length=255)
    token = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "calendar"], name="unique_watch_channel_per_calendar"
            )
        ]

    def __str__(self):
        return f"Watch {self.channel_id} for {self.calendar_id}"


class ConferenceConnection(models.Model):
    PROVIDER_CHOICES = (
        ("google_meet", "Google Meet"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conference_connections",
        null=True,
        blank=True,
    )
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, null=True, blank=True)
    provider = models.CharField(max_length=50, choices=PROVIDER_CHOICES)
    external_account_email = models.EmailField()
    external_account_id = models.CharField(max_length=255)
    access_token = DedicatedKeyEncryptedTextField(blank=True, null=True)
    refresh_token = DedicatedKeyEncryptedTextField(blank=True, null=True)
    token_expires_at = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "provider", "external_account_id"],
                name="unique_conference_user_provider_account",
                condition=models.Q(user__isnull=False)
            ),
            models.UniqueConstraint(
                fields=["team", "provider", "external_account_id"],
                name="unique_conference_team_provider_account",
                condition=models.Q(team__isnull=False)
            ),
            models.CheckConstraint(
                condition=models.Q(user__isnull=False, team__isnull=True) | models.Q(user__isnull=True, team__isnull=False),
                name="conference_connection_user_or_team"
            )
        ]
