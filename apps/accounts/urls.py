from django.urls import path
from .views import SettingsProfileView, SettingsPreferencesView, SettingsSecurityView, SettingsBrandingView, SettingsDangerView

app_name = "accounts"

urlpatterns = [
    path('settings/', SettingsProfileView.as_view(), name='settings_profile'),
    path('settings/preferences/', SettingsPreferencesView.as_view(), name='settings_preferences'),
    path('settings/security/', SettingsSecurityView.as_view(), name='settings_security'),
    path('settings/branding/', SettingsBrandingView.as_view(), name='settings_branding'),
    path('settings/danger/', SettingsDangerView.as_view(), name='settings_danger'),
]
