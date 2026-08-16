from django import forms
from apps.teams.models import Team

class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ['name', 'slug', 'description', 'brand_colour', 'timezone']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'block w-full rounded-md border-surface-300 shadow-sm focus:border-brand-500 focus:ring-brand-500 sm:text-sm'}),
            'slug': forms.TextInput(attrs={'class': 'block w-full rounded-md border-surface-300 shadow-sm focus:border-brand-500 focus:ring-brand-500 sm:text-sm'}),
            'description': forms.Textarea(attrs={'class': 'block w-full rounded-md border-surface-300 shadow-sm focus:border-brand-500 focus:ring-brand-500 sm:text-sm', 'rows': 3}),
            'brand_colour': forms.TextInput(attrs={'type': 'color', 'class': 'block w-full h-10 rounded-md border-surface-300 shadow-sm focus:border-brand-500 focus:ring-brand-500 sm:text-sm'}),
            'timezone': forms.TextInput(attrs={'class': 'block w-full rounded-md border-surface-300 shadow-sm focus:border-brand-500 focus:ring-brand-500 sm:text-sm'}),
        }
