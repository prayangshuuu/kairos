from django.urls import path
from .views import (
    EventTypeListView, EventTypeCreateView, EventTypeUpdateView,
    EventTypeDuplicateView, EventTypeToggleActiveView, EventTypeDeleteView,
    EventTypeCheckSlugView
)

app_name = "scheduling"

urlpatterns = [
    path('dashboard/event-types/', EventTypeListView.as_view(), name='eventtype_list'),
    path('dashboard/event-types/new/', EventTypeCreateView.as_view(), name='eventtype_create'),
    path('dashboard/event-types/check-slug/', EventTypeCheckSlugView.as_view(), name='eventtype_check_slug'),
    path('dashboard/event-types/<slug:slug>/', EventTypeUpdateView.as_view(), name='eventtype_edit'),
    path('dashboard/event-types/<slug:slug>/duplicate/', EventTypeDuplicateView.as_view(), name='eventtype_duplicate'),
    path('dashboard/event-types/<slug:slug>/toggle-active/', EventTypeToggleActiveView.as_view(), name='eventtype_toggle_active'),
    path('dashboard/event-types/<slug:slug>/delete/', EventTypeDeleteView.as_view(), name='eventtype_delete'),
]
