from django.contrib import admin
from .models import Schedule, AvailabilityRule, DateOverride

class AvailabilityRuleInline(admin.TabularInline):
    model = AvailabilityRule
    extra = 1

class DateOverrideInline(admin.TabularInline):
    model = DateOverride
    extra = 1

@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ["name", "user", "timezone", "is_default", "created_at"]
    list_filter = ["is_default", "timezone"]
    search_fields = ["name", "user__email"]
    inlines = [AvailabilityRuleInline, DateOverrideInline]

@admin.register(AvailabilityRule)
class AvailabilityRuleAdmin(admin.ModelAdmin):
    list_display = ["schedule", "weekday", "start_time", "end_time"]
    list_filter = ["weekday"]
    search_fields = ["schedule__name", "schedule__user__email"]

@admin.register(DateOverride)
class DateOverrideAdmin(admin.ModelAdmin):
    list_display = ["schedule", "date", "is_unavailable", "start_time", "end_time"]
    list_filter = ["is_unavailable", "date"]
    search_fields = ["schedule__name", "schedule__user__email"]
