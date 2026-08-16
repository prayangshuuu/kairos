from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from .models import Client

class ClientListView(LoginRequiredMixin, ListView):
    model = Client
    template_name = "clients/client_list.html"
    context_object_name = "clients"
    paginate_by = 50

    def get_queryset(self):
        qs = Client.objects.with_computed_fields().filter(host=self.request.user)
        
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

class ClientDetailView(LoginRequiredMixin, DetailView):
    model = Client
    template_name = "clients/client_detail.html"
    context_object_name = "client"

    def get_queryset(self):
        return Client.objects.with_computed_fields().filter(host=self.request.user)

import csv
from django.http import HttpResponse

from django.views.generic import View

class ClientExportView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="clients.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Name', 'Email', 'Phone', 'Timezone', 'Status'])
        
        for client in Client.objects.filter(host=request.user):
            writer.writerow([client.name, client.email, client.phone, client.timezone, client.status])
            
        return response

class ClientImportView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        if 'csv_file' in request.FILES:
            csv_file = request.FILES['csv_file']
            decoded_file = csv_file.read().decode('utf-8').splitlines()
            reader = csv.DictReader(decoded_file)
            for row in reader:
                Client.objects.update_or_create(
                    host=request.user,
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

class ClientBookOnBehalfView(LoginRequiredMixin, DetailView):
    model = Client
    def post(self, request, *args, **kwargs):
        client = self.get_object()
        event_type_id = request.POST.get('event_type_id')
        start_at = request.POST.get('start_at') # Expect ISO string
        
        if event_type_id and start_at:
            event_type = get_object_or_404(EventType, id=event_type_id, owner=request.user)
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
