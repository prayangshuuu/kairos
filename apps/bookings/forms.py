import json
from datetime import UTC, datetime

from django import forms
from django.core.exceptions import ValidationError
from django.core.signing import BadSignature, SignatureExpired, Signer


class BookingForm(forms.Form):
    invitee_name = forms.CharField(
        max_length=150,
        required=True,
        label="Name",
        widget=forms.TextInput(
            attrs={
                "class": "block w-full rounded-md border-surface-300 shadow-sm focus:border-primary-500 focus:ring-primary-500 sm:text-sm"
            }
        ),
    )
    invitee_email = forms.EmailField(
        required=True,
        label="Email",
        widget=forms.EmailInput(
            attrs={
                "class": "block w-full rounded-md border-surface-300 shadow-sm focus:border-primary-500 focus:ring-primary-500 sm:text-sm"
            }
        ),
    )
    guest_emails = forms.CharField(required=False, widget=forms.HiddenInput())
    invitee_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": "block w-full rounded-md border-surface-300 shadow-sm focus:border-primary-500 focus:ring-primary-500 sm:text-sm",
            }
        ),
        label="Additional notes",
    )

    # Hidden fields
    slot_time = forms.CharField(widget=forms.HiddenInput(), required=True)
    tz = forms.CharField(widget=forms.HiddenInput(), required=True)
    event_type_id = forms.CharField(widget=forms.HiddenInput(), required=True)

    # Anti-abuse
    website = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(
            attrs={"style": "display:none;", "tabindex": "-1", "autocomplete": "off"}
        ),
    )
    timestamp_token = forms.CharField(widget=forms.HiddenInput(), required=True)
    idempotency_token = forms.CharField(widget=forms.HiddenInput(), required=True)

    def __init__(self, *args, **kwargs):
        self.event_type = kwargs.pop("event_type", None)
        super().__init__(*args, **kwargs)

        if self.event_type:
            # Setup notes label if available (falling back to default)
            # The prompt says "labelled from the event type or a sensible default"
            # We don't have a specific field for notes label in event_type, so we'll use a sensible default.
            self.fields[
                "invitee_notes"
            ].label = "Please share anything that will help prepare for our meeting"

            # Add dynamic questions
            for q in self.event_type.questions.all().order_by("order"):
                field_name = f"question_{q.id}"

                f_kwargs = {
                    "label": q.label,
                    "help_text": q.help_text,
                    "required": q.is_required,
                }

                if q.field_type == "text":
                    field = forms.CharField(
                        **f_kwargs,
                        widget=forms.TextInput(
                            attrs={
                                "class": "block w-full rounded-md border-surface-300 shadow-sm focus:border-primary-500 focus:ring-primary-500 sm:text-sm"
                            }
                        ),
                    )
                elif q.field_type == "textarea":
                    f_kwargs["widget"] = forms.Textarea(
                        attrs={
                            "rows": 3,
                            "class": "block w-full rounded-md border-surface-300 shadow-sm focus:border-primary-500 focus:ring-primary-500 sm:text-sm",
                        }
                    )
                    field = forms.CharField(**f_kwargs)
                elif q.field_type == "select":
                    choices = [(opt, opt) for opt in q.options] if q.options else []
                    if not q.is_required:
                        choices.insert(0, ("", "---------"))
                    f_kwargs["choices"] = choices
                    f_kwargs["widget"] = forms.Select(
                        attrs={
                            "class": "block w-full rounded-md border-surface-300 shadow-sm focus:border-primary-500 focus:ring-primary-500 sm:text-sm"
                        }
                    )
                    field = forms.ChoiceField(**f_kwargs)
                elif q.field_type == "radio":
                    choices = [(opt, opt) for opt in q.options] if q.options else []
                    f_kwargs["choices"] = choices
                    f_kwargs["widget"] = forms.RadioSelect(
                        attrs={
                            "class": "h-4 w-4 text-primary-600 border-surface-300 focus:ring-primary-500"
                        }
                    )
                    field = forms.ChoiceField(**f_kwargs)
                    field.is_radio_or_checkbox_multiple = True
                elif q.field_type == "multiselect":
                    choices = [(opt, opt) for opt in q.options] if q.options else []
                    f_kwargs["choices"] = choices
                    f_kwargs["widget"] = forms.CheckboxSelectMultiple(
                        attrs={
                            "class": "h-4 w-4 text-primary-600 border-surface-300 focus:ring-primary-500"
                        }
                    )
                    field = forms.MultipleChoiceField(**f_kwargs)
                    field.is_radio_or_checkbox_multiple = True
                elif q.field_type == "checkbox":
                    field = forms.BooleanField(
                        **f_kwargs,
                        widget=forms.CheckboxInput(
                            attrs={
                                "class": "h-4 w-4 text-primary-600 border-surface-300 focus:ring-primary-500 rounded"
                            }
                        ),
                    )
                    field.is_single_checkbox = True
                elif q.field_type == "number":
                    field = forms.FloatField(
                        **f_kwargs,
                        widget=forms.NumberInput(
                            attrs={
                                "class": "block w-full rounded-md border-surface-300 shadow-sm focus:border-primary-500 focus:ring-primary-500 sm:text-sm"
                            }
                        ),
                    )
                elif q.field_type == "phone":
                    # Accept international loosely, e.g. +1 555-1234
                    f_kwargs["widget"] = forms.TextInput(
                        attrs={
                            "type": "tel",
                            "class": "block w-full rounded-md border-surface-300 shadow-sm focus:border-primary-500 focus:ring-primary-500 sm:text-sm",
                        }
                    )
                    field = forms.CharField(**f_kwargs)
                elif q.field_type == "email":
                    field = forms.EmailField(
                        **f_kwargs,
                        widget=forms.EmailInput(
                            attrs={
                                "class": "block w-full rounded-md border-surface-300 shadow-sm focus:border-primary-500 focus:ring-primary-500 sm:text-sm"
                            }
                        ),
                    )
                elif q.field_type == "url":
                    field = forms.URLField(
                        **f_kwargs,
                        widget=forms.URLInput(
                            attrs={
                                "class": "block w-full rounded-md border-surface-300 shadow-sm focus:border-primary-500 focus:ring-primary-500 sm:text-sm"
                            }
                        ),
                    )
                else:
                    field = forms.CharField(
                        **f_kwargs,
                        widget=forms.TextInput(
                            attrs={
                                "class": "block w-full rounded-md border-surface-300 shadow-sm focus:border-primary-500 focus:ring-primary-500 sm:text-sm"
                            }
                        ),
                    )

                self.fields[field_name] = field
                field.question_obj = q

    def clean_website(self):
        val = self.cleaned_data.get("website")
        if val:
            raise ValidationError("Invalid request.")
        return val

    def clean_timestamp_token(self):
        val = self.cleaned_data.get("timestamp_token")
        signer = Signer()
        try:
            original = signer.unsign(val)
            timestamp = float(original)
            if datetime.now(UTC).timestamp() - timestamp < 2.0:
                raise ValidationError("You are submitting too fast. Please try again.")
        except (BadSignature, SignatureExpired, ValueError):
            raise ValidationError("Session expired or invalid. Please reload the page.")
        return val

    def clean_guest_emails(self):
        val = self.cleaned_data.get("guest_emails")
        if not val:
            return []

        try:
            emails = json.loads(val)
        except (ValueError, TypeError):
            emails = []

        if not isinstance(emails, list):
            emails = []

        if len(emails) > 10:
            raise ValidationError("You can only add up to 10 guests.")

        valid_emails = []
        email_field = forms.EmailField()
        for email in emails:
            if not isinstance(email, str) or not email.strip():
                continue
            try:
                valid_emails.append(email_field.clean(email))
            except ValidationError:
                raise ValidationError(f"Invalid email address: {email}")

        return valid_emails

    def clean_slot_time(self):
        val = self.cleaned_data.get("slot_time")
        try:
            # Python 3.11+ handles Z, earlier ones need replacement
            if val.endswith("Z"):
                val = val[:-1] + "+00:00"
            dt = datetime.fromisoformat(val)
        except (ValueError, TypeError):
            raise ValidationError("Invalid date format.")

        if not dt.tzinfo:
            raise ValidationError("Date must be timezone-aware.")

        if dt < datetime.now(UTC):
            raise ValidationError("This time slot is in the past.")

        return dt

    def clean(self):
        cleaned_data = super().clean()

        # Build answers JSON structure
        answers = {}

        if self.event_type:
            for q in self.event_type.questions.all():
                field_name = f"question_{q.id}"
                if field_name in cleaned_data:
                    answers[str(q.id)] = {"label": q.label, "value": cleaned_data[field_name]}

        cleaned_data["answers"] = answers
        return cleaned_data
