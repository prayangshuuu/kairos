import datetime
from django.test import TestCase
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.test.client import RequestFactory

from apps.accounts.models import User
from apps.teams.models import Team, TeamMembership
from apps.core.models import URLNamespace
from apps.teams.services import remove_member, delete_team
from apps.bookings.models import Booking
from apps.scheduling.models import EventType
from apps.core.permissions import get_active_team

class TeamTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="user@test.com", slug="user-slug")
        self.user2 = User.objects.create(email="user2@test.com", slug="user2-slug")
        self.team = Team.objects.create(name="Test Team", slug="team-slug", owner=self.user)
        TeamMembership.objects.create(team=self.team, user=self.user, role=TeamMembership.RoleChoices.OWNER, status=TeamMembership.StatusChoices.ACTIVE)
        
    def test_slug_collisions(self):
        # Test team can't use an existing user slug
        with self.assertRaises(ValidationError):
            team2 = Team(name="Another Team", slug="user-slug", owner=self.user)
            team2.full_clean()
            
        # Test user can't use an existing team slug
        with self.assertRaises(ValidationError):
            user3 = User(email="user3@test.com", slug="team-slug")
            user3.full_clean()
            
    def test_remove_member_cancels_bookings(self):
        TeamMembership.objects.create(team=self.team, user=self.user2, role=TeamMembership.RoleChoices.MEMBER, status=TeamMembership.StatusChoices.ACTIVE)
        et = EventType.objects.create(owner=self.user, team=self.team, title="Team Event", duration_minutes=30)
        
        now = timezone.now()
        # Create a pending booking for user2
        booking = Booking.objects.create(
            event_type=et,
            host=self.user2,
            team=self.team,
            start_at=now + datetime.timedelta(days=1),
            end_at=now + datetime.timedelta(days=1, minutes=30),
            invitee_email="invitee@test.com",
            status=Booking.StatusChoices.PENDING
        )
        
        # Remove user2
        remove_member(self.team, self.user2)
        
        # Verify membership deleted
        self.assertFalse(TeamMembership.objects.filter(team=self.team, user=self.user2).exists())
        
        # Verify booking cancelled
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.StatusChoices.CANCELLED)
        
    def test_delete_team(self):
        et = EventType.objects.create(owner=self.user, team=self.team, title="Team Event", duration_minutes=30)
        now = timezone.now()
        booking = Booking.objects.create(
            event_type=et,
            host=self.user,
            team=self.team,
            start_at=now + datetime.timedelta(days=1),
            end_at=now + datetime.timedelta(days=1, minutes=30),
            invitee_email="invitee@test.com",
            status=Booking.StatusChoices.PENDING
        )
        
        delete_team(self.team)
        
        self.team.refresh_from_db()
        self.assertFalse(self.team.is_active)
        self.assertNotEqual(self.team.slug, "team-slug")
        
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.StatusChoices.CANCELLED)
        
        et.refresh_from_db()
        self.assertFalse(et.is_active)

    def test_active_team_context(self):
        factory = RequestFactory()
        request = factory.get("/")
        request.user = self.user
        
        # Fake session
        from django.contrib.sessions.backends.db import SessionStore
        request.session = SessionStore()
        
        # No team set
        self.assertIsNone(get_active_team(request))
        
        # Valid team set
        request.session['active_team_id'] = self.team.id
        self.assertEqual(get_active_team(request), self.team)
        
        # Invalid team (not member)
        request.session['active_team_id'] = 999
        self.assertIsNone(get_active_team(request))
