import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.text import slugify
from django.views.generic import CreateView, ListView, UpdateView, View

from .forms import EventTypeForm
from .models import BookingQuestion, EventType


class OwnerRequiredMixin(LoginRequiredMixin):
    def get_queryset(self):
        return super().get_queryset().filter(owner=self.request.user)


class EventTypeListView(OwnerRequiredMixin, ListView):
    model = EventType
    template_name = "scheduling/eventtype_list.html"
    context_object_name = "event_types"

    def get_queryset(self):
        from django.db.models import Count, Q
        return super().get_queryset().annotate(
            waitlist_count=Count(
                "waitlist_entries",
                filter=Q(waitlist_entries__status="waiting")
            )
        ).order_by("title")


class EventTypeCreateView(LoginRequiredMixin, CreateView):
    model = EventType
    form_class = EventTypeForm
    template_name = "scheduling/eventtype_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.integrations.models import CalendarConnection, ConferenceConnection
        from apps.payments.models import HostPaymentTerms, PaymentAccount

        # Check Google Meet (via Google Calendar)
        google_cal = CalendarConnection.objects.filter(
            user=self.request.user, provider="google", is_active=True
        ).first()
        context["google_meet_available"] = bool(google_cal and not google_cal.last_error)

        # Check Zoom
        context["zoom_connected"] = ConferenceConnection.objects.filter(
            user=self.request.user, provider="zoom", is_active=True
        ).exists()

        # Check Payments
        stripe_acc = PaymentAccount.objects.filter(
            user=self.request.user, provider="stripe_connect", is_active=True, charges_enabled=True
        ).first()
        context["stripe_active"] = bool(stripe_acc)

        paystation_acc = PaymentAccount.objects.filter(
            user=self.request.user, provider="paystation", is_active=True
        ).first()
        terms = HostPaymentTerms.objects.filter(user=self.request.user).exists()
        context["paystation_active"] = bool(paystation_acc and terms)

        return context

    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)

        # Save questions if passed via JSON
        questions_json = self.request.POST.get("questions_json")
        if questions_json:
            try:
                questions = json.loads(questions_json)
                for i, q in enumerate(questions):
                    if q.get("deleted"):
                        continue

                    options = q.get("options", [])
                    if q.get("field_type") in ["select", "multiselect", "radio"]:
                        if len(options) < 2:
                            return HttpResponse(
                                "Questions with options must have at least two options.", status=400
                            )

                    BookingQuestion.objects.create(
                        event_type=self.object,
                        label=q.get("label", ""),
                        help_text=q.get("help_text", ""),
                        field_type=q.get("field_type", "text"),
                        is_required=q.get("is_required", False),
                        options=options,
                        order=i,
                    )
            except json.JSONDecodeError:
                pass

        if self.request.htmx:
            messages.success(self.request, "Event type created successfully!")
            return HttpResponse(f"""
                <div hx-swap-oob="true" id="toast-container">
                    <div class="bg-green-500 text-white px-4 py-2 rounded shadow mb-4">Event type created!</div>
                </div>
                <script>window.location.href = "{reverse("scheduling:eventtype_edit", kwargs={"slug": self.object.slug})}";</script>
            """)
        return response

    def get_success_url(self):
        return reverse("scheduling:eventtype_edit", kwargs={"slug": self.object.slug})


class EventTypeUpdateView(OwnerRequiredMixin, UpdateView):
    model = EventType
    form_class = EventTypeForm
    template_name = "scheduling/eventtype_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.integrations.models import CalendarConnection, ConferenceConnection
        from apps.payments.models import HostPaymentTerms, PaymentAccount

        # Check Google Meet (via Google Calendar)
        google_cal = CalendarConnection.objects.filter(
            user=self.request.user, provider="google", is_active=True
        ).first()
        context["google_meet_available"] = bool(google_cal and not google_cal.last_error)

        # Check Zoom
        context["zoom_connected"] = ConferenceConnection.objects.filter(
            user=self.request.user, provider="zoom", is_active=True
        ).exists()

        # Check Payments
        stripe_acc = PaymentAccount.objects.filter(
            user=self.request.user, provider="stripe_connect", is_active=True, charges_enabled=True
        ).first()
        context["stripe_active"] = bool(stripe_acc)

        paystation_acc = PaymentAccount.objects.filter(
            user=self.request.user, provider="paystation", is_active=True
        ).first()
        terms = HostPaymentTerms.objects.filter(user=self.request.user).exists()
        context["paystation_active"] = bool(paystation_acc and terms)

        return context

    def form_valid(self, form):
        response = super().form_valid(form)

        # Sync questions
        questions_json = self.request.POST.get("questions_json")
        if questions_json:
            try:
                questions = json.loads(questions_json)
                for i, q in enumerate(questions):
                    if q.get("deleted") and q.get("id"):
                        BookingQuestion.objects.filter(id=q["id"], event_type=self.object).delete()
                        continue
                    elif q.get("deleted"):
                        continue

                    options = q.get("options", [])
                    if q.get("field_type") in ["select", "multiselect", "radio"]:
                        if len(options) < 2:
                            return HttpResponse(
                                "Questions with options must have at least two options.", status=400
                            )

                    if q.get("id"):
                        bq = BookingQuestion.objects.filter(
                            id=q["id"], event_type=self.object
                        ).first()
                        if bq:
                            bq.label = q.get("label", "")
                            bq.help_text = q.get("help_text", "")
                            bq.field_type = q.get("field_type", "text")
                            bq.is_required = q.get("is_required", False)
                            bq.options = options
                            bq.order = i
                            bq.save()
                    else:
                        BookingQuestion.objects.create(
                            event_type=self.object,
                            label=q.get("label", ""),
                            help_text=q.get("help_text", ""),
                            field_type=q.get("field_type", "text"),
                            is_required=q.get("is_required", False),
                            options=options,
                            order=i,
                        )
            except json.JSONDecodeError:
                pass

        if self.request.htmx:
            return HttpResponse("""
                <div hx-swap-oob="true" id="toast-container">
                    <div class="bg-green-500 text-white px-4 py-2 rounded shadow mb-4">Saved successfully!</div>
                </div>
            """)
        return response

    def get_success_url(self):
        return reverse("scheduling:eventtype_edit", kwargs={"slug": self.object.slug})


class EventTypeDuplicateView(LoginRequiredMixin, View):
    def post(self, request, slug):
        event = get_object_or_404(EventType, owner=request.user, slug=slug)

        with transaction.atomic():
            new_title = f"{event.title} (copy)"
            base_slug = slugify(new_title)
            new_slug = base_slug
            counter = 1
            while EventType.objects.filter(owner=request.user, slug=new_slug).exists():
                new_slug = f"{base_slug}-{counter}"
                counter += 1

            new_event = EventType.objects.get(id=event.id)
            new_event.pk = None
            new_event.title = new_title
            new_event.slug = new_slug
            new_event.clean()
            new_event.save()

            for q in event.questions.all():
                q.pk = None
                q.event_type = new_event
                q.save()

        return redirect("scheduling:eventtype_list")


class EventTypeToggleActiveView(LoginRequiredMixin, View):
    def post(self, request, slug):
        event = get_object_or_404(EventType, owner=request.user, slug=slug)

        event.is_active = not event.is_active
        event.save(update_fields=["is_active"])
        return redirect("scheduling:eventtype_list")


class EventTypeDeleteView(LoginRequiredMixin, View):
    def post(self, request, slug):
        event = get_object_or_404(EventType, owner=request.user, slug=slug)
        try:
            event.delete()
        except ProtectedError:
            messages.error(
                request,
                "Cannot delete event type with existing bookings. Please deactivate it instead.",
            )
        return redirect("scheduling:eventtype_list")


class EventTypeCheckSlugView(LoginRequiredMixin, View):
    def get(self, request):
        slug = request.GET.get("slug", "").lower()
        exclude_id = request.GET.get("exclude_id")

        if not slug:
            return HttpResponse("")

        qs = EventType.objects.filter(owner=request.user, slug=slug)
        if exclude_id:
            qs = qs.exclude(id=exclude_id)

        if qs.exists():
            return HttpResponse("<span class='text-red-500 text-sm'>Unavailable</span>")

        url = request.build_absolute_uri(
            f"/{request.user.slug or ('u/' + str(request.user.id))}/{slug}"
        )
        return HttpResponse(f"<span class='text-green-500 text-sm'>Available at {url}</span>")
