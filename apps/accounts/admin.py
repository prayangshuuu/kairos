from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DefaultUserAdmin
from .models import User

@admin.register(User)
class UserAdmin(DefaultUserAdmin):
    ordering = ('email',)
    list_display = ('email', 'slug', 'display_name', 'timezone', 'is_active', 'date_joined')
    search_fields = ('email', 'slug', 'display_name')
    
    # We remove the username field from forms
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('display_name', 'slug', 'bio', 'avatar')}),
        ('Preferences', {'fields': ('timezone', 'locale', 'time_format', 'week_start')}),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password'),
        }),
    )
