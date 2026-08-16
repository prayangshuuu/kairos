from django.shortcuts import get_object_or_404
from django.core.exceptions import PermissionDenied
from django.http import Http404
from apps.teams.models import TeamMembership

def get_active_team(request):
    team_id = request.session.get('active_team_id')
    if not team_id:
        return None
    from apps.teams.models import Team
    # Ensure user has access
    if request.user.is_authenticated:
        try:
            return Team.objects.get(id=team_id, memberships__user=request.user, memberships__status='active')
        except Team.DoesNotExist:
            return None
    return None

def can_manage(user, obj):
    """
    Returns True if the user can manage the object (own it, or have admin/owner role in the team that owns it).
    """
    if hasattr(obj, 'owner_id') and obj.owner_id == user.id:
        return True
    
    if hasattr(obj, 'user_id') and obj.user_id == user.id:
        return True
        
    team = getattr(obj, 'team', None)
    if team:
        membership = TeamMembership.objects.filter(team=team, user=user, status='active').first()
        if membership and membership.role in [TeamMembership.RoleChoices.OWNER, TeamMembership.RoleChoices.ADMIN]:
            return True
            
        # For EventType explicitly: members can manage their assigned event types
        if membership and membership.role == TeamMembership.RoleChoices.MEMBER and hasattr(obj, 'hosts'):
            if obj.hosts.filter(user=user, is_active=True).exists():
                return True
                
    return False

def can_view(user, obj):
    """
    Returns True if the user can view the object.
    Members can view team objects even if they cannot edit them.
    """
    if can_manage(user, obj):
        return True
        
    team = getattr(obj, 'team', None)
    if team:
        if TeamMembership.objects.filter(team=team, user=user, status='active').exists():
            return True
            
    return False

class TeamContextMixin:
    """
    Mixin for views to set the team context and filter queries by the active context.
    """
    def get_queryset(self):
        qs = super().get_queryset()
        team = get_active_team(self.request)
        if team:
            if hasattr(qs.model, 'team'):
                return qs.filter(team=team)
            elif hasattr(qs.model, 'event_type'):
                return qs.filter(event_type__team=team)
        else:
            # Personal context
            if hasattr(qs.model, 'team'):
                qs = qs.filter(team__isnull=True)
            elif hasattr(qs.model, 'event_type'):
                qs = qs.filter(event_type__team__isnull=True)
                
            if hasattr(qs.model, 'owner'):
                return qs.filter(owner=self.request.user)
            elif hasattr(qs.model, 'user'):
                return qs.filter(user=self.request.user)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_team'] = get_active_team(self.request)
        if self.request.user.is_authenticated:
            context['my_teams'] = self.request.user.team_memberships.filter(status='active').select_related('team')
        return context

class ManagePermissionMixin:
    """
    Ensures the user can manage the object being accessed in DetailView/UpdateView/DeleteView.
    Throws 404 (not 403) to prevent data leaking.
    """
    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if not can_manage(self.request.user, obj):
            raise Http404("No such object")
        return obj

class ViewPermissionMixin:
    """
    Ensures the user can view the object being accessed in DetailView/UpdateView/DeleteView.
    Throws 404 (not 403).
    """
    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if not can_view(self.request.user, obj):
            raise Http404("No such object")
        return obj
