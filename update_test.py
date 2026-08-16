import re
import sys
content = open('apps/bookings/tests/test_waitlist.py').read()
content = content.replace('def test_join_waitlist_get(client, host_with_schedule, event_type):', 'def test_join_waitlist_get(client, host_with_schedule, event_type_factory):\n    event_type = event_type_factory(owner=host_with_schedule)')
content = content.replace('def test_join_waitlist_post(client, host_with_schedule, event_type, mocker):', 'def test_join_waitlist_post(client, host_with_schedule, event_type_factory, monkeypatch):\n    event_type = event_type_factory(owner=host_with_schedule)\n    class Mocker:\n        def patch(self, p):\n            from unittest.mock import MagicMock\n            mock = MagicMock()\n            monkeypatch.setattr(p, mock)\n            return mock\n    mocker = Mocker()')
content = content.replace('def test_join_waitlist_full(client, host_with_schedule, event_type):', 'def test_join_waitlist_full(client, host_with_schedule, event_type_factory):\n    event_type = event_type_factory(owner=host_with_schedule)')
content = content.replace('def test_leave_waitlist(client, host_with_schedule, event_type):', 'def test_leave_waitlist(client, host_with_schedule, event_type_factory):\n    event_type = event_type_factory(owner=host_with_schedule)')
open('apps/bookings/tests/test_waitlist.py', 'w').write(content)
