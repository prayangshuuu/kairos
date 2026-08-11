import datetime
from zoneinfo import ZoneInfo
from django.db import models, transaction
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q, JSONField
from apps.teams.models import Team
from apps.accounts.validators import validate_timezone

# Availability rules are stored as NAIVE LOCAL TIMES plus a timezone on the parent Schedule.
# They are NOT stored in UTC. This is deliberate. "I work 9am to 5pm" must remain 9am-to-5pm 
# through daylight saving transitions. Conversion to UTC happens later in the slot engine, 
# per concrete date. Do not change this to store UTC!

class Schedule(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="schedules"
    )
    name = models.CharField(max_length=100)
    timezone = models.CharField(max_length=64, validators=[validate_timezone])
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_default=True),
                name="unique_default_schedule_per_user"
            )
        ]

    def save(self, *args, **kwargs):
        if not self.timezone and self.user_id:
            self.timezone = self.user.timezone

        if self.is_default:
            with transaction.atomic():
                Schedule.objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(is_default=False)
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    @property
    def zoneinfo(self):
        return ZoneInfo(self.timezone)

    def __str__(self):
        return self.name


class AvailabilityRule(models.Model):
    class WeekdayChoices(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    schedule = models.ForeignKey(
        Schedule,
        on_delete=models.CASCADE,
        related_name="rules"
    )
    # Weekday 0-6 where 0 is Monday, matching Python's date.weekday()
    weekday = models.IntegerField(choices=WeekdayChoices.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ["weekday", "start_time"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_time__gt=models.F("start_time")),
                name="rule_end_time_gt_start_time"
            )
        ]
        # Limitation: A rule cannot cross midnight. A shift like 22:00-02:00 must be entered as two rules on adjacent days.

    def __str__(self):
        return f"{self.get_weekday_display()} {self.start_time.strftime('%H:%M')} - {self.end_time.strftime('%H:%M')}"


class DateOverride(models.Model):
    schedule = models.ForeignKey(
        Schedule,
        on_delete=models.CASCADE,
        related_name="overrides"
    )
    date = models.DateField()
    is_unavailable = models.BooleanField(default=False)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    class Meta:
        ordering = ["date", "start_time"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(is_unavailable=True, start_time__isnull=True, end_time__isnull=True) |
                    Q(is_unavailable=False, start_time__isnull=False, end_time__isnull=False, end_time__gt=models.F("start_time"))
                ),
                name="override_valid_times"
            )
        ]

    def __str__(self):
        if self.is_unavailable:
            return f"{self.date} (Unavailable)"
        return f"{self.date} {self.start_time.strftime('%H:%M')} - {self.end_time.strftime('%H:%M')}"


class EventType(models.Model):
    class WindowTypeChoices(models.TextChoices):
        ROLLING = "rolling", "Rolling"
        FIXED_RANGE = "fixed_range", "Fixed Range"
        INDEFINITE = "indefinite", "Indefinite"

    class LocationTypeChoices(models.TextChoices):
        GOOGLE_MEET = "google_meet", "Google Meet"
        ZOOM = "zoom", "Zoom"
        MS_TEAMS = "ms_teams", "MS Teams"
        PHONE_HOST_CALLS = "phone_host_calls", "Phone (Host calls invitee)"
        PHONE_INVITEE_CALLS = "phone_invitee_calls", "Phone (Invitee calls host)"
        IN_PERSON = "in_person", "In Person"
        CUSTOM_LINK = "custom_link", "Custom Link"
        ASK_INVITEE = "ask_invitee", "Ask Invitee"

    class AssignmentStrategyChoices(models.TextChoices):
        SINGLE = "single", "Single"
        COLLECTIVE = "collective", "Collective"
        ROUND_ROBIN = "round_robin", "Round Robin"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_types")
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True)
    slug = models.SlugField(max_length=60)
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    duration_minutes = models.PositiveIntegerField(default=30)
    slot_interval_minutes = models.PositiveIntegerField(null=True, blank=True)

    buffer_before_minutes = models.PositiveIntegerField(default=0)
    buffer_after_minutes = models.PositiveIntegerField(default=0)
    minimum_notice_minutes = models.PositiveIntegerField(default=0)

    window_type = models.CharField(max_length=30, choices=WindowTypeChoices.choices, default=WindowTypeChoices.ROLLING)
    rolling_days = models.PositiveIntegerField(default=60)
    rolling_business_days_only = models.BooleanField(default=False)
    range_start = models.DateField(null=True, blank=True)
    range_end = models.DateField(null=True, blank=True)

    schedule = models.ForeignKey(Schedule, on_delete=models.SET_NULL, null=True, blank=True)

    location_type = models.CharField(max_length=30, choices=LocationTypeChoices.choices, default=LocationTypeChoices.GOOGLE_MEET)
    location_value = models.CharField(max_length=500, blank=True)

    max_bookings_per_day = models.PositiveIntegerField(null=True, blank=True)
    max_bookings_per_week = models.PositiveIntegerField(null=True, blank=True)
    max_bookings_per_month = models.PositiveIntegerField(null=True, blank=True)

    seats_per_slot = models.PositiveIntegerField(default=1)

    requires_confirmation = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    allow_guests = models.BooleanField(default=True)
    allow_rescheduling = models.BooleanField(default=True)
    allow_cancellation = models.BooleanField(default=True)
    cancellation_cutoff_hours = models.PositiveIntegerField(null=True, blank=True)

    price_cents = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")

    resource_id = models.UUIDField(null=True, blank=True)
    assignment_strategy = models.CharField(max_length=20, choices=AssignmentStrategyChoices.choices, default=AssignmentStrategyChoices.SINGLE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "slug"], name="unique_event_type_slug_per_owner"),
            models.CheckConstraint(
                condition=(
                    Q(duration_minutes__gte=5, duration_minutes__lte=1440)
                ),
                name="event_type_valid_duration"
            ),
            models.CheckConstraint(
                condition=(
                    Q(window_type="rolling", rolling_days__isnull=False) |
                    Q(window_type="fixed_range", range_start__isnull=False, range_end__isnull=False) |
                    Q(window_type="indefinite")
                ),
                name="event_type_valid_window"
            )
        ]

    @property
    def effective_slot_interval(self):
        return self.slot_interval_minutes or self.duration_minutes

    @property
    def effective_schedule(self):
        if self.schedule:
            return self.schedule
        return self.owner.get_default_schedule()

    @property
    def is_paid(self):
        return self.price_cents > 0

    @property
    def public_url(self):
        if self.owner.slug:
            return f"/{self.owner.slug}/{self.slug}"
        return f"/u/{self.owner.id}/{self.slug}"

    def __str__(self):
        return f"{self.title} ({self.duration_minutes} min)"


class BookingQuestion(models.Model):
    class FieldTypeChoices(models.TextChoices):
        TEXT = "text", "Text"
        TEXTAREA = "textarea", "Textarea"
        SELECT = "select", "Select"
        MULTISELECT = "multiselect", "Multiselect"
        RADIO = "radio", "Radio"
        CHECKBOX = "checkbox", "Checkbox"
        NUMBER = "number", "Number"
        PHONE = "phone", "Phone"
        EMAIL = "email", "Email"
        URL = "url", "URL"

    event_type = models.ForeignKey(EventType, on_delete=models.CASCADE, related_name="questions")
    label = models.CharField(max_length=200)
    help_text = models.CharField(max_length=300, blank=True)
    field_type = models.CharField(max_length=20, choices=FieldTypeChoices.choices, default=FieldTypeChoices.TEXT)
    options = JSONField(default=list, blank=True)
    is_required = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def clean(self):
        super().clean()
        if self.field_type in [self.FieldTypeChoices.SELECT, self.FieldTypeChoices.MULTISELECT, self.FieldTypeChoices.RADIO]:
            if not isinstance(self.options, list) or len(self.options) < 2:
                raise ValidationError({"options": "Select, multiselect, and radio fields require at least two options."})
        else:
            if self.options and len(self.options) > 0:
                raise ValidationError({"options": f"Field type {self.get_field_type_display()} does not support options."})

    def __str__(self):
        return self.label
