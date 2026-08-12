from django import template
from apps.subscriptions.entitlements import has_feature, within_limit, get_effective_plan_code
from apps.subscriptions.plans import get_plan

register = template.Library()


@register.simple_tag
def user_has_feature(user, feature_code):
    return has_feature(user, feature_code)


@register.simple_tag
def user_within_limit(user, limit_code, current_count):
    return within_limit(user, limit_code, current_count)


@register.simple_tag
def get_user_plan_code(user):
    return get_effective_plan_code(user)


@register.simple_tag
def get_user_plan_details(user):
    plan_code = get_effective_plan_code(user)
    return get_plan(plan_code)
