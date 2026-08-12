from django import forms
from .models import EventType, BookingQuestion

class EventTypeForm(forms.ModelForm):
    class Meta:
        model = EventType
        fields = [
            'title', 'slug', 'description', 'duration_minutes', 'location_type', 'location_value',
            'schedule', 'window_type', 'rolling_days', 'rolling_business_days_only', 'range_start', 'range_end',
            'minimum_notice_minutes', 'buffer_before_minutes', 'buffer_after_minutes', 'slot_interval_minutes',
            'max_bookings_per_day', 'max_bookings_per_week', 'max_bookings_per_month', 'seats_per_slot',
            'cancellation_cutoff_hours', 'reschedule_cutoff_hours', 'confirmation_deadline_hours',
            'requires_confirmation', 'is_hidden', 'is_active', 'allow_guests', 'allow_rescheduling', 'allow_cancellation',
            'price_cents', 'currency', 'payment_provider'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # We can add custom widget attrs or let Alpine handle it in the template

class BookingQuestionForm(forms.ModelForm):
    class Meta:
        model = BookingQuestion
        fields = ['label', 'help_text', 'field_type', 'options', 'is_required', 'order']

BookingQuestionFormSet = forms.inlineformset_factory(
    EventType,
    BookingQuestion,
    form=BookingQuestionForm,
    extra=0,
    can_delete=True
)
