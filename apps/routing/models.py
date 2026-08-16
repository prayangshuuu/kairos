from django.db import models
from django.conf import settings
from django.utils.text import slugify

class RoutingForm(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="routing_forms")
    team = models.ForeignKey('teams.Team', on_delete=models.CASCADE, null=True, blank=True, related_name="routing_forms")
    slug = models.CharField(max_length=255)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['owner', 'slug'], name='unique_owner_routing_form_slug'),
            models.UniqueConstraint(fields=['team', 'slug'], name='unique_team_routing_form_slug'),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError
        if not self.owner and not self.team:
            raise ValidationError("Form must belong to an owner or a team.")
        if self.owner and self.team:
            raise ValidationError("Form cannot belong to both an owner and a team.")

class RoutingFormField(models.Model):
    FIELD_TYPES = (
        ('text', 'Text'),
        ('textarea', 'Textarea'),
        ('select', 'Select'),
        ('multiselect', 'Multi-select'),
        ('radio', 'Radio'),
        ('checkbox', 'Checkbox'),
        ('number', 'Number'),
        ('email', 'Email'),
        ('phone', 'Phone'),
        ('url', 'URL'),
    )
    
    form = models.ForeignKey(RoutingForm, on_delete=models.CASCADE, related_name='fields')
    order = models.PositiveIntegerField(default=0)
    label = models.CharField(max_length=255)
    help_text = models.TextField(blank=True, null=True)
    field_type = models.CharField(max_length=50, choices=FIELD_TYPES)
    options = models.JSONField(default=list, blank=True)
    is_required = models.BooleanField(default=True)
    is_routing_field = models.BooleanField(default=True)
    identifier = models.CharField(max_length=255)

    class Meta:
        ordering = ['order']
        unique_together = ('form', 'identifier')

    def __str__(self):
        return f"{self.form.title} - {self.label}"
        
    def save(self, *args, **kwargs):
        if not self.identifier:
            self.identifier = slugify(self.label).replace('-', '_')
            # ensure unique identifier within form
            base_identifier = self.identifier
            counter = 1
            while RoutingFormField.objects.filter(form=self.form, identifier=self.identifier).exists():
                self.identifier = f"{base_identifier}_{counter}"
                counter += 1
        super().save(*args, **kwargs)

class RoutingRule(models.Model):
    ACTIONS = (
        ('route_to_event_type', 'Route to Event Type'),
        ('route_to_member', 'Route to Member'),
        ('route_to_external_url', 'Route to External URL'),
        ('show_message', 'Show Message'),
    )
    
    form = models.ForeignKey(RoutingForm, on_delete=models.CASCADE, related_name='rules')
    order = models.PositiveIntegerField(default=0)
    conditions = models.JSONField(default=dict, blank=True)
    # conditions format: {"match_type": "all", "rules": [{"field_identifier": "xyz", "operator": "equals", "value": "123"}]}
    
    action = models.CharField(max_length=50, choices=ACTIONS)
    target_event_type = models.ForeignKey('scheduling.EventType', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    target_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    target_url = models.URLField(blank=True, null=True)
    message = models.TextField(blank=True, null=True)
    is_fallback = models.BooleanField(default=False)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"Rule {self.order} for {self.form.title}"
        
    def clean(self):
        super().clean()
        from django.core.exceptions import ValidationError
        if self.is_fallback:
            if RoutingRule.objects.filter(form=self.form, is_fallback=True).exclude(pk=self.pk).exists():
                raise ValidationError("Only one fallback rule is allowed per form.")

class RoutingFormResponse(models.Model):
    form = models.ForeignKey(RoutingForm, on_delete=models.CASCADE, related_name='responses')
    answers = models.JSONField(default=dict)
    matched_rule = models.ForeignKey(RoutingRule, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    routed_to_event_type = models.ForeignKey('scheduling.EventType', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    routed_to_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    booking = models.ForeignKey('bookings.Booking', on_delete=models.SET_NULL, null=True, blank=True, related_name='routing_response')
    
    session_id = models.CharField(max_length=255, blank=True, null=True)
    referrer = models.URLField(blank=True, null=True)
    utm_source = models.CharField(max_length=255, blank=True, null=True)
    utm_medium = models.CharField(max_length=255, blank=True, null=True)
    utm_campaign = models.CharField(max_length=255, blank=True, null=True)
    utm_term = models.CharField(max_length=255, blank=True, null=True)
    utm_content = models.CharField(max_length=255, blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Response for {self.form.title} at {self.created_at}"
