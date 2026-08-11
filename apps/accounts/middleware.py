import re
from django.shortcuts import redirect
from django.urls import reverse

class OnboardingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.user.slug:
            path = request.path_info
            
            # Allow allauth URLs, admin, static, media, onboarding itself, and the home page
            if not (
                path.startswith('/accounts/') or
                path.startswith('/admin/') or
                path.startswith('/static/') or
                path.startswith('/media/') or
                path.startswith('/onboarding/') or
                path == '/' or 
                path == reverse('home')
            ):
                return redirect('onboarding')
                
        return self.get_response(request)
