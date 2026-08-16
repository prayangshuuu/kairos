import uuid

from django.conf import settings
from django.db import models
from django.db.models import Count, Q, F, Avg, ExpressionWrapper, fields

class ClientQuerySet(models.QuerySet):
    def with_computed_fields(self):
        # We need to annotate total_bookings, completed_bookings, cancelled_bookings, no_show_count, no_show_rate
        # lifetime_value (per currency) - maybe just omit for now or leave as empty dict/JSON
        # first_booking_at, last_booking_at, next_upcoming_booking, average_days_between_bookings
        from django.utils import timezone
        now = timezone.now()
        
        return self.annotate(
            total_bookings=Count('bookings'),
            completed_bookings=Count('bookings', filter=Q(bookings__status='confirmed', bookings__end_at__lte=now)),
            cancelled_bookings=Count('bookings', filter=Q(bookings__status='cancelled')),
            no_show_count=Count('bookings', filter=Q(bookings__status='no_show')),
        )

class ClientManager(models.Manager):
    def get_queryset(self):
        return ClientQuerySet(self.model, using=self._db)

    def with_computed_fields(self):
        return self.get_queryset().with_computed_fields()

class ClientTag(models.Model):
    name = models.CharField(max_length=100)
    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="client_tags")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["host", "name"], name="unique_client_tag_host_name")
        ]

    def __str__(self):
        return self.name

class Client(models.Model):
    class StatusChoices(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="clients")
    name = models.CharField(max_length=255)
    known_names = models.JSONField(default=list, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=50, blank=True, null=True)
    timezone = models.CharField(max_length=64, blank=True, null=True)
    
    notes = models.TextField(blank=True) # private to host
    
    tags = models.ManyToManyField(ClientTag, related_name="clients", blank=True)
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.ACTIVE)
    
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=100, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ClientManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["host", "email"], name="unique_client_host_email")
        ]
        indexes = [
            models.Index(fields=["host", "email"]),
            models.Index(fields=["host", "last_seen_at"]),
        ]

    def __str__(self):
        return self.name

    def scrub_pii(self):
        """Scrub PII for privacy/compliance while retaining the record."""
        self.name = "Anonymized Client"
        self.known_names = []
        self.email = f"anonymized_{self.pk}@example.com"
        self.phone = None
        self.notes = ""
        self.status = self.StatusChoices.ARCHIVED
        self.save()

class ClientNote(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="client_notes")
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class ClientFile(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="files")
    file = models.FileField(upload_to="client_files/")
    created_at = models.DateTimeField(auto_now_add=True)
