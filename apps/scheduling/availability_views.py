import json
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import DetailView, View

from .engine import get_slots
from .models import AvailabilityRule, DateOverride, EventType, Schedule


class OwnerRequiredMixin(LoginRequiredMixin):
    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)


class ScheduleListView(LoginRequiredMixin, View):
    def get(self, request):
        default_schedule = request.user.get_default_schedule()
        return redirect("scheduling:schedule_detail", pk=default_schedule.id)


class ScheduleDetailView(OwnerRequiredMixin, DetailView):
    model = Schedule
    template_name = "scheduling/schedule_detail.html"
    context_object_name = "schedule"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["schedules"] = Schedule.objects.filter(user=self.request.user)

        # Format rules for alpine
        rules = self.object.rules.all()
        weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        grid = []
        for i, name in enumerate(weekdays):
            day_rules = [r for r in rules if r.weekday == i]
            grid.append(
                {
                    "dayIndex": i,
                    "dayName": name,
                    "enabled": len(day_rules) > 0,
                    "ranges": [
                        {
                            "start": r.start_time.strftime("%H:%M"),
                            "end": r.end_time.strftime("%H:%M"),
                        }
                        for r in day_rules
                    ]
                    if day_rules
                    else [{"start": "09:00", "end": "17:00"}],
                }
            )

        context["grid_json"] = json.dumps(grid)
        context["overrides"] = self.object.overrides.filter(
            date__gte=timezone.now().date()
        ).order_by("date")

        import zoneinfo

        context["timezones"] = sorted(zoneinfo.available_timezones())
        return context


class ScheduleCreateView(LoginRequiredMixin, View):
    def post(self, request):
        name = request.POST.get("name", "New Schedule")
        s = Schedule.objects.create(
            user=request.user, name=name, timezone=request.user.timezone or "UTC"
        )
        for i in range(5):
            AvailabilityRule.objects.create(
                schedule=s, weekday=i, start_time="09:00", end_time="17:00"
            )
        return redirect("scheduling:schedule_detail", pk=s.id)


class ScheduleDuplicateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        schedule = get_object_or_404(Schedule, user=request.user, pk=pk)

        with transaction.atomic():
            new_s = Schedule.objects.create(
                user=request.user, name=f"{schedule.name} (copy)", timezone=schedule.timezone
            )
            for rule in schedule.rules.all():
                AvailabilityRule.objects.create(
                    schedule=new_s,
                    weekday=rule.weekday,
                    start_time=rule.start_time,
                    end_time=rule.end_time,
                )
        return redirect("scheduling:schedule_detail", pk=new_s.id)


class ScheduleDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        schedule = get_object_or_404(Schedule, user=request.user, pk=pk)

        # Block deleting if it's the only one
        if Schedule.objects.filter(user=request.user).count() == 1:
            messages.error(request, "You cannot delete your only schedule.")
            return redirect("scheduling:schedule_detail", pk=pk)

        if schedule.is_default:
            other = Schedule.objects.filter(user=request.user).exclude(pk=pk).first()
            if other:
                other.is_default = True
                other.save(update_fields=["is_default"])

        try:
            schedule.delete()
            messages.success(request, "Schedule deleted.")
        except ProtectedError as e:
            event_types = [obj.title for obj in e.protected_objects if isinstance(obj, EventType)]
            if event_types:
                messages.error(
                    request,
                    f"Cannot delete this schedule. It is used by Event Types: {', '.join(event_types)}.",
                )
            else:
                messages.error(request, "Cannot delete this schedule because it is in use.")
            return redirect("scheduling:schedule_detail", pk=pk)

        return redirect("scheduling:schedule_list")


class ScheduleSetDefaultView(LoginRequiredMixin, View):
    def post(self, request, pk):
        schedule = get_object_or_404(Schedule, user=request.user, pk=pk)
        schedule.is_default = True
        schedule.save()
        messages.success(request, "Default schedule updated.")
        return redirect("scheduling:schedule_detail", pk=pk)


class ScheduleRenameView(LoginRequiredMixin, View):
    def post(self, request, pk):
        schedule = get_object_or_404(Schedule, user=request.user, pk=pk)
        name = request.POST.get("name")
        if name:
            schedule.name = name
            schedule.save(update_fields=["name"])

        if getattr(request, "htmx", False):
            return HttpResponse(name)
        return redirect("scheduling:schedule_detail", pk=pk)


class ScheduleUpdateTimezoneView(LoginRequiredMixin, View):
    def post(self, request, pk):
        schedule = get_object_or_404(Schedule, user=request.user, pk=pk)
        tz = request.POST.get("timezone")
        if tz:
            schedule.timezone = tz
            schedule.save(update_fields=["timezone"])

        if getattr(request, "htmx", False):
            return HttpResponse("""
                <div hx-swap-oob="true" id="toast-container">
                    <div class="bg-green-500 text-white px-4 py-2 rounded shadow mb-4">Timezone updated!</div>
                </div>
            """)
        return redirect("scheduling:schedule_detail", pk=pk)


class ScheduleRulesUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        schedule = get_object_or_404(Schedule, user=request.user, pk=pk)
        data_json = request.POST.get("grid_json")

        if not data_json:
            return HttpResponse("No data provided.", status=400)

        try:
            grid = json.loads(data_json)
        except json.JSONDecodeError:
            return HttpResponse("Invalid JSON.", status=400)

        # Validate grid
        valid_rules = []
        errors = []

        for day in grid:
            if not day.get("enabled"):
                continue

            ranges = day.get("ranges", [])
            for r in ranges:
                start = r.get("start")
                end = r.get("end")

                if not start or not end:
                    errors.append(f"{day['dayName']}: Missing start or end time.")
                    continue

                if end <= start:
                    errors.append(
                        f"{day['dayName']}: End time ({end}) must be after start time ({start})."
                    )
                    continue

                # Check overlap
                for existing in valid_rules:
                    if existing["weekday"] == day["dayIndex"]:
                        if max(start, existing["start_time"]) < min(end, existing["end_time"]):
                            errors.append(f"{day['dayName']}: Overlapping times.")
                            break

                valid_rules.append(
                    {"weekday": day["dayIndex"], "start_time": start, "end_time": end}
                )

        if errors:
            return HttpResponse("<br>".join(errors), status=400)

        with transaction.atomic():
            schedule.rules.all().delete()
            for r in valid_rules:
                AvailabilityRule.objects.create(
                    schedule=schedule,
                    weekday=r["weekday"],
                    start_time=r["start_time"],
                    end_time=r["end_time"],
                )

        if getattr(request, "htmx", False):
            return HttpResponse("""
                <span class="text-green-600 font-medium ml-2 text-sm" x-data="{show: true}" x-show="show" x-init="setTimeout(() => show = false, 2000)">Saved!</span>
            """)
        return redirect("scheduling:schedule_detail", pk=pk)


class ScheduleDateOverrideUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        schedule = get_object_or_404(Schedule, user=request.user, pk=pk)

        date_str = request.POST.get("date")
        if not date_str:
            # Maybe bulk action?
            start_date_str = request.POST.get("start_date")
            end_date_str = request.POST.get("end_date")
            if start_date_str and end_date_str:
                try:
                    s_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                    e_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

                    if e_date < s_date:
                        return HttpResponse("End date must be after start date.", status=400)

                    current_date = s_date
                    overrides = []
                    while current_date <= e_date:
                        overrides.append(
                            DateOverride(schedule=schedule, date=current_date, is_unavailable=True)
                        )
                        current_date += timedelta(days=1)

                    with transaction.atomic():
                        # Delete existing overrides for these dates
                        DateOverride.objects.filter(
                            schedule=schedule, date__range=(s_date, e_date)
                        ).delete()
                        DateOverride.objects.bulk_create(overrides)

                    messages.success(request, f"Marked {s_date} to {e_date} as unavailable.")
                    return redirect("scheduling:schedule_detail", pk=pk)
                except ValueError:
                    return HttpResponse("Invalid date format.", status=400)

            return HttpResponse("Date is required.", status=400)

        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return HttpResponse("Invalid date format.", status=400)

        is_unavailable = request.POST.get("is_unavailable") == "true"

        with transaction.atomic():
            DateOverride.objects.filter(schedule=schedule, date=target_date).delete()

            if is_unavailable:
                DateOverride.objects.create(
                    schedule=schedule, date=target_date, is_unavailable=True
                )
            else:
                starts = request.POST.getlist("start_time")
                ends = request.POST.getlist("end_time")

                if not starts or not ends or len(starts) != len(ends):
                    return HttpResponse("Invalid time ranges.", status=400)

                for s, e in zip(starts, ends, strict=False):
                    if not s or not e:
                        continue
                    if e <= s:
                        return HttpResponse("End time must be after start time.", status=400)
                    DateOverride.objects.create(
                        schedule=schedule,
                        date=target_date,
                        is_unavailable=False,
                        start_time=s,
                        end_time=e,
                    )

        return redirect("scheduling:schedule_detail", pk=pk)


class ScheduleDateOverrideDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk, override_id):
        schedule = get_object_or_404(Schedule, user=request.user, pk=pk)
        DateOverride.objects.filter(schedule=schedule, id=override_id).delete()
        messages.success(request, "Override deleted.")
        return redirect("scheduling:schedule_detail", pk=pk)


class SchedulePreviewView(LoginRequiredMixin, View):
    def get(self, request, pk):
        schedule = get_object_or_404(Schedule, user=request.user, pk=pk)

        # Create a dummy event type in memory
        event_type = EventType(
            owner=request.user, title="Preview", duration_minutes=30, schedule=schedule
        )

        now = timezone.now()
        start_date = now.date()
        end_date = start_date + timedelta(days=6)

        all_slots = get_slots(
            event_type=event_type, from_date=start_date, to_date=end_date, now=now
        )

        # Group by local date
        import zoneinfo
        from collections import defaultdict

        tz = zoneinfo.ZoneInfo(schedule.timezone)
        slots_by_date = defaultdict(list)

        for slot in all_slots:
            local_time = slot.astimezone(tz)
            slots_by_date[local_time.date()].append(local_time)

        # Render a simple preview
        html = "<div class='space-y-4'>"

        if not slots_by_date:
            html += "<p class='text-sm text-gray-500'>No availability found in the next 7 days.</p>"

        # Ensure dates are displayed in order, including empty ones if preferred, but dict sort is fine
        for day in sorted(slots_by_date.keys()):
            day_slots = slots_by_date[day]
            if not day_slots:
                continue
            html += f"<div><h4 class='font-medium text-sm text-gray-900'>{day.strftime('%A, %b %-d')}</h4>"
            html += "<div class='mt-2 flex flex-wrap gap-2'>"
            for local_time in day_slots:
                html += f"<span class='inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-primary-100 text-primary-800'>{local_time.strftime('%H:%M')}</span>"
            html += "</div></div>"

        html += "</div>"

        return HttpResponse(html)
