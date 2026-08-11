import pytest
from django.urls import reverse
from django.core.signing import TimestampSigner
from apps.integrations.models import CalendarConnection
from apps.accounts.models import User

@pytest.mark.django_db
def test_google_callback_rejects_tampered_state(client):
    # Create and login a user
    user = User.objects.create_user(
        email='testuser@example.com', 
        password='password123',
        slug='testuser'
    )
    client.force_login(user)
    
    # Tampered state
    tampered_state = "tampered_state_value"
    
    url = reverse('integrations:google_callback')
    response = client.get(url, {'state': tampered_state, 'code': 'fake_code'})
    
    # Should redirect back to dashboard due to error
    assert response.status_code == 302
    assert response.url == reverse('integrations:dashboard')
    
    # Ensure no connection was created
    assert CalendarConnection.objects.count() == 0

from unittest.mock import patch
from django.core.signing import SignatureExpired
import time

@pytest.mark.django_db
def test_google_callback_rejects_expired_state(client):
    user = User.objects.create_user(
        email='testuser2@example.com', 
        password='password123',
        slug='testuser2'
    )
    client.force_login(user)
    
    # Create an expired state manually
    # State string format: value:timestamp:signature
    signer = TimestampSigner()
    
    # We mock time.time so it thinks we are signing it in the past
    with patch('time.time', return_value=time.time() - 601):
        expired_state = signer.sign(str(user.id))
        
    url = reverse('integrations:google_callback')
    response = client.get(url, {'state': expired_state, 'code': 'fake_code'})
    
    assert response.status_code == 302
    assert response.url == reverse('integrations:dashboard')
    assert CalendarConnection.objects.count() == 0

@pytest.mark.django_db
@patch('apps.integrations.views.requests.post')
@patch('apps.integrations.views.requests.get')
def test_google_callback_accepts_superset_scopes(mock_get, mock_post, client):
    user = User.objects.create_user(
        email='testuser3@example.com', 
        password='password123',
        slug='testuser3'
    )
    client.force_login(user)
    
    signer = TimestampSigner()
    state = signer.sign(str(user.id))
    
    # Mock token response with an extra scope
    mock_post.return_value.ok = True
    mock_post.return_value.json.return_value = {
        'access_token': 'fake_access',
        'refresh_token': 'fake_refresh',
        'expires_in': 3600,
        'scope': 'https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/userinfo.profile'
    }
    
    # Mock profile response
    mock_get.return_value.ok = True
    mock_get.return_value.json.return_value = {
        'id': 'test_calendar_id@example.com'
    }
    
    url = reverse('integrations:google_callback')
    response = client.get(url, {'state': state, 'code': 'fake_code'})
    
    assert response.status_code == 302
    assert response.url == reverse('integrations:dashboard')
    
    # Should create connection
    assert CalendarConnection.objects.count() == 1
    conn = CalendarConnection.objects.first()
    assert conn.external_account_id == 'test_calendar_id@example.com'
