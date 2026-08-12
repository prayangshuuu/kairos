"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from apps.accounts import views as account_views
from apps.core.views import healthz, readyz, custom_bad_request, custom_permission_denied, custom_page_not_found, custom_server_error, resend_webhook

handler400 = 'apps.core.views.custom_bad_request'
handler403 = 'apps.core.views.custom_permission_denied'
handler404 = 'apps.core.views.custom_page_not_found'
handler500 = 'apps.core.views.custom_server_error'

urlpatterns = [
    path('', account_views.home, name='home'),
    path('dashboard/', account_views.dashboard, name='dashboard'),
    path('dashboard/integrations/', include('apps.integrations.urls')),
    path('onboarding/', account_views.onboarding, name='onboarding'),
    path('onboarding/check-slug/', account_views.check_slug, name='check_slug'),
    path('healthz/', healthz, name='healthz'),
    path('readyz/', readyz, name='readyz'),
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('', include('apps.accounts.urls')),
    path('payments/', include('apps.payments.urls')),
    path('', include('apps.subscriptions.urls')),
    path('', include('apps.scheduling.urls')),
    path('', include('apps.bookings.urls')),
    path('webhooks/resend/', resend_webhook, name='resend_webhook'),
]


handler404 = 'apps.core.views.custom_404'
handler500 = 'apps.core.views.custom_500'

if settings.DEBUG:
    import debug_toolbar
    urlpatterns += [path('__debug__/', include(debug_toolbar.urls))]
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
