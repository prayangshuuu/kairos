import json
import urllib.parse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.core.signing import Signer

from apps.routing.models import RoutingForm, RoutingFormField, RoutingRule, RoutingFormResponse
from apps.routing.engine import evaluate
from apps.scheduling.models import EventType
from apps.accounts.models import User

from django.views.decorators.clickjacking import xframe_options_exempt

@xframe_options_exempt
def public_routing_form_view(request, owner_slug, form_slug, is_embed=False):
    form = get_object_or_404(RoutingForm, slug=form_slug, is_active=True)
    
    if form.owner and form.owner.slug != owner_slug:
        return HttpResponse("Not found", status=404)
    if form.team and form.team.slug != owner_slug:
        return HttpResponse("Not found", status=404)

    if request.method == "POST":
        answers = {}
        fields = form.fields.all()
        
        for field in fields:
            if field.field_type in ['multiselect', 'checkbox']:
                answers[field.identifier] = request.POST.getlist(field.identifier)
                if field.field_type == 'checkbox' and not answers[field.identifier]:
                    # checkbox not in post means false
                    answers[field.identifier] = False
            else:
                answers[field.identifier] = request.POST.get(field.identifier)
                
        rules = list(form.rules.all())
        decision = evaluate(rules, list(fields), answers)
        
        response = RoutingFormResponse.objects.create(
            form=form,
            answers=answers,
            matched_rule_id=decision.matched_rule_id,
            routed_to_event_type_id=decision.target_event_type_id,
            routed_to_user_id=decision.target_user_id,
            session_id=request.session.session_key,
            referrer=request.META.get('HTTP_REFERER'),
            utm_source=request.GET.get('utm_source'),
            utm_medium=request.GET.get('utm_medium'),
            utm_campaign=request.GET.get('utm_campaign'),
        )
        
        if decision.action == 'show_message':
            return render(request, "routing/public_message.html", {
                "form": form,
                "message": decision.message,
                "is_embed": is_embed
            })
            
        elif decision.action == 'route_to_external_url':
            url = decision.target_url
            if url:
                query_params = urllib.parse.urlencode(answers, doseq=True)
                separator = '&' if '?' in url else '?'
                return redirect(f"{url}{separator}{query_params}")
            return HttpResponse("Configuration error: no target URL.", status=500)
            
        elif decision.action in ['route_to_member', 'route_to_event_type']:
            signer = Signer()
            prefill_data = json.dumps(answers)
            signed_prefill = signer.sign(prefill_data)
            
            target_url = None
            if decision.target_event_type_id:
                # route_to_event_type or route_to_member (where event type is specified)
                event_type = EventType.objects.get(id=decision.target_event_type_id)
                url_name = "bookings:booking_embed" if is_embed else "bookings:booking_page"
                if event_type.team:
                    target_url = reverse(url_name, args=[event_type.team.slug, event_type.slug])
                else:
                    target_url = reverse(url_name, args=[event_type.owner.slug, event_type.slug])
            
            if target_url:
                query_params = urllib.parse.urlencode({
                    'routing_prefill': signed_prefill,
                    'routing_response_id': response.id
                })
                return redirect(f"{target_url}?{query_params}")
                
            return HttpResponse("Configuration error: missing routing target.", status=500)

    # Prevent cookie setting on this response for embeds
    resp = render(request, "routing/public_form.html", {
        "form": form,
        "fields": form.fields.all(),
        "is_embed": is_embed
    })
    
    if is_embed:
        resp.cookies.clear()
        
    return resp


from django.views.generic import DetailView
from django.contrib.auth.mixins import LoginRequiredMixin

class RoutingFormEmbedCodeView(LoginRequiredMixin, DetailView):
    model = RoutingForm
    template_name = "routing/dashboard/embed_code.html"
    context_object_name = "form"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return RoutingForm.objects.filter(owner=self.request.user)

    def get_context_data(self, **kwargs):
        from django.urls import reverse
        context = super().get_context_data(**kwargs)
        form = self.get_object()
        if form.team:
            embed_url = self.request.build_absolute_uri(
                reverse("routing:public_form_embed", kwargs={"owner_slug": form.team.slug, "form_slug": form.slug})
            )
        else:
            embed_url = self.request.build_absolute_uri(
                reverse("routing:public_form_embed", kwargs={"owner_slug": self.request.user.slug, "form_slug": form.slug})
            )
        context["embed_url"] = embed_url
        context["domain"] = self.request.get_host()
        return context
