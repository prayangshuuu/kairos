import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.scheduling.models import EventType, Schedule

pytestmark = pytest.mark.django_db(transaction=True)


def test_cannot_edit_other_users_event_type(client):
    user1 = User.objects.create_user(email="user1@example.com", password="password", slug="user1")
    User.objects.create_user(email="user2@example.com", password="password", slug="user2")

    schedule = Schedule.objects.create(user=user1, name="Default", timezone="UTC")
    EventType.objects.create(
        owner=user1, schedule=schedule, title="Secret Event", slug="secret", duration_minutes=30
    )

    # Login as user2
    client.login(email="user2@example.com", password="password")

    url = reverse("scheduling:eventtype_edit", kwargs={"slug": "secret"})
    response = client.get(url)

    # Should be 404 because OwnerRequiredMixin filters the queryset
    assert response.status_code == 404
