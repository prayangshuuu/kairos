from django import forms

from apps.scheduling.models import EventType
from apps.workflows.engine import validate_template_string
from apps.workflows.models import Workflow, WorkflowStep


class WorkflowForm(forms.ModelForm):
    OFFSET_PRESETS = [
        (-1440, "24 Hours Before"),
        (-60, "1 Hour Before"),
        (-15, "15 Minutes Before"),
        (0, "Immediately on Trigger"),
        (15, "15 Minutes After"),
        (60, "1 Hour After"),
        (1440, "1 Day After"),
    ]

    offset_preset = forms.ChoiceField(
        choices=OFFSET_PRESETS,
        required=False,
        label="Time Offset Preset",
        help_text="Choose a human-friendly timing preset",
    )

    class Meta:
        model = Workflow
        fields = ["name", "trigger", "offset_minutes", "event_types", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "w-full border border-gray-300 rounded-lg p-2.5 text-sm"}),
            "trigger": forms.Select(attrs={"class": "w-full border border-gray-300 rounded-lg p-2.5 text-sm"}),
            "offset_minutes": forms.NumberInput(attrs={"class": "w-full border border-gray-300 rounded-lg p-2.5 text-sm"}),
            "event_types": forms.SelectMultiple(attrs={"class": "w-full border border-gray-300 rounded-lg p-2.5 text-sm"}),
            "is_active": forms.CheckboxInput(attrs={"class": "rounded border-gray-300 text-teal-600 focus:ring-teal-500"}),
        }

    def __init__(self, *args, user=None, team=None, **kwargs):
        super().__init__(*args, **kwargs)
        if team:
            self.fields["event_types"].queryset = EventType.objects.filter(team=team)
        elif user:
            self.fields["event_types"].queryset = EventType.objects.filter(owner=user, team__isnull=True)


class WorkflowStepForm(forms.ModelForm):
    class Meta:
        model = WorkflowStep
        fields = ["channel", "recipient", "subject_template", "body_template", "is_active"]
        widgets = {
            "channel": forms.Select(attrs={"class": "w-full border border-gray-300 rounded-lg p-2.5 text-sm"}),
            "recipient": forms.Select(attrs={"class": "w-full border border-gray-300 rounded-lg p-2.5 text-sm"}),
            "subject_template": forms.TextInput(attrs={"class": "w-full border border-gray-300 rounded-lg p-2.5 text-sm", "placeholder": "Subject (e.g. Reminder: {event_title} tomorrow)"}),
            "body_template": forms.Textarea(attrs={"class": "w-full border border-gray-300 rounded-lg p-2.5 text-sm", "rows": 5, "placeholder": "Email body content with {invitee_name}, {start_time}, etc."}),
            "is_active": forms.CheckboxInput(attrs={"class": "rounded border-gray-300 text-teal-600 focus:ring-teal-500"}),
        }

    def clean_subject_template(self):
        subject = self.cleaned_data.get("subject_template", "")
        if subject:
            validate_template_string(subject)
        return subject

    def clean_body_template(self):
        body = self.cleaned_data.get("body_template", "")
        if body:
            validate_template_string(body)
        return body
