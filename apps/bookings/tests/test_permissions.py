import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_dashboard_requires_login(client):
    res = client.get(reverse("bookings:dashboard_bookings"))
    assert res.status_code == 302
    assert "login" in res.url
