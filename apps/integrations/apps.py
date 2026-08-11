from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    name = 'apps.integrations'

    def ready(self):
        from django.conf import settings
        from django.core.exceptions import ImproperlyConfigured
        import os

        if not settings.DEBUG and not os.environ.get('OAUTH_ENCRYPTION_KEY'):
            raise ImproperlyConfigured("OAUTH_ENCRYPTION_KEY must be set in production to encrypt OAuth tokens.")
