from django import forms

from .models import User, UserNotificationPreference
from .validators import validate_slug


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["display_name", "bio", "avatar"]


class SlugForm(forms.Form):
    slug = forms.CharField(max_length=40, validators=[validate_slug])
    password = forms.CharField(widget=forms.PasswordInput)


class PreferencesForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["timezone", "locale", "time_format", "week_start"]


class NotificationPreferencesForm(forms.ModelForm):
    class Meta:
        model = UserNotificationPreference
        fields = ["new_booking", "reschedule", "pending_reminder", "daily_agenda"]


class BrandingForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["brand_color"]
