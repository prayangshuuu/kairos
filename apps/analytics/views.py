import csv
import json
from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView, View
from django.http import HttpResponse
from django.db.models import Sum
from django.db.models.functions import Coalesce

from .models import DailyMetric, BookingFunnelEvent

from apps.core.permissions import TeamContextMixin, ViewPermissionMixin, get_active_team

class DashboardInsightsView(TeamContextMixin, ViewPermissionMixin, TemplateView):
    template_name = "analytics/insights.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        host = self.request.user
        team = get_active_team(self.request)
        
        # Determine date range
        period = self.request.GET.get("period", "30d")
        now = timezone.now()
        today = now.date()
        
        if period == "7d":
            start_date = today - timedelta(days=7)
        elif period == "30d":
            start_date = today - timedelta(days=30)
        elif period == "90d":
            start_date = today - timedelta(days=90)
        elif period == "this_year":
            start_date = today.replace(month=1, day=1)
        elif period == "all_time":
            start_date = today - timedelta(days=3650) # 10 years
        else:
            start_date = today - timedelta(days=30)
            
        if team:
            metrics_qs = DailyMetric.objects.filter(team=team, date__gte=start_date, date__lte=today)
        else:
            metrics_qs = DailyMetric.objects.filter(host=host, team__isnull=True, date__gte=start_date, date__lte=today)
        
        # Aggregate totals
        totals = metrics_qs.aggregate(
            views=Coalesce(Sum('views'), 0),
            bookings_created=Coalesce(Sum('bookings_created'), 0),
            bookings_cancelled=Coalesce(Sum('bookings_cancelled'), 0),
            bookings_rescheduled=Coalesce(Sum('bookings_rescheduled'), 0),
            bookings_completed=Coalesce(Sum('bookings_completed'), 0),
            no_shows=Coalesce(Sum('no_shows'), 0),
            revenue_cents=Coalesce(Sum('revenue_cents'), 0),
            # Funnel totals
            profile_viewed=Coalesce(Sum('profile_viewed_count'), 0),
            booking_page_viewed=Coalesce(Sum('booking_page_viewed_count'), 0),
            date_selected=Coalesce(Sum('date_selected_count'), 0),
            slot_selected=Coalesce(Sum('slot_selected_count'), 0),
            form_started=Coalesce(Sum('form_started_count'), 0),
            form_submitted=Coalesce(Sum('form_submitted_count'), 0),
            payment_started=Coalesce(Sum('payment_started_count'), 0),
            booking_completed_count=Coalesce(Sum('booking_completed_count'), 0),
        )
        
        # Group by date for charts
        daily_data = metrics_qs.values('date').annotate(
            bookings=Coalesce(Sum('bookings_created'), 0),
            views=Coalesce(Sum('views'), 0)
        ).order_by('date')
        
        dates = []
        bookings_data = []
        views_data = []
        
        # Fill in missing dates
        current_date = start_date
        daily_dict = {item['date']: item for item in daily_data}
        
        while current_date <= today:
            dates.append(current_date.strftime("%b %d"))
            if current_date in daily_dict:
                bookings_data.append(daily_dict[current_date]['bookings'])
                views_data.append(daily_dict[current_date]['views'])
            else:
                bookings_data.append(0)
                views_data.append(0)
            current_date += timedelta(days=1)
            
        chart_data = {
            "dates": dates,
            "bookings": bookings_data,
            "views": views_data
        }
        
        # Calculate rates
        total_bookings = totals['bookings_created']
        completion_rate = round((totals['bookings_completed'] / total_bookings) * 100) if total_bookings else 0
        cancellation_rate = round((totals['bookings_cancelled'] / total_bookings) * 100) if total_bookings else 0
        no_show_rate = round((totals['no_shows'] / total_bookings) * 100) if total_bookings else 0
        
        bv = totals['booking_page_viewed']
        ds = totals['date_selected']
        fs = totals['form_started']
        fsub = totals['form_submitted']
        
        dropoff_to_date = round(((bv - ds) / bv) * 100) if bv else 0
        dropoff_to_form = round(((ds - fs) / ds) * 100) if ds else 0
        dropoff_to_submit = round(((fs - fsub) / fs) * 100) if fs else 0
        
        context.update({
            "period": period,
            "totals": totals,
            "chart_data_json": json.dumps(chart_data),
            "completion_rate": completion_rate,
            "cancellation_rate": cancellation_rate,
            "no_show_rate": no_show_rate,
            "dropoff_to_date": dropoff_to_date,
            "dropoff_to_form": dropoff_to_form,
            "dropoff_to_submit": dropoff_to_submit,
            "active_team": team,
        })
        return context

class ExportInsightsView(TeamContextMixin, ViewPermissionMixin, View):
    def get(self, request, *args, **kwargs):
        host = request.user
        team = get_active_team(request)
        period = request.GET.get("period", "30d")
        now = timezone.now()
        today = now.date()
        
        if period == "7d":
            start_date = today - timedelta(days=7)
        elif period == "30d":
            start_date = today - timedelta(days=30)
        elif period == "90d":
            start_date = today - timedelta(days=90)
        elif period == "this_year":
            start_date = today.replace(month=1, day=1)
        elif period == "all_time":
            start_date = today - timedelta(days=3650)
        else:
            start_date = today - timedelta(days=30)

        if team:
            metrics = DailyMetric.objects.filter(
                team=team, 
                date__gte=start_date, 
                date__lte=today
            ).order_by('-date')
        else:
            metrics = DailyMetric.objects.filter(
                host=host, 
                team__isnull=True,
                date__gte=start_date, 
                date__lte=today
            ).order_by('-date')

        response = HttpResponse(
            content_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="analytics_{period}.csv"'},
        )

        writer = csv.writer(response)
        writer.writerow([
            "Date", "Event Type", "Views", "Bookings Created", "Bookings Cancelled", 
            "Bookings Rescheduled", "Bookings Completed", "No Shows", "Revenue Cents"
        ])
        
        for m in metrics:
            writer.writerow([
                m.date,
                m.event_type.title if m.event_type else "All",
                m.views,
                m.bookings_created,
                m.bookings_cancelled,
                m.bookings_rescheduled,
                m.bookings_completed,
                m.no_shows,
                m.revenue_cents
            ])

        return response
