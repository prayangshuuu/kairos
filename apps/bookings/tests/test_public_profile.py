import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.scheduling.models import EventType

pytestmark = pytest.mark.django_db


def test_public_profile_privacy_and_caching(client):
    user = User.objects.create_user(
        email="host@example.com", password="password", slug="host_slug", is_active=True
    )

    # Create one visible and one hidden event
    EventType.objects.create(
        owner=user, slug="visible", title="Visible Event", is_hidden=False, is_active=True
    )
    EventType.objects.create(
        owner=user, slug="hidden", title="Hidden Event", is_hidden=True, is_active=True
    )

    url = reverse("bookings:public_profile", kwargs={"slug": "host_slug"})
    response = client.get(url)

    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Visible event should be present
    assert "Visible Event" in content

    # Hidden event must be completely absent from response body
    assert "Hidden Event" not in content
    assert "/hidden/" not in content  # the URL to the event should not be present either

    # No session cookie should be set
    assert not response.cookies.get("sessionid")
    assert "Set-Cookie" not in response.headers or "sessionid" not in response.headers.get(
        "Set-Cookie", ""
    )

    # Cache headers must be present
    assert "max-age=60" in response.headers["Cache-Control"]
    assert "stale-while-revalidate=300" in response.headers["Cache-Control"]
