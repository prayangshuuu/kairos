import zoneinfo

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone as django_timezone
from django_ratelimit.decorators import ratelimit

from .models import User
from .validators import validate_slug


def home(request):
    if request.user.is_authenticated:
        if not request.user.slug:
            return redirect("onboarding")
        return redirect("dashboard")
    return render(request, "home.html")


@login_required
def dashboard(request):
    from apps.bookings.models import Booking
    from apps.integrations.models import CalendarConnection

    pending_bookings = (
        Booking.objects.filter(host=request.user, status=Booking.StatusChoices.PENDING)
        .select_related("event_type")
        .order_by("start_at")
    )

    next_meeting = (
        Booking.objects.filter(
            host=request.user,
            status=Booking.StatusChoices.CONFIRMED,
            start_at__gte=django_timezone.now(),
        )
        .select_related("event_type")
        .order_by("start_at")
        .first()
    )

    broken_connections = CalendarConnection.objects.filter(user=request.user, is_active=False)

    return render(
        request,
        "dashboard.html",
        {
            "pending_bookings": pending_bookings,
            "next_meeting": next_meeting,
            "broken_connections": broken_connections,
        },
    )


@login_required
def onboarding(request):
    if request.user.slug:
        return redirect("dashboard")

    if request.method == "POST":
        slug = request.POST.get("slug", "").lower()
        display_name = request.POST.get("display_name", "")
        timezone = request.POST.get("timezone", "UTC")

        try:
            validate_slug(slug)
            if User.objects.filter(slug=slug).exclude(id=request.user.id).exists():
                raise ValidationError("Slug is taken.")

            request.user.slug = slug
            request.user.display_name = display_name
            request.user.timezone = timezone
            request.user.save()
            return redirect("dashboard")
        except ValidationError:
            pass  # Handle error in UI

    timezones = sorted(zoneinfo.available_timezones())

    # Pre-fill suggestions
    suggestion = request.user.display_name
    if not suggestion and request.user.first_name:
        suggestion = f"{request.user.first_name} {request.user.last_name}".strip()
    if not suggestion:
        suggestion = request.user.email.split("@")[0]

    base_slug = "".join(c if c.isalnum() else "-" for c in suggestion).strip("-").lower()

    suggested_slug = base_slug
    counter = 1
    while User.objects.filter(slug=suggested_slug).exists():
        suggested_slug = f"{base_slug}-{counter}"
        counter += 1

    return render(
        request,
        "accounts/onboarding.html",
        {
            "timezones": timezones,
            "suggested_slug": suggested_slug,
        },
    )


@login_required
@ratelimit(key="ip", rate="30/m", method="GET", block=True)
def check_slug(request):
    slug = request.GET.get("slug", "").lower()

    if not slug:
        return HttpResponse("")

    try:
        validate_slug(slug)
    except ValidationError as e:
        return HttpResponse(f"<span class='text-red-500 text-sm'>{e.message}</span>")

    if User.objects.filter(slug=slug).exclude(id=request.user.id).exists():
        return HttpResponse("<span class='text-red-500 text-sm'>Unavailable</span>")

    return HttpResponse("<span class='text-green-500 text-sm'>Available</span>")


from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import View

from apps.accounts.forms import (
    BrandingForm,
    NotificationPreferencesForm,
    PreferencesForm,
    ProfileForm,
    SlugForm,
)
from apps.accounts.models import UserNotificationPreference, UserSlugHistory
from apps.accounts.services import process_avatar


class SettingsProfileView(LoginRequiredMixin, View):
    def get(self, request):
        profile_form = ProfileForm(instance=request.user)
        slug_form = SlugForm(initial={"slug": request.user.slug})
        return render(
            request,
            "accounts/settings_profile.html",
            {
                "profile_form": profile_form,
                "slug_form": slug_form,
            },
        )

    def post(self, request):
        if "update_profile" in request.POST:
            profile_form = ProfileForm(request.POST, request.FILES, instance=request.user)
            if profile_form.is_valid():
                user = profile_form.save(commit=False)
                if "avatar" in request.FILES:
                    user.avatar = process_avatar(request.FILES["avatar"])
                user.save()
                return render(
                    request,
                    "accounts/partials/settings_success.html",
                    {"message": "Profile updated successfully."},
                )
            return render(
                request,
                "accounts/partials/settings_error.html",
                {"message": "Error updating profile."},
            )

        elif "update_slug" in request.POST:
            slug_form = SlugForm(request.POST)
            if slug_form.is_valid():
                new_slug = slug_form.cleaned_data["slug"]
                password = slug_form.cleaned_data["password"]

                if not request.user.check_password(password):
                    return render(
                        request,
                        "accounts/partials/settings_error.html",
                        {"message": "Incorrect password."},
                    )

                if (
                    User.objects.filter(slug=new_slug).exclude(id=request.user.id).exists()
                    or UserSlugHistory.objects.filter(old_slug=new_slug).exists()
                ):
                    return render(
                        request,
                        "accounts/partials/settings_error.html",
                        {"message": "Slug is not available."},
                    )

                # Save old slug to history
                if request.user.slug:
                    UserSlugHistory.objects.get_or_create(
                        user=request.user, old_slug=request.user.slug
                    )

                request.user.slug = new_slug
                request.user.save()
                return render(
                    request,
                    "accounts/partials/settings_success.html",
                    {"message": "Slug updated successfully."},
                )
            return render(
                request,
                "accounts/partials/settings_error.html",
                {"message": "Error updating slug."},
            )


class SettingsPreferencesView(LoginRequiredMixin, View):
    def get(self, request):
        pref_form = PreferencesForm(instance=request.user)
        notif_pref, _ = UserNotificationPreference.objects.get_or_create(user=request.user)
        notif_form = NotificationPreferencesForm(instance=notif_pref)
        return render(
            request,
            "accounts/settings_preferences.html",
            {
                "pref_form": pref_form,
                "notif_form": notif_form,
            },
        )

    def post(self, request):
        if "update_prefs" in request.POST:
            pref_form = PreferencesForm(request.POST, instance=request.user)
            if pref_form.is_valid():
                pref_form.save()

                # Check if update schedules is checked
                if request.POST.get("update_schedules"):
                    request.user.schedules.update(timezone=pref_form.cleaned_data["timezone"])

                return render(
                    request,
                    "accounts/partials/settings_success.html",
                    {"message": "Preferences updated successfully."},
                )
            return render(
                request,
                "accounts/partials/settings_error.html",
                {"message": "Error updating preferences."},
            )

        elif "update_notifs" in request.POST:
            notif_pref, _ = UserNotificationPreference.objects.get_or_create(user=request.user)
            notif_form = NotificationPreferencesForm(request.POST, instance=notif_pref)
            if notif_form.is_valid():
                notif_form.save()
                return render(
                    request,
                    "accounts/partials/settings_success.html",
                    {"message": "Notifications updated successfully."},
                )
            return render(
                request,
                "accounts/partials/settings_error.html",
                {"message": "Error updating notifications."},
            )


class SettingsSecurityView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, "accounts/settings_security.html")


class SettingsBrandingView(LoginRequiredMixin, View):
    def get(self, request):
        brand_form = BrandingForm(instance=request.user)
        return render(request, "accounts/settings_branding.html", {"brand_form": brand_form})

    def post(self, request):
        brand_form = BrandingForm(request.POST, instance=request.user)
        if brand_form.is_valid():
            # If hide_branding is true, maybe check plan in future. Force false if no plan.
            brand_form.save()
            return render(
                request,
                "accounts/partials/settings_success.html",
                {"message": "Branding updated successfully."},
            )
        return render(
            request,
            "accounts/partials/settings_error.html",
            {"message": "Error updating branding."},
        )


class SettingsDangerView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, "accounts/settings_danger.html")

    def post(self, request):
        if "export_data" in request.POST:
            from apps.accounts.tasks import export_user_data

            export_user_data.delay(request.user.id)
            return render(
                request,
                "accounts/partials/settings_success.html",
                {"message": "Export started. You will receive an email shortly."},
            )

        elif "delete_account" in request.POST:
            email_confirm = request.POST.get("email")
            password = request.POST.get("password")

            if email_confirm != request.user.email:
                return render(
                    request,
                    "accounts/partials/settings_error.html",
                    {"message": "Email does not match."},
                )

            if not request.user.check_password(password):
                return render(
                    request,
                    "accounts/partials/settings_error.html",
                    {"message": "Incorrect password."},
                )

            # Schedule anonymization in 30 days
            from apps.accounts.tasks import run_account_anonymization

            run_account_anonymization.apply_async((request.user.id,), countdown=30 * 24 * 60 * 60)

            from django.contrib.auth import logout

            logout(request)
            response = HttpResponse()
            response["HX-Redirect"] = "/"
            return response
