import calendar
import zoneinfo
from datetime import UTC, date, datetime, timedelta

from django.core.cache import cache
from django.db.models import Prefetch
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone as django_timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_control
from django.views.generic import DetailView, View

from apps.accounts.models import User
from apps.bookings.models import Booking, WaitlistEntry
from apps.scheduling.engine import get_slots
from apps.scheduling.models import EventType


@method_decorator(
    cache_control(public=True, max_age=60, stale_while_revalidate=300), name="dispatch"
)
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
            slug__iexact=slug, is_active=True, slug__isnull=False
        ).prefetch_related(
            Prefetch(
                "event_types",
                queryset=EventType.objects.filter(is_active=True, is_hidden=False).order_by(
                    "created_at"
                ),
                to_attr="public_event_types",
            )
        )

        obj = queryset.first()
        if not obj:
            from apps.accounts.models import UserSlugHistory

            history = UserSlugHistory.objects.filter(old_slug__iexact=slug).first()
            if history and history.user.slug:
                from django.http import HttpResponseRedirect
                from django.urls import reverse

                return HttpResponseRedirect(
                    reverse("bookings:public_profile", kwargs={"slug": history.user.slug})
                )
            raise Http404("User not found or inactive")

        return obj

    def get(self, request, *args, **kwargs):
        obj_or_redirect = self.get_object()
        from django.http import HttpResponseRedirect

        if isinstance(obj_or_redirect, HttpResponseRedirect):
            return obj_or_redirect

        self.object = obj_or_redirect

        from apps.analytics.utils import track_funnel_event, set_funnel_cookie
        session_id = track_funnel_event(request, self.object.id, "profile_viewed", None)

        context = self.get_context_data(object=self.object)
        response = self.render_to_response(context)

        if hasattr(request, "session"):
            request.session.accessed = False
            request.session.modified = False

        return set_funnel_cookie(response, session_id)


class BookingPageView(View):
    def get_host_and_event(self, host_slug, event_slug):
        host = User.objects.filter(slug__iexact=host_slug, is_active=True).first()
        if not host:
            raise Http404("Host not found")
        event = EventType.objects.filter(
            owner=host, slug__iexact=event_slug, is_active=True
        ).first()
        if not event:
            raise Http404("Event not found")
        return host, event

    def get(self, request, host_slug, event_slug):
        host, event = self.get_host_and_event(host_slug, event_slug)

        tz_str = request.GET.get("tz")
        if not tz_str or tz_str not in zoneinfo.available_timezones():
            tz_str = "UTC"
        visitor_tz = zoneinfo.ZoneInfo(tz_str)

        now_utc = django_timezone.now()
        now_visitor = now_utc.astimezone(visitor_tz)

        try:
            year = int(request.GET.get("year", now_visitor.year))
            month = int(request.GET.get("month", now_visitor.month))
            if not (1 <= month <= 12):
                raise ValueError
        except (ValueError, TypeError):
            year = now_visitor.year
            month = now_visitor.month

        try:
            day = int(request.GET.get("day"))
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
            from apps.integrations.services import fetch_external_busy

            u_start = datetime.combine(fetch_start, datetime.min.time(), tzinfo=UTC)
            u_end = datetime.combine(fetch_end, datetime.max.time(), tzinfo=UTC)
            external_busy = fetch_external_busy(host, u_start, u_end)
            month_slots = get_slots(
                event, fetch_start, fetch_end, now_utc, external_busy=external_busy
            )

            available_dates = set()
            for slot in month_slots:
                local_slot = slot.astimezone(visitor_tz)
                if local_slot.year == year and local_slot.month == month:
                    available_dates.add(local_slot.date())

            cache.set(cache_key, available_dates, 60)

        today_visitor = now_visitor.date()

        if not selected_date:
            future_avail = [d for d in available_dates if d >= today_visitor]
            selected_date = min(future_avail) if future_avail else today_visitor

        if selected_date:
            day_start = selected_date - timedelta(days=2)
            day_end = selected_date + timedelta(days=2)
            from apps.integrations.services import fetch_external_busy

            u_start = datetime.combine(day_start, datetime.min.time(), tzinfo=UTC)
            u_end = datetime.combine(day_end, datetime.max.time(), tzinfo=UTC)
            external_busy = fetch_external_busy(host, u_start, u_end)
            raw_slots = get_slots(event, day_start, day_end, now_utc, external_busy=external_busy)

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
        is_prev_disabled = (prev_year < now_visitor.year) or (
            prev_year == now_visitor.year and prev_month < now_visitor.month
        )

        offer_waitlist = False
        if event.waitlist_enabled:
            future_avail = [d for d in available_dates if d >= today_visitor]
            no_slots = (not future_avail) or (selected_date and not day_slots)
            if no_slots:
                from apps.bookings.models import WaitlistEntry
                current_size = WaitlistEntry.objects.filter(
                    event_type=event, status=WaitlistEntry.StatusChoices.WAITING
                ).count()
                if event.waitlist_max_size is None or current_size < event.waitlist_max_size:
                    offer_waitlist = True

        context = {
            "host": host,
            "event": event,
            "visitor_tz": tz_str,
            "year": year,
            "month": month,
            "month_name": calendar.month_name[month],
            "month_weeks": month_weeks,
            "available_dates": available_dates,
            "selected_date": selected_date,
            "day_slots": day_slots,
            "now_visitor": now_visitor,
            "today_visitor": today_visitor,
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
            "is_prev_disabled": is_prev_disabled,
            "offer_waitlist": offer_waitlist,
            "all_timezones": zoneinfo.available_timezones(),
        }

        partial = request.GET.get("partial")
        if request.headers.get("HX-Request"):
            if partial == "calendar":
                return render(request, "bookings/partials/calendar.html", context)
            elif partial == "slots":
                from apps.analytics.utils import track_funnel_event
                track_funnel_event(request, host.id, "date_selected", event.id)
                return render(request, "bookings/partials/slots.html", context)
            elif partial == "tz_change":
                return render(request, "bookings/partials/booking_body.html", context)

        # Fire analytics task
        from apps.analytics.utils import track_funnel_event, set_funnel_cookie
        session_id = track_funnel_event(request, host.id, "booking_page_viewed", event.id)

        response = render(request, "bookings/booking_page.html", context)
        return set_funnel_cookie(response, session_id)


class BookingStubView(View):
    def get_host_and_event(self, host_slug, event_slug):
        host = User.objects.filter(slug__iexact=host_slug, is_active=True).first()
        if not host:
            raise Http404("Host not found")
        event = EventType.objects.filter(
            owner=host, slug__iexact=event_slug, is_active=True
        ).first()
        if not event:
            raise Http404("Event not found")
        return host, event

    def post(self, request, host_slug, event_slug):
        host, event = self.get_host_and_event(host_slug, event_slug)

        # Simple IP rate limiting using Django cache
        ip_addr = request.META.get("REMOTE_ADDR", "")
        rate_key = f"rl_submit_{ip_addr}_{event.id}"
        attempts = cache.get(rate_key, 0)
        if attempts > 10:
            return HttpResponse("Too many requests. Please try again later.", status=429)
        cache.set(rate_key, attempts + 1, 60)

        # Are we submitting the form or requesting it?
        # If it's a POST from the calendar slot button, it only sends slot_time, tz, etc in query params, or URL, or body
        # Let's check if the form is being submitted (contains invitee_email)
        import uuid
        from zoneinfo import ZoneInfo

        from django.core.signing import Signer

        from apps.bookings.models import Booking
        from apps.bookings.services import SlotUnavailable, create_booking
        from apps.scheduling.engine import get_slots

        from .forms import BookingForm

        if "invitee_email" in request.POST:
            # Form submission
            from apps.analytics.utils import track_funnel_event
            track_funnel_event(request, host.id, "form_submitted", event.id)
            
            form = BookingForm(request.POST, event_type=event)
            if form.is_valid():
                idemp_token = form.cleaned_data["idempotency_token"]
                cache_key = f"booking_idemp_{idemp_token}"
                booking_id = cache.get(cache_key)

                if booking_id:
                    # Idempotency hit: already processed
                    booking = Booking.objects.get(id=booking_id)
                else:
                    try:
                        booking = create_booking(
                            event_type=event,
                            start_at=form.cleaned_data["slot_time"],
                            invitee_name=form.cleaned_data["invitee_name"],
                            invitee_email=form.cleaned_data["invitee_email"],
                            invitee_timezone=form.cleaned_data["tz"],
                            answers=form.cleaned_data["answers"],
                            notes=form.cleaned_data["invitee_notes"],
                            guest_emails=form.cleaned_data["guest_emails"],
                            now=django_timezone.now(),
                        )
                        cache.set(cache_key, booking.id, 86400)
                    except SlotUnavailable:
                        # Re-render slot picker with fresh slots
                        d = (
                            form.cleaned_data["slot_time"]
                            .astimezone(ZoneInfo(form.cleaned_data["tz"]))
                            .date()
                        )
                        from apps.integrations.services import fetch_external_busy

                        u_start = datetime.combine(
                            d - timedelta(days=1), datetime.min.time(), tzinfo=UTC
                        )
                        u_end = datetime.combine(
                            d + timedelta(days=1), datetime.max.time(), tzinfo=UTC
                        )
                        external_busy = fetch_external_busy(host, u_start, u_end)
                        day_slots = get_slots(
                            event, d, d, django_timezone.now(), external_busy=external_busy
                        )
                        context = {
                            "host": host,
                            "event": event,
                            "visitor_tz": form.cleaned_data["tz"],
                            "day_slots": day_slots,
                            "selected_day": d.isoformat(),
                            "error_message": "Sorry, that time was just booked by someone else. Here are the remaining times for that day.",
                        }
                        return render(request, "bookings/partials/slots.html", context, status=409)

                if booking.status == Booking.StatusChoices.PENDING_PAYMENT:
                    track_funnel_event(request, host.id, "payment_started", event.id)
                    from apps.payments.routing import select_provider
                    from apps.payments.services import create_payment_for_booking

                    payment = create_payment_for_booking(booking=booking)
                    provider = select_provider(booking.event_type)

                    from django.urls import reverse

                    success_url = request.build_absolute_uri(reverse("payments:payment_return"))
                    cancel_url = request.build_absolute_uri(
                        f"{reverse('payments:payment_cancel')}?payment_uid={payment.uid}"
                    )

                    checkout_result = provider.create_checkout(payment, success_url, cancel_url)

                    # Update payment with external_session_id so PaymentReturnView can find it
                    payment.external_session_id = checkout_result.session_id
                    payment.save(update_fields=["external_session_id"])

                    response = HttpResponse()
                    response["HX-Redirect"] = checkout_result.redirect_url
                    return response
                else:
                    track_funnel_event(request, host.id, "booking_completed", event.id)
                    # Return HX-Redirect header
                    response = HttpResponse()

                    from apps.bookings.tokens import make_manage_token

                    token = make_manage_token(booking)
                    response["HX-Redirect"] = f"/booking/{booking.uid}/?t={token}"
                    return response
            else:
                # Re-render form with errors
                tz_str = request.POST.get("tz", "UTC")
                try:
                    slot_time = datetime.fromisoformat(
                        request.POST.get("slot_time", "").replace("Z", "+00:00")
                    )
                except ValueError:
                    slot_time = django_timezone.now()

                context = {
                    "host": host,
                    "event": event,
                    "form": form,
                    "visitor_tz": tz_str,
                    "slot_time": slot_time,
                }
                return render(request, "bookings/partials/booking_form.html", context)
        else:
            # Slot clicked, render empty form
            from apps.analytics.utils import track_funnel_event
            track_funnel_event(request, host.id, "slot_selected", event.id)
            track_funnel_event(request, host.id, "form_started", event.id)
            
            tz_str = request.GET.get("tz") or request.POST.get("tz", "UTC")
            slot_time_str = request.GET.get("slot") or request.POST.get("slot", "")
            try:
                slot_time = datetime.fromisoformat(slot_time_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                raise Http404("Invalid slot")

            signer = Signer()
            initial = {
                "slot_time": slot_time_str,
                "tz": tz_str,
                "event_type_id": event.id,
                "timestamp_token": signer.sign(str(django_timezone.now().timestamp())),
                "idempotency_token": uuid.uuid4().hex,
            }
            form = BookingForm(initial=initial, event_type=event)

            context = {
                "host": host,
                "event": event,
                "form": form,
                "visitor_tz": tz_str,
                "slot_time": slot_time,
            }
            return render(request, "bookings/partials/booking_form.html", context)


from django.shortcuts import get_object_or_404


class BookingConfirmationView(View):
    def get(self, request, uid):
        from apps.bookings.tokens import verify_manage_token

        token = request.GET.get("t", "")
        if not verify_manage_token(uid, token):
            raise Http404("Not found")

        from apps.bookings.models import Booking

        booking = get_object_or_404(Booking.objects.select_related("event_type", "host"), uid=uid)

        # Calculate outlook/google calendar urls
        dtformat = "%Y%m%dT%H%M%SZ"
        start_utc = booking.start_at.strftime(dtformat)
        end_utc = booking.end_at.strftime(dtformat)
        title = f"Meeting with {booking.host.get_full_name() or booking.host.email}"
        details = f"Event: {booking.event_type.title}"
        location = booking.location_value or ""

        from urllib.parse import urlencode

        gcal_params = {
            "action": "TEMPLATE",
            "text": title,
            "dates": f"{start_utc}/{end_utc}",
            "details": details,
            "location": location,
        }
        google_url = f"https://calendar.google.com/calendar/render?{urlencode(gcal_params)}"

        outlook_params = {
            "path": "/calendar/action/compose",
            "rru": "addevent",
            "startdt": start_utc,
            "enddt": end_utc,
            "subject": title,
            "body": details,
            "location": location,
        }
        outlook_url = (
            f"https://outlook.live.com/calendar/0/deeplink/compose?{urlencode(outlook_params)}"
        )

        # Determine host timezone for display
        host_tz = (
            booking.event_type.schedule.timezone
            if booking.event_type.schedule
            else booking.host.timezone
        )

        context = {
            "booking": booking,
            "token": token,
            "google_url": google_url,
            "outlook_url": outlook_url,
            "host_tz": host_tz,
            "is_past": booking.start_at < django_timezone.now(),
        }
        return render(request, "bookings/confirmation.html", context)


class BookingICSView(View):
    def get(self, request, uid):
        from apps.bookings.tokens import verify_manage_token

        token = request.GET.get("t", "")
        if not verify_manage_token(uid, token):
            raise Http404("Not found")

        from apps.bookings.models import Booking

        booking = get_object_or_404(Booking.objects.select_related("event_type", "host"), uid=uid)

        from apps.bookings.ics import generate_ics_for_booking

        ics_content = generate_ics_for_booking(booking)

        from django.http import HttpResponse

        response = HttpResponse(ics_content, content_type="text/calendar")
        response["Content-Disposition"] = f'attachment; filename="booking_{booking.uid}.ics"'
        return response


import contextlib

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect


class BookingCancelView(View):
    def get(self, request, uid):
        from apps.bookings.tokens import verify_manage_token

        token = request.GET.get("t", "")
        if not verify_manage_token(uid, token):
            raise Http404("Not found")

        booking = get_object_or_404(Booking.objects.select_related("event_type", "host"), uid=uid)

        if booking.status in [Booking.StatusChoices.CANCELLED, Booking.StatusChoices.REJECTED]:
            return redirect(f"/booking/{booking.uid}/?t={token}")

        context = {
            "booking": booking,
            "token": token,
            "cutoff_reached": False,
        }

        if booking.event_type.cancellation_cutoff_hours is not None:
            cutoff = booking.start_at - timedelta(
                hours=booking.event_type.cancellation_cutoff_hours
            )
            if django_timezone.now() > cutoff:
                context["cutoff_reached"] = True

        return render(request, "bookings/cancel.html", context)

    def post(self, request, uid):
        from apps.bookings.tokens import verify_manage_token

        token = request.GET.get("t", "")
        if not verify_manage_token(uid, token):
            raise Http404("Not found")

        booking = get_object_or_404(Booking.objects.select_related("event_type", "host"), uid=uid)

        reason = request.POST.get("reason", "")

        from apps.bookings.services import AlreadyCancelled, CancellationNotAllowed, cancel_booking

        try:
            cancel_booking(
                booking=booking, cancelled_by="invitee", reason=reason, now=django_timezone.now()
            )
        except AlreadyCancelled:
            pass  # just redirect
        except CancellationNotAllowed as e:
            return HttpResponse(str(e), status=403)

        return redirect(f"/booking/{booking.uid}/?t={token}")


class DashboardBookingCancelView(LoginRequiredMixin, View):
    def post(self, request, uid):
        booking = get_object_or_404(Booking, uid=uid, host=request.user)
        reason = request.POST.get("reason", "")

        from apps.bookings.services import AlreadyCancelled, cancel_booking

        with contextlib.suppress(AlreadyCancelled):
            cancel_booking(
                booking=booking, cancelled_by="host", reason=reason, now=django_timezone.now()
            )

        # Redirect back to referring page, or dashboard
        next_url = request.META.get("HTTP_REFERER", "/dashboard/")
        return redirect(next_url)


class BookingRescheduleView(View):
    def get(self, request, uid):
        from apps.bookings.tokens import verify_manage_token

        token = request.GET.get("t", "")
        if not verify_manage_token(uid, token):
            raise Http404("Not found")

        booking = get_object_or_404(Booking.objects.select_related("event_type", "host"), uid=uid)

        # Check live status
        if booking.status in [Booking.StatusChoices.CANCELLED, Booking.StatusChoices.REJECTED]:
            return redirect(f"/booking/{booking.uid}/?t={token}")

        event = booking.event_type
        host = booking.host

        tz_str = request.GET.get("tz") or booking.invitee_timezone
        if tz_str not in zoneinfo.available_timezones():
            tz_str = "UTC"
        visitor_tz = zoneinfo.ZoneInfo(tz_str)

        now_utc = django_timezone.now()
        now_visitor = now_utc.astimezone(visitor_tz)

        try:
            year = int(request.GET.get("year", now_visitor.year))
            month = int(request.GET.get("month", now_visitor.month))
            if not (1 <= month <= 12):
                raise ValueError
        except (ValueError, TypeError):
            year = now_visitor.year
            month = now_visitor.month

        try:
            day = int(request.GET.get("day"))
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
        from apps.integrations.services import fetch_external_busy

        u_start = datetime.combine(fetch_start, datetime.min.time(), tzinfo=UTC)
        u_end = datetime.combine(fetch_end, datetime.max.time(), tzinfo=UTC)
        external_busy = fetch_external_busy(host, u_start, u_end)
        month_slots = get_slots(
            event,
            fetch_start,
            fetch_end,
            now_utc,
            external_busy=external_busy,
            exclude_booking_id=booking.id,
        )

        available_dates = set()
        for slot in month_slots:
            local_slot = slot.astimezone(visitor_tz)
            if local_slot.year == year and local_slot.month == month:
                available_dates.add(local_slot.date())

        today_visitor = now_visitor.date()

        if not selected_date:
            future_avail = [d for d in available_dates if d >= today_visitor]
            selected_date = min(future_avail) if future_avail else today_visitor

        if selected_date:
            day_start = selected_date - timedelta(days=2)
            day_end = selected_date + timedelta(days=2)
            from apps.integrations.services import fetch_external_busy

            u_start = datetime.combine(day_start, datetime.min.time(), tzinfo=UTC)
            u_end = datetime.combine(day_end, datetime.max.time(), tzinfo=UTC)
            external_busy = fetch_external_busy(host, u_start, u_end)
            raw_slots = get_slots(
                event,
                day_start,
                day_end,
                now_utc,
                external_busy=external_busy,
                exclude_booking_id=booking.id,
            )

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

        is_prev_disabled = (prev_year < now_visitor.year) or (
            prev_year == now_visitor.year and prev_month < now_visitor.month
        )

        context = {
            "booking": booking,
            "token": token,
            "host": host,
            "event": event,
            "visitor_tz": tz_str,
            "year": year,
            "month": month,
            "month_name": calendar.month_name[month],
            "month_weeks": month_weeks,
            "available_dates": available_dates,
            "selected_date": selected_date,
            "day_slots": day_slots,
            "now_visitor": now_visitor,
            "today_visitor": today_visitor,
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
            "is_prev_disabled": is_prev_disabled,
            "all_timezones": zoneinfo.available_timezones(),
        }

        partial = request.GET.get("partial")
        if request.headers.get("HX-Request"):
            if partial == "calendar":
                return render(request, "bookings/partials/calendar.html", context)
            elif partial == "slots":
                return render(request, "bookings/partials/slots.html", context)
            elif partial == "tz_change":
                return render(request, "bookings/partials/booking_body.html", context)

        return render(request, "bookings/reschedule.html", context)

    def post(self, request, uid):
        from apps.bookings.tokens import verify_manage_token

        token = request.GET.get("t", "")
        if not verify_manage_token(uid, token):
            raise Http404("Not found")

        booking = get_object_or_404(Booking.objects.select_related("event_type", "host"), uid=uid)

        reason = request.POST.get("reason", "")
        tz_str = request.POST.get("tz") or booking.invitee_timezone
        slot_time_str = request.POST.get("slot_time")

        try:
            slot_time = datetime.fromisoformat(slot_time_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return HttpResponse("Invalid slot time.", status=400)

        from apps.bookings.services import (
            AlreadyCancelled,
            ReschedulingNotAllowed,
            SlotUnavailable,
            reschedule_booking,
        )

        try:
            new_booking = reschedule_booking(
                booking=booking,
                new_start_at=slot_time,
                rescheduled_by="invitee",
                reason=reason,
                now=django_timezone.now(),
            )
        except AlreadyCancelled:
            return redirect(f"/booking/{booking.uid}/?t={token}")
        except ReschedulingNotAllowed as e:
            return HttpResponse(str(e), status=403)
        except SlotUnavailable as e:
            # Need to re-render the slots partial
            event = booking.event_type
            d = slot_time.astimezone(ZoneInfo(tz_str)).date()
            from apps.integrations.services import fetch_external_busy

            u_start = datetime.combine(d - timedelta(days=1), datetime.min.time(), tzinfo=UTC)
            u_end = datetime.combine(d + timedelta(days=1), datetime.max.time(), tzinfo=UTC)
            external_busy = fetch_external_busy(booking.host, u_start, u_end)
            day_slots = get_slots(
                event,
                d,
                d,
                django_timezone.now(),
                external_busy=external_busy,
                exclude_booking_id=booking.id,
            )
            context = {
                "host": booking.host,
                "event": event,
                "visitor_tz": tz_str,
                "day_slots": day_slots,
                "selected_day": d.isoformat(),
                "error_message": str(e),
            }
            return render(request, "bookings/partials/slots.html", context, status=409)

        # Success: redirect to new booking
        from apps.bookings.tokens import make_manage_token

        new_token = make_manage_token(new_booking)
        response = HttpResponse()
        response["HX-Redirect"] = f"/booking/{new_booking.uid}/?t={new_token}"
        return response


class DashboardBookingRescheduleView(LoginRequiredMixin, View):
    def post(self, request, uid):
        booking = get_object_or_404(Booking, uid=uid, host=request.user)
        reason = request.POST.get("reason", "")
        slot_time_str = request.POST.get("slot_time")

        try:
            slot_time = datetime.fromisoformat(slot_time_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return HttpResponse("Invalid slot time.", status=400)

        from apps.bookings.services import AlreadyCancelled, reschedule_booking

        with contextlib.suppress(AlreadyCancelled):
            reschedule_booking(
                booking=booking,
                new_start_at=slot_time,
                rescheduled_by="host",
                reason=reason,
                now=django_timezone.now(),
            )

        next_url = request.META.get("HTTP_REFERER", "/dashboard/")
        return redirect(next_url)


class DashboardBookingApproveView(LoginRequiredMixin, View):
    def post(self, request, uid):
        booking = get_object_or_404(Booking, uid=uid, host=request.user)
        from apps.bookings.services import InvalidTransition, approve_booking

        with contextlib.suppress(InvalidTransition):
            approve_booking(booking=booking, approved_by=request.user, now=django_timezone.now())
        return redirect("dashboard")


class DashboardBookingRejectView(LoginRequiredMixin, View):
    def post(self, request, uid):
        booking = get_object_or_404(Booking, uid=uid, host=request.user)
        reason = request.POST.get("reason", "")
        from apps.bookings.services import InvalidTransition, reject_booking

        with contextlib.suppress(InvalidTransition):
            reject_booking(
                booking=booking, rejected_by=request.user, reason=reason, now=django_timezone.now()
            )
        return redirect("dashboard")


class BookingApproveView(View):
    def get(self, request, uid):
        from apps.bookings.tokens import verify_approve_token

        token = request.GET.get("t", "")
        if not verify_approve_token(uid, token):
            raise Http404("Not found")

        booking = get_object_or_404(Booking, uid=uid)
        return render(
            request, "bookings/approve_confirm.html", {"booking": booking, "token": token}
        )

    def post(self, request, uid):
        from apps.bookings.tokens import verify_approve_token

        token = request.GET.get("t", "")
        if not verify_approve_token(uid, token):
            raise Http404("Not found")

        booking = get_object_or_404(Booking, uid=uid)
        from apps.bookings.services import InvalidTransition, approve_booking

        with contextlib.suppress(InvalidTransition):
            approve_booking(booking=booking, approved_by=booking.host, now=django_timezone.now())

        return render(
            request, "bookings/action_success.html", {"booking": booking, "action": "approved"}
        )


class BookingRejectView(View):
    def get(self, request, uid):
        from apps.bookings.tokens import verify_reject_token

        token = request.GET.get("t", "")
        if not verify_reject_token(uid, token):
            raise Http404("Not found")

        booking = get_object_or_404(Booking, uid=uid)
        return render(request, "bookings/reject_confirm.html", {"booking": booking, "token": token})

    def post(self, request, uid):
        from apps.bookings.tokens import verify_reject_token

        token = request.GET.get("t", "")
        if not verify_reject_token(uid, token):
            raise Http404("Not found")

        booking = get_object_or_404(Booking, uid=uid)
        reason = request.POST.get("reason", "")

        from apps.bookings.services import InvalidTransition, reject_booking

        with contextlib.suppress(InvalidTransition):
            reject_booking(
                booking=booking, rejected_by=booking.host, reason=reason, now=django_timezone.now()
            )

        return render(
            request, "bookings/action_success.html", {"booking": booking, "action": "rejected"}
        )


import csv

from django.db.models import Count, Q
from django.http import StreamingHttpResponse


class Echo:
    def write(self, value):
        return value


class DashboardBookingsView(LoginRequiredMixin, View):
    def get(self, request):
        now = django_timezone.now()

        # Base queryset
        qs = (
            Booking.objects.filter(host=request.user)
            .select_related("event_type", "host")
            .prefetch_related("attendees")
        )

        # Single aggregate query for tab counts
        counts = qs.aggregate(
            upcoming=Count(
                "id", filter=Q(status=Booking.StatusChoices.CONFIRMED, start_at__gte=now)
            ),
            pending=Count("id", filter=Q(status=Booking.StatusChoices.PENDING)),
            past=Count("id", filter=Q(status=Booking.StatusChoices.CONFIRMED, start_at__lt=now)),
            cancelled=Count(
                "id",
                filter=Q(
                    status__in=[Booking.StatusChoices.CANCELLED, Booking.StatusChoices.REJECTED]
                ),
            ),
        )

        tab = request.GET.get("tab", "upcoming")
        if tab == "upcoming":
            qs = qs.filter(status=Booking.StatusChoices.CONFIRMED, start_at__gte=now).order_by(
                "start_at"
            )
        elif tab == "pending":
            qs = qs.filter(status=Booking.StatusChoices.PENDING).order_by("start_at")
        elif tab == "past":
            qs = qs.filter(status=Booking.StatusChoices.CONFIRMED, start_at__lt=now).order_by(
                "-start_at"
            )
        elif tab == "cancelled":
            qs = qs.filter(
                status__in=[Booking.StatusChoices.CANCELLED, Booking.StatusChoices.REJECTED]
            ).order_by("-start_at")

        # Filtering
        event_type_ids = request.GET.getlist("event_type")
        if event_type_ids:
            qs = qs.filter(event_type_id__in=event_type_ids)

        date_preset = request.GET.get("date")
        if date_preset == "this_week":
            qs = qs.filter(start_at__gte=now, start_at__lte=now + timedelta(days=7))
        elif date_preset == "this_month":
            qs = qs.filter(start_at__year=now.year, start_at__month=now.month)
        elif date_preset == "next_30":
            qs = qs.filter(start_at__gte=now, start_at__lte=now + timedelta(days=30))

        search = request.GET.get("q")
        if search:
            qs = qs.filter(Q(invitee_name__icontains=search) | Q(invitee_email__icontains=search))

        # Export
        if request.GET.get("export") == "csv":

            def get_rows():
                yield [
                    "Booking UID",
                    "Date",
                    "Time UTC",
                    "Time Local",
                    "Duration",
                    "Event Type",
                    "Invitee Name",
                    "Invitee Email",
                    "Status",
                    "Created At",
                ]
                for b in qs:
                    local_tz = (
                        zoneinfo.ZoneInfo(request.user.timezone)
                        if request.user.timezone
                        else zoneinfo.ZoneInfo("UTC")
                    )
                    local_time = b.start_at.astimezone(local_tz)
                    yield [
                        str(b.uid),
                        local_time.strftime("%Y-%m-%d"),
                        b.start_at.strftime("%H:%M"),
                        local_time.strftime("%H:%M"),
                        str(b.event_type.duration_minutes),
                        b.event_type.title,
                        b.invitee_name,
                        b.invitee_email,
                        b.get_status_display(),
                        b.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    ]

            pseudo_buffer = Echo()
            writer = csv.writer(pseudo_buffer)
            response = StreamingHttpResponse(
                (writer.writerow(row) for row in get_rows()), content_type="text/csv"
            )
            response["Content-Disposition"] = 'attachment; filename="bookings.csv"'
            return response

        # Pagination
        from django.core.paginator import Paginator

        paginator = Paginator(qs, 25)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        event_types = EventType.objects.filter(owner=request.user)

        context = {
            "page_obj": page_obj,
            "tab": tab,
            "counts": counts,
            "event_types": event_types,
            "current_event_types": [int(i) for i in event_type_ids if i.isdigit()],
            "current_date": date_preset,
            "search_q": search,
            "host_tz": request.user.timezone or "UTC",
        }

        if request.headers.get("HX-Request"):
            return render(request, "dashboard/bookings/list_partial.html", context)
        return render(request, "dashboard/bookings/list.html", context)


class WaitlistListView(LoginRequiredMixin, View):
    def get(self, request, slug):
        event_type = get_object_or_404(EventType, owner=request.user, slug=slug)
        waitlist_entries = event_type.waitlist_entries.order_by("-created_at")
        context = {
            "event_type": event_type,
            "waitlist_entries": waitlist_entries,
        }
        return render(request, "bookings/waitlist_list.html", context)


class WaitlistRemoveView(LoginRequiredMixin, View):
    def post(self, request, uid):
        from apps.bookings.models import WaitlistEntry
        entry = get_object_or_404(WaitlistEntry, host=request.user, claim_token=uid)
        entry.status = WaitlistEntry.StatusChoices.REMOVED
        entry.save(update_fields=["status", "updated_at"])
        from django.contrib import messages
        messages.success(request, f"Removed {entry.invitee_name} from waitlist.")
        return redirect(request.META.get("HTTP_REFERER", "/dashboard/"))


class JoinWaitlistView(View):
    def get_host_and_event(self, host_slug, event_slug):
        host = User.objects.filter(slug__iexact=host_slug, is_active=True).first()
        if not host:
            raise Http404("Host not found")
        event = EventType.objects.filter(
            owner=host, slug__iexact=event_slug, is_active=True
        ).first()
        if not event:
            raise Http404("Event not found")
        return host, event

    def get(self, request, host_slug, event_slug):
        host, event = self.get_host_and_event(host_slug, event_slug)
        if not event.waitlist_enabled:
            raise Http404("Waitlist not enabled")
        
        current_size = WaitlistEntry.objects.filter(
            event_type=event, status=WaitlistEntry.StatusChoices.WAITING
        ).count()
        if event.waitlist_max_size is not None and current_size >= event.waitlist_max_size:
            return render(request, "bookings/waitlist_full.html", {"host": host, "event": event})
            
        tz_str = request.GET.get("tz", "UTC")
        if tz_str not in zoneinfo.available_timezones():
            tz_str = "UTC"

        from apps.bookings.forms import WaitlistForm
        from django.core.signing import Signer

        form = WaitlistForm(
            event_type=event,
            initial={"tz": tz_str, "event_type_id": str(event.id)},
        )
        signer = Signer()
        timestamp = str(datetime.now(UTC).timestamp())
        form.fields["timestamp_token"].initial = signer.sign(timestamp)

        return render(request, "bookings/join_waitlist.html", {"form": form, "host": host, "event": event})

    def post(self, request, host_slug, event_slug):
        host, event = self.get_host_and_event(host_slug, event_slug)
        if not event.waitlist_enabled:
            raise Http404("Waitlist not enabled")

        current_size = WaitlistEntry.objects.filter(
            event_type=event, status=WaitlistEntry.StatusChoices.WAITING
        ).count()
        if event.waitlist_max_size is not None and current_size >= event.waitlist_max_size:
            return render(request, "bookings/waitlist_full.html", {"host": host, "event": event})

        from apps.bookings.forms import WaitlistForm
        form = WaitlistForm(request.POST, event_type=event)
        if form.is_valid():
            email = form.cleaned_data["invitee_email"]
            # Rate limit check (using cache)
            from django.core.cache import cache
            rl_key = f"rl_waitlist_{event.id}_{email}"
            attempts = cache.get(rl_key, 0)
            if attempts >= 3:
                return HttpResponse("Too many requests. Please try again later.", status=429)
            cache.set(rl_key, attempts + 1, 3600)  # 1 hour window

            # Prevent duplicate active entry
            if WaitlistEntry.objects.filter(
                event_type=event,
                invitee_email=email,
                status__in=[WaitlistEntry.StatusChoices.WAITING, WaitlistEntry.StatusChoices.OFFERED]
            ).exists():
                form.add_error("invitee_email", "You are already on the waitlist for this event.")
                return render(request, "bookings/join_waitlist.html", {"form": form, "host": host, "event": event})

            entry = WaitlistEntry.objects.create(
                event_type=event,
                host=host,
                invitee_name=form.cleaned_data["invitee_name"],
                invitee_email=email,
                notes=form.cleaned_data.get("invitee_notes", ""),
                invitee_timezone=form.cleaned_data["tz"],
                answers=form.cleaned_data["answers"],
            )
            
            from apps.bookings.tasks import send_waitlist_confirmation
            send_waitlist_confirmation.delay(entry.id)
            
            return render(request, "bookings/join_waitlist_success.html", {"host": host, "event": event, "entry": entry})
        
        return render(request, "bookings/join_waitlist.html", {"form": form, "host": host, "event": event})


class LeaveWaitlistView(View):
    def get(self, request, uid):
        from apps.bookings.models import WaitlistEntry
        entry = get_object_or_404(WaitlistEntry, claim_token=uid)
        if entry.status != WaitlistEntry.StatusChoices.WAITING:
            return render(request, "bookings/leave_waitlist_invalid.html", {"entry": entry})
        return render(request, "bookings/leave_waitlist_confirm.html", {"entry": entry})
        
    def post(self, request, uid):
        from apps.bookings.models import WaitlistEntry
        entry = get_object_or_404(WaitlistEntry, claim_token=uid)
        if entry.status == WaitlistEntry.StatusChoices.WAITING:
            entry.status = WaitlistEntry.StatusChoices.CANCELLED
            entry.save(update_fields=["status"])
        return render(request, "bookings/leave_waitlist_success.html", {"entry": entry})


class WaitlistClaimView(View):
    def get(self, request, uid):
        from apps.bookings.models import WaitlistEntry
        from django.utils import timezone
        
        entry = get_object_or_404(WaitlistEntry, claim_token=uid)
        now = timezone.now()
        
        if entry.status != WaitlistEntry.StatusChoices.OFFERED or (entry.offer_expires_at and entry.offer_expires_at < now):
            return render(request, "bookings/waitlist_claim_expired.html", {"entry": entry})
            
        return render(request, "bookings/waitlist_claim.html", {"entry": entry})
        
    def post(self, request, uid):
        from apps.bookings.models import WaitlistEntry
        from apps.bookings.services import create_booking, SlotUnavailable
        from django.utils import timezone
        
        entry = get_object_or_404(WaitlistEntry, claim_token=uid)
        now = timezone.now()
        
        if entry.status != WaitlistEntry.StatusChoices.OFFERED or (entry.offer_expires_at and entry.offer_expires_at < now):
            return render(request, "bookings/waitlist_claim_expired.html", {"entry": entry})
            
        entry.status = WaitlistEntry.StatusChoices.CLAIMED
        entry.save(update_fields=["status", "updated_at"])
        
        try:
            booking = create_booking(
                event_type=entry.event_type,
                start_at=entry.offered_booking_slot,
                invitee_name=entry.invitee_name,
                invitee_email=entry.invitee_email,
                invitee_timezone=entry.invitee_timezone,
                answers=entry.answers,
                notes=entry.notes,
                guest_emails=[],
                now=now,
            )
            
            if booking.status == Booking.StatusChoices.PENDING_PAYMENT:
                from apps.payments.routing import select_provider
                from apps.payments.services import create_payment_for_booking
                from django.urls import reverse

                payment = create_payment_for_booking(booking=booking)
                provider = select_provider(booking.event_type)
                success_url = request.build_absolute_uri(reverse("payments:payment_return"))
                cancel_url = request.build_absolute_uri(
                    f"{reverse('payments:payment_cancel')}?payment_uid={payment.uid}"
                )
                checkout_result = provider.create_checkout(payment, success_url, cancel_url)
                payment.external_session_id = checkout_result.session_id
                payment.save(update_fields=["external_session_id"])

                return redirect(checkout_result.url)
                
            return redirect("bookings:booking_confirmation", uid=booking.uid)
            
        except SlotUnavailable:
            entry.status = WaitlistEntry.StatusChoices.OFFERED
            entry.save(update_fields=["status", "updated_at"])
            return render(request, "bookings/waitlist_claim_expired.html", {"entry": entry})


class DashboardBookingNoShowView(LoginRequiredMixin, View):
    def post(self, request, uid):
        booking = get_object_or_404(Booking, uid=uid, host=request.user)
        from apps.bookings.services import InvalidTransition, mark_booking_no_show

        with contextlib.suppress(InvalidTransition):
            mark_booking_no_show(booking=booking, marked_by=request.user, now=django_timezone.now())
        next_url = request.META.get("HTTP_REFERER", "/dashboard/bookings/")
        return redirect(next_url)

from django.views.decorators.clickjacking import xframe_options_exempt
import json

class BookingEmbedView(BookingPageView):
    @method_decorator(xframe_options_exempt)
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        # Prevent cookie setting on this response for embeds
        response.cookies.clear()
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Pass metadata and UTMs down so the template/form can grab them
        context['is_embed'] = True
        return context

    def get(self, request, host_slug, event_slug):
        response = super().get(request, host_slug, event_slug)
        if hasattr(response, 'template_name'):
            response.template_name = ['bookings/booking_embed.html']
        return response
