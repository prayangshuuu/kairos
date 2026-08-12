import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_pricing_page_renders_free_model(client):
    """Pricing page renders successfully and explains 100% free business model."""
    response = client.get(reverse("subscriptions:pricing"))
    assert response.status_code == 200
    assert "No Subscription Tiers" in response.content.decode()
    assert "100% Free" in response.content.decode()
