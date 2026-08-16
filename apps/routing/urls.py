from django.urls import path
from . import views

app_name = 'routing'

urlpatterns = [
    path('<str:owner_slug>/forms/<str:form_slug>/', views.public_routing_form_view, name='public_form'),
    path('<str:owner_slug>/forms/<str:form_slug>/embed/', views.public_routing_form_view, {'is_embed': True}, name='public_form_embed'),
    path('dashboard/routing-forms/<slug:slug>/embed/', views.RoutingFormEmbedCodeView.as_view(), name='form_embed_code'),
]
