from django.urls import path
from . import views

app_name = 'demo'

urlpatterns = [
    path('app/', views.app_view, name='app'),
    path('public/', views.public_view, name='public'),
    path('partial/', views.partial_view, name='partial'),
]
