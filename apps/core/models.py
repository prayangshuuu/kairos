from django.db import models
from django.utils import timezone


class EmailEvent(models.Model):
    """
    Records email events (bounces, complaints) received via webhook.
    """

    recipient = models.EmailField(db_index=True)
    event_type = models.CharField(max_length=50, db_index=True)  # e.g., 'bounced', 'complained'
    message_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event_type} for {self.recipient}"


class BouncedEmail(models.Model):
    """
    Maintains a suppression list of hard-bounced or complained email addresses.
    We should never send to these again.
    """

    email = models.EmailField(unique=True)
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email
