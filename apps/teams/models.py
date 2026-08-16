import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone
from apps.accounts.validators import validate_slug, validate_timezone

class Team(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=60, unique=True, validators=[validate_slug])
    description = models.TextField(blank=True)
    avatar = models.ImageField(upload_to="team_avatars/", null=True, blank=True)
    brand_colour = models.CharField(max_length=7, default="#000000")
    timezone = models.CharField(max_length=64, default="UTC", validators=[validate_timezone])
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_teams")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.slug:
            from apps.accounts.models import User
            from django.core.exceptions import ValidationError
            if User.objects.filter(slug__iexact=self.slug).exists():
                raise ValidationError({"slug": "This slug is already in use by a user."})

    def save(self, *args, **kwargs):
        if self.slug:
            self.slug = self.slug.lower()
            
        old_slug = None
        if self.pk:
            old_team = Team.objects.filter(pk=self.pk).first()
            if old_team and old_team.slug != self.slug:
                old_slug = old_team.slug
                
        super().save(*args, **kwargs)
        
        if self.slug:
            from apps.core.models import URLNamespace
            URLNamespace.objects.get_or_create(slug=self.slug)

    @property
    def booking_url(self):
        if self.slug:
            return f"/{self.slug}"
        return None


class TeamMembership(models.Model):
    class RoleChoices(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    class StatusChoices(models.TextChoices):
        INVITED = "invited", "Invited"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_memberships")
    role = models.CharField(max_length=20, choices=RoleChoices.choices, default=RoleChoices.MEMBER)
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.INVITED)
    
    accepted_at = models.DateTimeField(null=True, blank=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="invitations_sent")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["team", "user"], name="unique_team_user")
        ]

    def __str__(self):
        return f"{self.user} in {self.team} ({self.role})"


class TeamInvitation(models.Model):
    class StatusChoices(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        EXPIRED = "expired", "Expired"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=TeamMembership.RoleChoices.choices, default=TeamMembership.RoleChoices.MEMBER)
    token = models.CharField(max_length=64, unique=True, db_index=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_invites_sent")
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["team", "email"], condition=models.Q(status="pending"), name="unique_pending_invite_per_email_team")
        ]

    def __str__(self):
        return f"Invite to {self.email} for {self.team}"
    
    @property
    def is_expired(self):
        return timezone.now() > self.expires_at or self.status == self.StatusChoices.EXPIRED
