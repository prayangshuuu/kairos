import datetime
from zoneinfo import ZoneInfo
from django.db import models, transaction
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
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
