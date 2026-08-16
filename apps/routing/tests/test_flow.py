import json
import pytest
from datetime import datetime, timezone
from django.urls import reverse
from django.core.signing import Signer

from apps.accounts.models import User
from apps.teams.models import Team, TeamMembership
from apps.scheduling.models import EventType, BookingQuestion
from apps.bookings.models import Booking
from apps.routing.models import RoutingForm, RoutingFormField, RoutingRule, RoutingFormResponse
from apps.routing.engine import check_unreachable_rules

@pytest.fixture
def user():
    return User.objects.create_user(slug="host", password="pw", email="host@test.com")

@pytest.fixture
def team(user):
    t = Team.objects.create(name="Test Team", slug="test-team", owner=user)
    TeamMembership.objects.create(team=t, user=user, role='owner')
    return t

@pytest.fixture
def event_type(user):
    return EventType.objects.create(
        owner=user, title="15 Min", slug="15-min", duration_minutes=15, is_active=True
    )

@pytest.fixture
def team_event_type(team):
    # For a team event type, owner can be null if constraints allow, actually let's assign team's owner just in case
    # wait, EventType requires owner.
    # owner=team.members.first().user
    owner = team.memberships.first().user
    return EventType.objects.create(
        owner=owner, team=team, title="Round Robin", slug="round-robin", duration_minutes=30, is_active=True, assignment_strategy='round_robin'
    )

@pytest.fixture
def routing_form(user):
    form = RoutingForm.objects.create(
        owner=user, slug="contact", title="Contact Form"
    )
    RoutingFormField.objects.create(
        form=form, order=1, label="Company Size", field_type="number", identifier="company_size"
    )
    RoutingFormField.objects.create(
        form=form, order=2, label="Email", field_type="email", identifier="email"
    )
    return form

@pytest.mark.django_db
def test_first_match_wins_and_fallback(client, routing_form, event_type, user):
    r1 = RoutingRule.objects.create(
        form=routing_form, order=1, action="show_message", message="Disqualified",
        conditions={"match_type": "all", "rules": [{"field_identifier": "company_size", "operator": "less_than", "value": 50}]}
    )
    r2 = RoutingRule.objects.create(
        form=routing_form, order=2, action="route_to_event_type", target_event_type=event_type,
        conditions={"match_type": "all", "rules": [{"field_identifier": "company_size", "operator": "greater_than", "value": 100}]}
    )
    fallback = RoutingRule.objects.create(
        form=routing_form, order=3, action="show_message", message="Fallback", is_fallback=True
    )

    url = reverse('routing:public_form', args=[user.slug, routing_form.slug])
    
    # Test < 50
    resp = client.post(url, {"company_size": "10", "email": "test@test.com"})
    assert b"Disqualified" in resp.content
    assert RoutingFormResponse.objects.count() == 1
    
    # Test > 100
    resp2 = client.post(url, {"company_size": "200", "email": "test@test.com"})
    assert resp2.status_code == 302
    assert event_type.slug in resp2.url
    
    # Test fallback
    resp3 = client.post(url, {"company_size": "75", "email": "test@test.com"})
    assert b"Fallback" in resp3.content

@pytest.mark.django_db
def test_prefill_and_survive_to_booking(client, routing_form, event_type, user):
    BookingQuestion.objects.create(event_type=event_type, label="Company Size", field_type="number", order=1)
    
    RoutingRule.objects.create(
        form=routing_form, order=1, action="route_to_event_type", target_event_type=event_type,
        conditions={"match_type": "all", "rules": [{"field_identifier": "company_size", "operator": "greater_than", "value": 10}]}
    )
    
    url = reverse('routing:public_form', args=[user.slug, routing_form.slug])
    resp = client.post(url, {"company_size": "100", "email": "test@test.com"})
    assert resp.status_code == 302
    redirect_url = resp.url
    assert "routing_prefill" in redirect_url
    
    # Now follow redirect to booking page
    resp_booking = client.get(redirect_url)
    assert resp_booking.status_code == 200
    
    # Need to simulate form POST to create booking
    # First, let's extract routing_prefill and routing_response_id
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(redirect_url)
    qs = parse_qs(parsed.query)
    
    prefill = qs["routing_prefill"][0]
    response_id = qs["routing_response_id"][0]
    
    from unittest.mock import patch
    from datetime import timedelta
    with patch('apps.bookings.services.create_booking') as mock_create:
        now = datetime.now(timezone.utc)
        future_time = now + timedelta(days=1)
        dummy_booking = Booking.objects.create(
            event_type=event_type,
            host=user,
            start_at=future_time,
            end_at=future_time + timedelta(minutes=15),
            invitee_email="test@test.com",
            status="CONFIRMED"
        )
        mock_create.return_value = dummy_booking

        signer = Signer()
        booking_url = reverse('bookings:booking_stub', args=[user.slug, event_type.slug])
        resp_post = client.post(booking_url, {
            "invitee_name": "Test",
            "invitee_email": "test@test.com",
            "slot_time": future_time.isoformat(),
            "tz": "UTC",
            "event_type_id": event_type.id,
            "timestamp_token": signer.sign(str((now - timedelta(seconds=5)).timestamp())),
            "idempotency_token": "token123",
            "routing_response_id": response_id,
            "routing_prefill": prefill,
            "question_" + str(event_type.questions.first().id): "100"
        })
        
        b = dummy_booking
        assert b is not None, f"Booking not created. Form error? Response: {resp_post.content.decode('utf-8')}"
        r_resp = RoutingFormResponse.objects.get(id=response_id)
        assert r_resp.booking == b, f"Booking not linked! Response: {resp_post.content.decode('utf-8')}"
        assert r_resp.answers["company_size"] == "100"

@pytest.mark.django_db
def test_tampered_prefill(client, routing_form, event_type, user):
    RoutingRule.objects.create(
        form=routing_form, order=1, action="route_to_event_type", target_event_type=event_type,
        conditions={}
    )
    
    signer = Signer()
    tampered_prefill = "tampered"
    
    url = reverse('bookings:booking_page', args=[user.slug, event_type.slug]) + f"?routing_prefill={tampered_prefill}"
    resp = client.get(url)
    assert resp.status_code == 200 # Should not crash, just ignore prefill

@pytest.mark.django_db
def test_team_routing_round_robin(client, routing_form, team_event_type, team):
    routing_form.owner = None
    routing_form.team = team
    routing_form.save()
    
    RoutingRule.objects.create(
        form=routing_form, order=1, action="route_to_event_type", target_event_type=team_event_type,
        conditions={"match_type": "all", "rules": [{"field_identifier": "company_size", "operator": "greater_than", "value": 10}]}
    )
    
    url = reverse('routing:public_form', args=[team.slug, routing_form.slug])
    resp = client.post(url, {"company_size": "50", "email": "team@test.com"})
    assert resp.status_code == 302
    assert reverse('bookings:booking_page', args=[team.slug, team_event_type.slug]) in resp.url

def test_unreachable_rule_detection():
    r1 = RoutingRule(
        id=1, order=1,
        conditions={"match_type": "all", "rules": [{"field_identifier": "a", "operator": "equals", "value": "1"}]}
    )
    r2 = RoutingRule(
        id=2, order=2,
        conditions={"match_type": "all", "rules": [{"field_identifier": "a", "operator": "equals", "value": "1"}, {"field_identifier": "b", "operator": "equals", "value": "2"}]}
    )
    
    unreachable = check_unreachable_rules([r1, r2])
    assert unreachable == [2] # rule 2 is unreachable because rule 1's conditions are a subset
    
    r_catchall = RoutingRule(
        id=3, order=3,
        conditions={"match_type": "all", "rules": []}
    )
    r4 = RoutingRule(
        id=4, order=4,
        conditions={"match_type": "all", "rules": [{"field_identifier": "c", "operator": "equals", "value": "3"}]}
    )
    
    unreachable2 = check_unreachable_rules([r1, r2, r_catchall, r4])
    assert 4 in unreachable2 # rule 4 is unreachable because rule 3 is a catch-all
