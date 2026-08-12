import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

import pytest
pytest.main(['-v', '--tb=short', 'apps/bookings/tests/test_meeting_links.py::test_notifications_contain_meeting_url'])
