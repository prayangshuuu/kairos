import pytest
from django.contrib.auth import get_user_model
from apps.clients.models import Client

User = get_user_model()

@pytest.mark.django_db
def test_client_creation():
    user = User.objects.create(email="test@example.com")
    client = Client.objects.create(
        host=user,
        name="John Doe",
        email="john@example.com",
    )
    assert client.name == "John Doe"
    assert client.email == "john@example.com"
    assert Client.objects.count() == 1

@pytest.mark.django_db
def test_client_scrub_pii():
    user = User.objects.create(email="scrubber@example.com")
    client = Client.objects.create(
        host=user,
        name="Jane Doe",
        email="jane@example.com",
        phone="555-1234",
        notes="Secret notes",
        status=Client.StatusChoices.ACTIVE
    )
    client.scrub_pii()
    
    assert client.name == "Anonymized Client"
    assert client.email == f"anonymized_{client.pk}@example.com"
    assert client.phone is None
    assert client.notes == ""
    assert client.status == Client.StatusChoices.ARCHIVED


