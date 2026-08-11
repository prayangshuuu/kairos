import zoneinfo
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from .models import User
from .validators import validate_slug
from django.core.exceptions import ValidationError

def home(request):
    if request.user.is_authenticated:
        if not request.user.slug:
            return redirect('onboarding')
        return redirect('dashboard')
    return render(request, 'home.html')

@login_required
def dashboard(request):
    return render(request, 'dashboard.html')

@login_required
def onboarding(request):
    if request.user.slug:
        return redirect('dashboard')
        
    if request.method == 'POST':
        slug = request.POST.get('slug', '').lower()
        display_name = request.POST.get('display_name', '')
        timezone = request.POST.get('timezone', 'UTC')
        
        try:
            validate_slug(slug)
            if User.objects.filter(slug=slug).exclude(id=request.user.id).exists():
                raise ValidationError("Slug is taken.")
            
            request.user.slug = slug
            request.user.display_name = display_name
            request.user.timezone = timezone
            request.user.save()
            return redirect('dashboard')
        except ValidationError as e:
            pass # Handle error in UI
            
    timezones = sorted(list(zoneinfo.available_timezones()))
    
    # Pre-fill suggestions
    suggestion = request.user.display_name
    if not suggestion and request.user.first_name:
        suggestion = f"{request.user.first_name} {request.user.last_name}".strip()
    if not suggestion:
        suggestion = request.user.email.split('@')[0]
        
    base_slug = "".join(c if c.isalnum() else "-" for c in suggestion).strip("-").lower()
    
    suggested_slug = base_slug
    counter = 1
    while User.objects.filter(slug=suggested_slug).exists():
        suggested_slug = f"{base_slug}-{counter}"
        counter += 1
        
    return render(request, 'accounts/onboarding.html', {
        'timezones': timezones,
        'suggested_slug': suggested_slug,
    })

@login_required
def check_slug(request):
    slug = request.GET.get('slug', '').lower()
    
    if not slug:
        return HttpResponse("")
        
    try:
        validate_slug(slug)
    except ValidationError as e:
        return HttpResponse(f"<span class='text-red-500 text-sm'>{e.message}</span>")
        
    if User.objects.filter(slug=slug).exclude(id=request.user.id).exists():
        return HttpResponse("<span class='text-red-500 text-sm'>Unavailable</span>")
        
    return HttpResponse("<span class='text-green-500 text-sm'>Available</span>")
