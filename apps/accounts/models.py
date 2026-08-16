import zoneinfo
from zoneinfo import ZoneInfo

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _

from .validators import validate_slug, validate_timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("The given email must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class TimeFormatChoices(models.TextChoices):
        H12 = "12", "12-hour"
        H24 = "24", "24-hour"

    class WeekStartChoices(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    username = None
    email = models.EmailField(_("email address"), unique=True)
    slug = models.SlugField(
        max_length=40, unique=True, null=True, blank=True, validators=[validate_slug]
    )
    display_name = models.CharField(max_length=100, blank=True)
    bio = models.TextField(blank=True)
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    timezone = models.CharField(max_length=64, default="UTC", validators=[validate_timezone])
    locale = models.CharField(max_length=10, default="en")
    time_format = models.CharField(
        max_length=2, choices=TimeFormatChoices.choices, default=TimeFormatChoices.H12
    )
    week_start = models.IntegerField(
        choices=WeekStartChoices.choices, default=WeekStartChoices.MONDAY
    )
    brand_color = models.CharField(max_length=7, default="#000000")
    hide_branding = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def clean(self):
        super().clean()
        if self.slug:
            from apps.teams.models import Team
            from django.core.exceptions import ValidationError
            if Team.objects.filter(slug__iexact=self.slug).exists():
                raise ValidationError({"slug": "This slug is already in use by a team."})

    def save(self, *args, **kwargs):
        if self.slug:
            self.slug = self.slug.lower()
        
        # Check if slug is changing or new
        old_slug = None
        if self.pk:
            old_user = User.objects.filter(pk=self.pk).first()
            if old_user and old_user.slug != self.slug:
                old_slug = old_user.slug
        
        super().save(*args, **kwargs)

        if self.slug:
            from apps.core.models import URLNamespace
            URLNamespace.objects.get_or_create(slug=self.slug)
        if old_slug and old_slug != self.slug:
            # We don't delete from URLNamespace to prevent immediate reuse
            pass
    @property
    def booking_url(self):
        if self.slug:
            return f"/{self.slug}"
        return None

    @property
    def zoneinfo(self):
        try:
            return ZoneInfo(self.timezone)
        except zoneinfo.ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    def __str__(self):
        return self.email

    def get_default_schedule(self):
        import datetime

        from apps.scheduling.models import AvailabilityRule

        schedule = self.schedules.filter(is_default=True).first()
        if not schedule:
            schedule = self.schedules.create(
                name="Working Hours", timezone=self.timezone, is_default=True
            )
            # Create Mon-Fri 09:00-17:00
            rules = []
            for weekday in range(5):  # 0 to 4 (Mon to Fri)
                rules.append(
                    AvailabilityRule(
                        schedule=schedule,
                        weekday=weekday,
                        start_time=datetime.time(9, 0),
                        end_time=datetime.time(17, 0),
                    )
                )
            AvailabilityRule.objects.bulk_create(rules)

        return schedule


class UserSlugHistory(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="slug_history")
    old_slug = models.SlugField(max_length=40, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class UserNotificationPreference(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="notification_preferences"
    )
    new_booking = models.BooleanField(default=True)
    reschedule = models.BooleanField(default=True)
    pending_reminder = models.BooleanField(default=True)
    daily_agenda = models.BooleanField(default=True)
    # Cancellation is omitted here because it cannot be disabled.
