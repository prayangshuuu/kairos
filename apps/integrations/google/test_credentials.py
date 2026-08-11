import pytest
import datetime
import threading
from unittest.mock import patch
from django.utils import timezone

from apps.accounts.models import User
from apps.integrations.models import CalendarConnection, NotificationLog
from apps.integrations.google.credentials import get_valid_credentials
from apps.integrations.google.exceptions import TerminalGoogleApiError, TransientGoogleApiError
from apps.integrations.tasks import handle_terminal_connection_error, send_disconnection_email
from google.auth.exceptions import RefreshError

@pytest.fixture
def user():
    return User.objects.create_user(email="test@example.com", password="password")

@pytest.fixture
def connection(user):
    return CalendarConnection.objects.create(
        user=user,
        provider="google",
        external_account_id="12345",
        access_token="old_access_token",
        refresh_token="old_refresh_token",
        token_expires_at=timezone.now() + datetime.timedelta(minutes=30),
        is_active=True
    )

@pytest.mark.django_db(transaction=True)
def test_token_refresh_timing(connection):
    from google.oauth2.credentials import Credentials
    with patch.object(Credentials, 'refresh', autospec=True) as mock_refresh:
        creds = get_valid_credentials(connection)
        assert not mock_refresh.called
        assert creds.token == "old_access_token"

    connection.token_expires_at = timezone.now() + datetime.timedelta(minutes=2)
    connection.save()
    
    with patch.object(Credentials, 'refresh', autospec=True) as mock_refresh:
        def side_effect(self_creds, request):
            self_creds.token = "new_access_token"
            self_creds.expiry = (timezone.now() + datetime.timedelta(hours=1)).replace(tzinfo=None)
        
        mock_refresh.side_effect = side_effect
        creds = get_valid_credentials(connection)
        assert mock_refresh.called
        assert creds.token == "new_access_token"
        
        connection.refresh_from_db()
        assert connection.access_token == "new_access_token"

@pytest.mark.django_db(transaction=True)
def test_rotated_refresh_token_persisted(connection):
    connection.token_expires_at = timezone.now() + datetime.timedelta(minutes=2)
    connection.save()
    
    from google.oauth2.credentials import Credentials
    with patch.object(Credentials, 'refresh', autospec=True) as mock_refresh:
        def side_effect(self_creds, request):
            self_creds.token = "new_access_token"
            self_creds._refresh_token = "new_refresh_token"
            self_creds.expiry = (timezone.now() + datetime.timedelta(hours=1)).replace(tzinfo=None)
        mock_refresh.side_effect = side_effect
        
        creds = get_valid_credentials(connection)
        assert creds.refresh_token == "new_refresh_token"
        
        connection.refresh_from_db()
        assert connection.refresh_token == "new_refresh_token"

@pytest.mark.django_db(transaction=True)
def test_invalid_grant_deactivates_and_emails(connection):
    connection.token_expires_at = timezone.now() - datetime.timedelta(minutes=2)
    connection.save()
    
    from google.oauth2.credentials import Credentials
    with patch.object(Credentials, 'refresh', autospec=True) as mock_refresh, \
         patch('apps.integrations.tasks.send_disconnection_email.delay') as mock_delay:
        mock_refresh.side_effect = RefreshError("invalid_grant: Bad Request")
        
        with pytest.raises(TerminalGoogleApiError):
            get_valid_credentials(connection)
            
        connection.refresh_from_db()
        assert not connection.is_active
        assert "invalid_grant" in connection.last_error
        
        mock_delay.assert_called_once_with(connection.id)

@pytest.mark.django_db(transaction=True)
def test_second_invalid_grant_does_not_email_again(connection):
    # Call the actual celery task logic to test debouncing
    send_disconnection_email(connection.id)
    assert NotificationLog.objects.filter(connection=connection, kind="disconnection").count() == 1
    
    # Call again
    send_disconnection_email(connection.id)
    assert NotificationLog.objects.filter(connection=connection, kind="disconnection").count() == 1

@pytest.mark.django_db(transaction=True)
def test_429_transient_error(connection):
    connection.token_expires_at = timezone.now() - datetime.timedelta(minutes=2)
    connection.save()
    
    from google.oauth2.credentials import Credentials
    with patch.object(Credentials, 'refresh', autospec=True) as mock_refresh:
        mock_refresh.side_effect = RefreshError("HttpError 429 when requesting")
        
        with pytest.raises(TransientGoogleApiError):
            get_valid_credentials(connection)
            
        connection.refresh_from_db()
        assert connection.is_active

@pytest.mark.django_db(transaction=True)
def test_concurrent_refreshes_do_not_corrupt_token(connection):
    connection.token_expires_at = timezone.now() - datetime.timedelta(minutes=2)
    connection.save()
    
    refresh_count = 0
    
    def do_refresh():
        from django.db import connection as db_connection
        from google.oauth2.credentials import Credentials
        try:
            with patch.object(Credentials, 'refresh', autospec=True) as mock_refresh:
                def fake_refresh(self_creds, request):
                    nonlocal refresh_count
                    refresh_count += 1
                    self_creds.token = "new_access_token_concurrent"
                    self_creds.expiry = (timezone.now() + datetime.timedelta(hours=1)).replace(tzinfo=None)

                mock_refresh.side_effect = fake_refresh
                
                conn = CalendarConnection.objects.get(id=connection.id)
                get_valid_credentials(conn)
        finally:
            db_connection.close()

    t1 = threading.Thread(target=do_refresh)
    t2 = threading.Thread(target=do_refresh)
    
    t1.start()
    t2.start()
    
    t1.join()
    t2.join()
    
    assert refresh_count == 1
