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
    def get_host_and_event(self, host_slug, event_slug):
        host = User.objects.filter(slug__iexact=host_slug, is_active=True).first()
        if not host:
            raise Http404("Host not found")
        event = EventType.objects.filter(owner=host, slug__iexact=event_slug, is_active=True).first()
        if not event:
            raise Http404("Event not found")
        return host, event

    def post(self, request, host_slug, event_slug):
        host, event = self.get_host_and_event(host_slug, event_slug)
        
        # Simple IP rate limiting using Django cache
        ip_addr = request.META.get('REMOTE_ADDR', '')
        rate_key = f"rl_submit_{ip_addr}_{event.id}"
        attempts = cache.get(rate_key, 0)
        if attempts > 10:
            return HttpResponse("Too many requests. Please try again later.", status=429)
        cache.set(rate_key, attempts + 1, 60)
        
        # Are we submitting the form or requesting it?
        # If it's a POST from the calendar slot button, it only sends slot_time, tz, etc in query params, or URL, or body
        # Let's check if the form is being submitted (contains invitee_email)
        from .forms import BookingForm
        from django.core.signing import Signer
        import uuid
        from apps.bookings.services import create_booking, SlotUnavailable
        from apps.scheduling.engine import get_slots
        from apps.bookings.models import Booking
        from zoneinfo import ZoneInfo
        
        if 'invitee_email' in request.POST:
            # Form submission
            form = BookingForm(request.POST, event_type=event)
            if form.is_valid():
                idemp_token = form.cleaned_data['idempotency_token']
                cache_key = f"booking_idemp_{idemp_token}"
                booking_id = cache.get(cache_key)
                
                if booking_id:
                    # Idempotency hit: already processed
                    booking = Booking.objects.get(id=booking_id)
                else:
                    try:
                        booking = create_booking(
                            event_type=event,
                            start_at=form.cleaned_data['slot_time'],
                            invitee_name=form.cleaned_data['invitee_name'],
                            invitee_email=form.cleaned_data['invitee_email'],
                            invitee_timezone=form.cleaned_data['tz'],
                            answers=form.cleaned_data['answers'],
                            notes=form.cleaned_data['invitee_notes'],
                            guest_emails=form.cleaned_data['guest_emails'],
                            now=django_timezone.now()
                        )
                        cache.set(cache_key, booking.id, 86400)
                    except SlotUnavailable as e:
                        # Re-render slot picker with fresh slots
                        d = form.cleaned_data['slot_time'].astimezone(ZoneInfo(form.cleaned_data['tz'])).date()
                        day_slots = get_slots(event, d, d, django_timezone.now())
                        context = {
                            'host': host,
                            'event': event,
                            'visitor_tz': form.cleaned_data['tz'],
                            'day_slots': day_slots,
                            'selected_day': d.isoformat(),
                            'error_message': "Sorry, that time was just booked by someone else. Here are the remaining times for that day."
                        }
                        return render(request, "bookings/partials/slots.html", context, status=409)
                        
                # Return HX-Redirect header
                response = HttpResponse()
                
                from apps.bookings.tokens import make_manage_token
                token = make_manage_token(booking)
                response['HX-Redirect'] = f"/booking/{booking.uid}/?t={token}"
                return response
            else:
                # Re-render form with errors
                tz_str = request.POST.get('tz', 'UTC')
                try:
                    slot_time = datetime.fromisoformat(request.POST.get('slot_time', '').replace('Z', '+00:00'))
                except ValueError:
                    slot_time = django_timezone.now()
                    
                context = {
                    'host': host,
                    'event': event,
                    'form': form,
                    'visitor_tz': tz_str,
                    'slot_time': slot_time,
                }
                return render(request, "bookings/partials/booking_form.html", context)
        else:
            # Slot clicked, render empty form
            tz_str = request.GET.get('tz') or request.POST.get('tz', 'UTC')
            slot_time_str = request.GET.get('slot') or request.POST.get('slot', '')
            try:
                slot_time = datetime.fromisoformat(slot_time_str.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                raise Http404("Invalid slot")
                
            signer = Signer()
            initial = {
                'slot_time': slot_time_str,
                'tz': tz_str,
                'event_type_id': event.id,
                'timestamp_token': signer.sign(str(django_timezone.now().timestamp())),
                'idempotency_token': uuid.uuid4().hex
            }
            form = BookingForm(initial=initial, event_type=event)
            
            context = {
                'host': host,
                'event': event,
                'form': form,
                'visitor_tz': tz_str,
                'slot_time': slot_time,
            }
            return render(request, "bookings/partials/booking_form.html", context)

from django.shortcuts import get_object_or_404

class BookingConfirmationView(View):
    def get(self, request, uid):
        from apps.bookings.tokens import verify_manage_token
        token = request.GET.get('t', '')
        if not verify_manage_token(uid, token):
            raise Http404("Not found")
            
        from apps.bookings.models import Booking
        booking = get_object_or_404(Booking.objects.select_related('event_type', 'host'), uid=uid)
        
        # Calculate outlook/google calendar urls
        dtformat = "%Y%m%dT%H%M%SZ"
        start_utc = booking.start_at.strftime(dtformat)
        end_utc = booking.end_at.strftime(dtformat)
        title = f"Meeting with {booking.host.get_full_name() or booking.host.email}"
        details = f"Event: {booking.event_type.title}"
        location = booking.location_value or ""
        
        from urllib.parse import urlencode
        gcal_params = {
            'action': 'TEMPLATE',
            'text': title,
            'dates': f"{start_utc}/{end_utc}",
            'details': details,
            'location': location,
        }
        google_url = f"https://calendar.google.com/calendar/render?{urlencode(gcal_params)}"
        
        outlook_params = {
            'path': '/calendar/action/compose',
            'rru': 'addevent',
            'startdt': start_utc,
            'enddt': end_utc,
            'subject': title,
            'body': details,
            'location': location,
        }
        outlook_url = f"https://outlook.live.com/calendar/0/deeplink/compose?{urlencode(outlook_params)}"
        
        # Determine host timezone for display
        host_tz = booking.event_type.schedule.timezone if booking.event_type.schedule else booking.host.timezone
        
        context = {
            'booking': booking,
            'token': token,
            'google_url': google_url,
            'outlook_url': outlook_url,
            'host_tz': host_tz,
            'is_past': booking.start_at < django_timezone.now(),
        }
        return render(request, "bookings/confirmation.html", context)

class BookingICSView(View):
    def get(self, request, uid):
        from apps.bookings.tokens import verify_manage_token
        token = request.GET.get('t', '')
        if not verify_manage_token(uid, token):
            raise Http404("Not found")
            
        from apps.bookings.models import Booking
        booking = get_object_or_404(Booking.objects.select_related('event_type', 'host'), uid=uid)
        
        from apps.bookings.ics import generate_ics_for_booking
        ics_content = generate_ics_for_booking(booking)
        
        from django.http import HttpResponse
        response = HttpResponse(ics_content, content_type='text/calendar')
        response['Content-Disposition'] = f'attachment; filename="booking_{booking.uid}.ics"'
        return response

from django.utils import timezone as django_timezone
from django.shortcuts import redirect
from django.contrib.auth.mixins import LoginRequiredMixin

class BookingCancelView(View):
    def get(self, request, uid):
        from apps.bookings.tokens import verify_manage_token
        token = request.GET.get('t', '')
        if not verify_manage_token(uid, token):
            raise Http404("Not found")
            
        booking = get_object_or_404(Booking.objects.select_related('event_type', 'host'), uid=uid)
        
        if booking.status in [Booking.StatusChoices.CANCELLED, Booking.StatusChoices.REJECTED]:
            return redirect(f"/booking/{booking.uid}/?t={token}")
            
        context = {
            'booking': booking,
            'token': token,
            'cutoff_reached': False,
        }
        
        if booking.event_type.cancellation_cutoff_hours is not None:
            cutoff = booking.start_at - timedelta(hours=booking.event_type.cancellation_cutoff_hours)
            if django_timezone.now() > cutoff:
                context['cutoff_reached'] = True
                
        return render(request, "bookings/cancel.html", context)
        
    def post(self, request, uid):
        from apps.bookings.tokens import verify_manage_token
        token = request.GET.get('t', '')
        if not verify_manage_token(uid, token):
            raise Http404("Not found")
            
        booking = get_object_or_404(Booking.objects.select_related('event_type', 'host'), uid=uid)
        
        reason = request.POST.get('reason', '')
        
        from apps.bookings.services import cancel_booking, AlreadyCancelled, CancellationNotAllowed
        try:
            cancel_booking(
                booking=booking,
                cancelled_by="invitee",
                reason=reason,
                now=django_timezone.now()
            )
        except AlreadyCancelled:
            pass # just redirect
        except CancellationNotAllowed as e:
            return HttpResponse(str(e), status=403)
            
        return redirect(f"/booking/{booking.uid}/?t={token}")

class DashboardBookingCancelView(LoginRequiredMixin, View):
    def post(self, request, uid):
        booking = get_object_or_404(Booking, uid=uid, host=request.user)
        reason = request.POST.get('reason', '')
        
        from apps.bookings.services import cancel_booking, AlreadyCancelled
        try:
            cancel_booking(
                booking=booking,
                cancelled_by="host",
                reason=reason,
                now=django_timezone.now()
            )
        except AlreadyCancelled:
            pass
            
        # Redirect back to referring page, or dashboard
        next_url = request.META.get('HTTP_REFERER', '/dashboard/')
        return redirect(next_url)

class BookingRescheduleView(View):
    def get(self, request, uid):
        from apps.bookings.tokens import verify_manage_token
        token = request.GET.get('t', '')
        if not verify_manage_token(uid, token):
            raise Http404("Not found")
            
        booking = get_object_or_404(Booking.objects.select_related('event_type', 'host'), uid=uid)
        
        # Check live status
        if booking.status in [Booking.StatusChoices.CANCELLED, Booking.StatusChoices.REJECTED]:
            return redirect(f"/booking/{booking.uid}/?t={token}")
            
        event = booking.event_type
        host = booking.host
        
        tz_str = request.GET.get('tz') or booking.invitee_timezone
        if tz_str not in zoneinfo.available_timezones():
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
            
        # No caching for reschedule since it's user-specific exclusion
        fetch_start = first_day_of_month - timedelta(days=2)
        fetch_end = last_day_of_month + timedelta(days=2)
        month_slots = get_slots(event, fetch_start, fetch_end, now_utc, exclude_booking_id=booking.id)
        
        available_dates = set()
        for slot in month_slots:
            local_slot = slot.astimezone(visitor_tz)
            if local_slot.year == year and local_slot.month == month:
                available_dates.add(local_slot.date())
                
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
            raw_slots = get_slots(event, day_start, day_end, now_utc, exclude_booking_id=booking.id)
            
            day_slots = []
            for slot in raw_slots:
                if slot.astimezone(visitor_tz).date() == selected_date:
                    day_slots.append(slot)
        else:
            day_slots = []
            
        cal = calendar.Calendar(firstweekday=calendar.MONDAY)
        month_weeks = cal.monthdatescalendar(year, month)
        
        prev_month = month - 1 if month > 1 else 12
        prev_year = year if month > 1 else year - 1
        next_month = month + 1 if month < 12 else 1
        next_year = year if month < 12 else year + 1
        
        is_prev_disabled = (prev_year < now_visitor.year) or (prev_year == now_visitor.year and prev_month < now_visitor.month)
        
        context = {
            'booking': booking,
            'token': token,
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
                
        return render(request, "bookings/reschedule.html", context)

    def post(self, request, uid):
        from apps.bookings.tokens import verify_manage_token
        token = request.GET.get('t', '')
        if not verify_manage_token(uid, token):
            raise Http404("Not found")
            
        booking = get_object_or_404(Booking.objects.select_related('event_type', 'host'), uid=uid)
        
        reason = request.POST.get('reason', '')
        tz_str = request.POST.get('tz') or booking.invitee_timezone
        slot_time_str = request.POST.get('slot_time')
        
        try:
            slot_time = datetime.fromisoformat(slot_time_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return HttpResponse("Invalid slot time.", status=400)
            
        from apps.bookings.services import reschedule_booking, SlotUnavailable, ReschedulingNotAllowed, AlreadyCancelled
        try:
            new_booking = reschedule_booking(
                booking=booking,
                new_start_at=slot_time,
                rescheduled_by="invitee",
                reason=reason,
                now=django_timezone.now()
            )
        except AlreadyCancelled:
            return redirect(f"/booking/{booking.uid}/?t={token}")
        except ReschedulingNotAllowed as e:
            return HttpResponse(str(e), status=403)
        except SlotUnavailable as e:
            # Need to re-render the slots partial
            event = booking.event_type
            d = slot_time.astimezone(ZoneInfo(tz_str)).date()
            day_slots = get_slots(event, d, d, django_timezone.now(), exclude_booking_id=booking.id)
            context = {
                'host': booking.host,
                'event': event,
                'visitor_tz': tz_str,
                'day_slots': day_slots,
                'selected_day': d.isoformat(),
                'error_message': str(e)
            }
            return render(request, "bookings/partials/slots.html", context, status=409)
            
        # Success: redirect to new booking
        from apps.bookings.tokens import make_manage_token
        new_token = make_manage_token(new_booking)
        response = HttpResponse()
        response['HX-Redirect'] = f"/booking/{new_booking.uid}/?t={new_token}"
        return response

class DashboardBookingRescheduleView(LoginRequiredMixin, View):
    def post(self, request, uid):
        booking = get_object_or_404(Booking, uid=uid, host=request.user)
        reason = request.POST.get('reason', '')
        slot_time_str = request.POST.get('slot_time')
        
        try:
            slot_time = datetime.fromisoformat(slot_time_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return HttpResponse("Invalid slot time.", status=400)
        
        from apps.bookings.services import reschedule_booking, AlreadyCancelled
        try:
            new_booking = reschedule_booking(
                booking=booking,
                new_start_at=slot_time,
                rescheduled_by="host",
                reason=reason,
                now=django_timezone.now()
            )
        except AlreadyCancelled:
            pass
            
        next_url = request.META.get('HTTP_REFERER', '/dashboard/')
        return redirect(next_url)
