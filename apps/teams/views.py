from django.views import View
from django.shortcuts import redirect
from django.contrib.auth.mixins import LoginRequiredMixin

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
