from django.db import transaction
from django.utils import timezone
from apps.bookings.models import Booking
from apps.teams.models import TeamMembership

def remove_member(team, user):
    with transaction.atomic():
        membership = TeamMembership.objects.filter(team=team, user=user).first()
        if not membership:
            return
            
        membership.delete()
        
        # Cancel all pending future bookings for this team where this user is the host
        now = timezone.now()
        bookings = Booking.objects.filter(
            event_type__team=team, 
            host=user, 
            status__in=[Booking.StatusChoices.PENDING, Booking.StatusChoices.PENDING_PAYMENT, Booking.StatusChoices.CONFIRMED],
            start_at__gt=now
        )
        for b in bookings:
            b.status = Booking.StatusChoices.CANCELLED
            b.cancellation_reason = "Host is no longer with the team"
            # In a real app we would send cancellation emails here,
            # but that logic is usually handled by signals or explicitly called.
            b.save(update_fields=['status', 'cancellation_reason', 'updated_at'])

def delete_team(team):
    with transaction.atomic():
        team.is_active = False
        team.slug = f"deleted-{team.id}-{team.slug}"[:60] # Free up the slug
        team.save(update_fields=['is_active', 'slug', 'updated_at'])
        
        now = timezone.now()
        bookings = Booking.objects.filter(
            event_type__team=team, 
            status__in=[Booking.StatusChoices.PENDING, Booking.StatusChoices.PENDING_PAYMENT, Booking.StatusChoices.CONFIRMED],
            start_at__gt=now
        )
        for b in bookings:
            b.status = Booking.StatusChoices.CANCELLED
            b.cancellation_reason = "Team was deleted"
            b.save(update_fields=['status', 'cancellation_reason', 'updated_at'])
            
        # Deactivate all event types
        team.eventtype_set.update(is_active=False)
