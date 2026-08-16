from django.urls import path
from . import views

app_name = 'routing'

urlpatterns = [
    path('<str:owner_slug>/forms/<str:form_slug>/', views.public_routing_form_view, name='public_form'),
]
