from django.views import View
from django.shortcuts import redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from apps.teams.models import Team, TeamMembership
from apps.teams.forms import TeamForm

class SwitchContextView(LoginRequiredMixin, View):
    def post(self, request):
        team_id = request.POST.get("team_id")
        next_url = request.POST.get("next", "dashboard")
        
        if team_id:
            # Verify membership
            if request.user.team_memberships.filter(team_id=team_id, status='active').exists():
                request.session['active_team_id'] = int(team_id)
        else:
            # Switch to personal context
            if 'active_team_id' in request.session:
                del request.session['active_team_id']
                
        return redirect(next_url)

class TeamListView(LoginRequiredMixin, ListView):
    model = Team
    template_name = "teams/dashboard/team_list.html"
    context_object_name = "teams"

    def get_queryset(self):
        # Teams the user is a member of (or just owned teams if you want strict ownership)
        return Team.objects.filter(memberships__user=self.request.user, memberships__status='active').distinct()

class TeamCreateView(LoginRequiredMixin, CreateView):
    model = Team
    form_class = TeamForm
    template_name = "teams/dashboard/team_form.html"
    success_url = reverse_lazy("teams:team_list")

    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        # Create owner membership
        TeamMembership.objects.create(
            team=self.object, 
            user=self.request.user, 
            role='owner', 
            status='active'
        )
        return response

class TeamUpdateView(LoginRequiredMixin, UpdateView):
    model = Team
    form_class = TeamForm
    template_name = "teams/dashboard/team_form.html"
    success_url = reverse_lazy("teams:team_list")

    def get_queryset(self):
        # Only owners can edit
        return Team.objects.filter(owner=self.request.user)

class TeamDeleteView(LoginRequiredMixin, DeleteView):
    model = Team
    template_name = "teams/dashboard/team_confirm_delete.html"
    success_url = reverse_lazy("teams:team_list")

    def get_queryset(self):
        # Only owners can delete
        return Team.objects.filter(owner=self.request.user)
