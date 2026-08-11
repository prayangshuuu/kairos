import calendar
from datetime import date, datetime, timedelta, timezone
import zoneinfo
from django.core.cache import cache
from django.shortcuts import render
from django.views.generic import DetailView, View
from django.http import Http404, HttpResponse
from django.db.models import Prefetch
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_control
from django.utils import timezone as django_timezone

from apps.accounts.models import User
from apps.scheduling.models import EventType
from apps.analytics.tasks import record_page_view
from apps.scheduling.engine import get_slots

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


class BookingPageView(View):
    def get_host_and_event(self, host_slug, event_slug):
        host = User.objects.filter(slug__iexact=host_slug, is_active=True).first()
        if not host:
            raise Http404("Host not found")
        event = EventType.objects.filter(owner=host, slug__iexact=event_slug, is_active=True).first()
        if not event:
            raise Http404("Event not found")
        return host, event
        
    def get(self, request, host_slug, event_slug):
        host, event = self.get_host_and_event(host_slug, event_slug)
        
        tz_str = request.GET.get('tz')
        if not tz_str or tz_str not in zoneinfo.available_timezones():
            tz_str = 'UTC'
        visitor_tz = zoneinfo.ZoneInfo(tz_str)
        
        now_utc = django_timezone.now()
        now_visitor = now_utc.astimezone(visitor_tz)
        
        try:
            year = int(request.GET.get('year', now_visitor.year))
            month = int(request.GET.get('month', now_visitor.month))
            if not (1 <= month <= 12):
                raise ValueError
        except (ValueError, TypeError):
            year = now_visitor.year
            month = now_visitor.month
            
        try:
            day = int(request.GET.get('day'))
            selected_date = date(year, month, day)
        except (ValueError, TypeError):
            selected_date = None
            
        first_day_of_month = date(year, month, 1)
        if month == 12:
            last_day_of_month = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day_of_month = date(year, month + 1, 1) - timedelta(days=1)
            
        cache_key = f"avail_{event.id}_{year}_{month}_{tz_str}"
        available_dates = cache.get(cache_key)
        
        if available_dates is None:
            fetch_start = first_day_of_month - timedelta(days=2)
            fetch_end = last_day_of_month + timedelta(days=2)
            month_slots = get_slots(event, fetch_start, fetch_end, now_utc)
            
            available_dates = set()
            for slot in month_slots:
                local_slot = slot.astimezone(visitor_tz)
                if local_slot.year == year and local_slot.month == month:
                    available_dates.add(local_slot.date())
                    
            cache.set(cache_key, available_dates, 60)
            
        today_visitor = now_visitor.date()
        
        if not selected_date:
            future_avail = [d for d in available_dates if d >= today_visitor]
            if future_avail:
                selected_date = min(future_avail)
            else:
                selected_date = today_visitor
                
        if selected_date:
            day_start = selected_date - timedelta(days=2)
            day_end = selected_date + timedelta(days=2)
            raw_slots = get_slots(event, day_start, day_end, now_utc)
            
            day_slots = []
            for slot in raw_slots:
                if slot.astimezone(visitor_tz).date() == selected_date:
                    day_slots.append(slot)
        else:
            day_slots = []
            
        # Month grid respecting visitor week_start convention (default Monday)
        cal = calendar.Calendar(firstweekday=calendar.MONDAY)
        month_weeks = cal.monthdatescalendar(year, month)
        
        # Calculate prev and next month info
        prev_month = month - 1 if month > 1 else 12
        prev_year = year if month > 1 else year - 1
        next_month = month + 1 if month < 12 else 1
        next_year = year if month < 12 else year + 1
        
        # Disable previous if below current month
        is_prev_disabled = (prev_year < now_visitor.year) or (prev_year == now_visitor.year and prev_month < now_visitor.month)
        
        context = {
            'host': host,
            'event': event,
            'visitor_tz': tz_str,
            'year': year,
            'month': month,
            'month_name': calendar.month_name[month],
            'month_weeks': month_weeks,
            'available_dates': available_dates,
            'selected_date': selected_date,
            'day_slots': day_slots,
            'now_visitor': now_visitor,
            'today_visitor': today_visitor,
            'prev_year': prev_year,
            'prev_month': prev_month,
            'next_year': next_year,
            'next_month': next_month,
            'is_prev_disabled': is_prev_disabled,
            'all_timezones': zoneinfo.available_timezones()
        }
        
        partial = request.GET.get('partial')
        if request.headers.get('HX-Request'):
            if partial == 'calendar':
                return render(request, "bookings/partials/calendar.html", context)
            elif partial == 'slots':
                return render(request, "bookings/partials/slots.html", context)
            elif partial == 'tz_change':
                return render(request, "bookings/partials/booking_body.html", context)
                
        # Fire analytics task
        referrer = request.META.get('HTTP_REFERER', '')
        record_page_view.delay(host.id, event.id, referrer)
                
        return render(request, "bookings/booking_page.html", context)


class BookingStubView(View):
    def post(self, request, host_slug, event_slug):
        return HttpResponse("<div>Booking form partial will appear here in the next task.</div>")
