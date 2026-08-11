from django.shortcuts import render
from django.contrib import messages
from django.template.response import TemplateResponse

def app_view(request):
    messages.info(request, "Welcome to the dashboard layout demo!")
    return render(request, "demo/app_demo.html")

def public_view(request):
    messages.success(request, "Welcome to the public layout demo!")
    return render(request, "demo/public_demo.html")

def partial_view(request):
    messages.success(request, "This is an HTMX partial toast message!")
    return render(request, "partials/_demo_content.html")

def custom_404(request, exception=None):
    return render(request, '404.html', status=404)

def custom_500(request):
    return render(request, '500.html', status=500)
