from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from .models import Client
from apps.core.permissions import TeamContextMixin, ViewPermissionMixin

class ClientListView(TeamContextMixin, ViewPermissionMixin, ListView):
    model = Client
    template_name = "clients/client_list.html"
    context_object_name = "clients"
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset().with_computed_fields()
        
        # Search
        q = self.request.GET.get('q')
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q))
            
        # Status filter
        status = self.request.GET.get('status')
        if status in [Client.StatusChoices.ACTIVE, Client.StatusChoices.ARCHIVED]:
            qs = qs.filter(status=status)
        elif not status:
            qs = qs.filter(status=Client.StatusChoices.ACTIVE)
            
        # Sort
        sort = self.request.GET.get('sort', '-last_seen_at')
        valid_sorts = ['name', '-name', 'first_seen_at', '-first_seen_at', 'last_seen_at', '-last_seen_at']
        if sort in valid_sorts:
            qs = qs.order_by(sort)
            
        return qs

class ClientDetailView(TeamContextMixin, ViewPermissionMixin, DetailView):
    model = Client
    template_name = "clients/client_detail.html"
    context_object_name = "client"

    def get_queryset(self):
        return super().get_queryset().with_computed_fields()

import csv
from django.http import HttpResponse

from django.views.generic import View

class ClientExportView(TeamContextMixin, ViewPermissionMixin, View):
    model = Client
    
    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="clients.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Name', 'Email', 'Phone', 'Timezone', 'Status'])
        
        for client in self.get_queryset():
            writer.writerow([client.name, client.email, client.phone, client.timezone, client.status])
            
        return response

class ClientImportView(TeamContextMixin, ViewPermissionMixin, View):
    model = Client

    def post(self, request, *args, **kwargs):
        if 'csv_file' in request.FILES:
            csv_file = request.FILES['csv_file']
            decoded_file = csv_file.read().decode('utf-8').splitlines()
            reader = csv.DictReader(decoded_file)
            from apps.core.permissions import get_active_team
            team = get_active_team(request)
            for row in reader:
                if team:
                    Client.objects.update_or_create(
                        team=team,
                        email=row.get('Email', '').strip().lower(),
                        defaults={
                            'name': row.get('Name', ''),
                            'phone': row.get('Phone', ''),
                            'timezone': row.get('Timezone', ''),
                        }
                    )
                else:
                    Client.objects.update_or_create(
                        host=request.user,
                        team__isnull=True,
                        email=row.get('Email', '').strip().lower(),
                        defaults={
                            'name': row.get('Name', ''),
                            'phone': row.get('Phone', ''),
                            'timezone': row.get('Timezone', ''),
                        }
                    )
        return redirect('clients:list')

from apps.bookings.services import create_booking
from apps.scheduling.models import EventType
from django.utils import timezone

class ClientBookOnBehalfView(TeamContextMixin, ViewPermissionMixin, DetailView):
    model = Client
    def post(self, request, *args, **kwargs):
        client = self.get_object()
        event_type_id = request.POST.get('event_type_id')
        start_at = request.POST.get('start_at') # Expect ISO string
        
        if event_type_id and start_at:
            from apps.core.permissions import get_active_team
            team = get_active_team(request)
            if team:
                event_type = get_object_or_404(EventType, id=event_type_id, team=team)
            else:
                event_type = get_object_or_404(EventType, id=event_type_id, owner=request.user, team__isnull=True)
            import datetime
            dt_start = datetime.datetime.fromisoformat(start_at)
            
            create_booking(
                event_type=event_type,
                start_at=dt_start,
                invitee_name=client.name,
                invitee_email=client.email,
                invitee_timezone=client.timezone or 'UTC',
                answers={},
                notes=request.POST.get('notes', ''),
                now=timezone.now()
            )
        return redirect('clients:detail', pk=client.pk)
