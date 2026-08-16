from django.urls import path
from . import views

app_name = "clients"

urlpatterns = [
    path("", views.ClientListView.as_view(), name="list"),
    path("<int:pk>/", views.ClientDetailView.as_view(), name="detail"),
    path("export/", views.ClientExportView.as_view(), name="export"),
    path("import/", views.ClientImportView.as_view(), name="import"),
    path("<int:pk>/book/", views.ClientBookOnBehalfView.as_view(), name="book_on_behalf"),
]
