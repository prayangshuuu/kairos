from django.shortcuts import render
from django.views.generic import DetailView
from django.http import Http404
from django.db.models import Prefetch
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_control

from apps.accounts.models import User
from apps.scheduling.models import EventType
from apps.analytics.tasks import record_page_view

@method_decorator(cache_control(public=True, max_age=60, stale_while_revalidate=300), name='dispatch')
class PublicProfileView(DetailView):
    model = User
    template_name = "bookings/public_profile.html"
    context_object_name = "host"
    slug_field = "slug"
    slug_url_kwarg = "slug"
    
    def get_object(self, queryset=None):
        slug = self.kwargs.get(self.slug_url_kwarg)
        if not slug:
            raise Http404("No slug provided")
            
        queryset = User.objects.filter(
            slug__iexact=slug, 
            is_active=True,
            slug__isnull=False
        ).prefetch_related(
            Prefetch(
                "event_types",
                queryset=EventType.objects.filter(is_active=True, is_hidden=False).order_by("created_at"),
                to_attr="public_event_types"
            )
        )
        
        obj = queryset.first()
        if not obj:
            raise Http404("User not found or inactive")
            
        return obj

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        
        referrer = request.META.get('HTTP_REFERER', '')
        record_page_view.delay(self.object.id, None, referrer)
        
        context = self.get_context_data(object=self.object)
        response = self.render_to_response(context)
        
        if hasattr(request, 'session'):
            request.session.accessed = False
            request.session.modified = False
            
        return response
