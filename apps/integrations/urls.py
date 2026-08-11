from django.urls import path
from . import views

app_name = 'integrations'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('google/connect/', views.google_connect, name='google_connect'),
    path('google/callback/', views.google_callback, name='google_callback'),
    path('google/disconnect/', views.google_disconnect, name='google_disconnect'),
    path('webhook/google/calendar/', views.google_webhook, name='google_webhook'),
]
