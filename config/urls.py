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

urlpatterns = [
    path('', account_views.home, name='home'),
    path('dashboard/', account_views.dashboard, name='dashboard'),
    path('onboarding/', account_views.onboarding, name='onboarding'),
    path('onboarding/check-slug/', account_views.check_slug, name='check_slug'),
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('', include('apps.accounts.urls')),
    path('demo/', include('apps.demo.urls')),
    path('', include('apps.scheduling.urls')),
    path('', include('apps.bookings.urls')),
]

handler404 = 'apps.demo.views.custom_404'
handler500 = 'apps.demo.views.custom_500'

if settings.DEBUG:
    import debug_toolbar
    urlpatterns += [path('__debug__/', include(debug_toolbar.urls))]
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
