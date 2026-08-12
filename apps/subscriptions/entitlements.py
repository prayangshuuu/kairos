import functools
import math
from collections.abc import Callable

from django.shortcuts import redirect
from django.urls import reverse

from apps.subscriptions.plans import get_plan_feature, get_plan_limit


def get_user_subscription(user):
    """Retrieve and cache the user's subscription on the user instance to avoid DB queries in loops."""
    if not user or not user.is_authenticated:
        return None

    if hasattr(user, "_cached_subscription"):
        return user._cached_subscription

    from apps.subscriptions.models import Subscription

    sub, _ = Subscription.objects.get_or_create(
        user=user, defaults={"plan_code": "free", "status": Subscription.STATUS_ACTIVE}
    )
    user._cached_subscription = sub
    return sub


def get_effective_plan_code(user) -> str:
    """Return the active plan_code for the user, falling back to 'free' if expired/lapsed."""
    if not user or not user.is_authenticated:
        return "free"

    sub = get_user_subscription(user)
    if not sub:
        return "free"

    return sub.effective_plan_code


def has_feature(user, feature_code: str) -> bool:
    """Check if a user's subscription includes a given feature."""
    plan_code = get_effective_plan_code(user)
    return get_plan_feature(plan_code, feature_code)


def within_limit(user, limit_code: str, current_count: int) -> bool:
    """Check if the user is strictly within their plan limit (current_count < max_limit)."""
    plan_code = get_effective_plan_code(user)
    limit = get_plan_limit(plan_code, limit_code)

    if limit is None or limit == math.inf or (isinstance(limit, float) and math.isinf(limit)):
        return True

    return current_count < limit


def require_feature(feature_code: str, redirect_url: str | None = None):
    """View decorator that enforces a feature requirement at the view level."""

    def decorator(view_func: Callable):
        @functools.wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user or not request.user.is_authenticated:
                return redirect("account_login")

            if not has_feature(request.user, feature_code):
                if redirect_url:
                    return redirect(redirect_url)
                return redirect(
                    reverse("subscriptions:pricing") + f"?feature_required={feature_code}"
                )
            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator


def check_limit(limit_code: str, get_current_count: Callable, redirect_url: str | None = None):
    """View decorator that enforces a numeric limit before proceeding."""

    def decorator(view_func: Callable):
        @functools.wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user or not request.user.is_authenticated:
                return redirect("account_login")

            count = get_current_count(request, *args, **kwargs)
            if not within_limit(request.user, limit_code, count):
                if redirect_url:
                    return redirect(redirect_url)
                return redirect(reverse("subscriptions:pricing") + f"?limit_reached={limit_code}")
            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator
