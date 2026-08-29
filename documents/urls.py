from django.urls import path

from documents.views import (
    DocumentDetailView,
    DocumentDownloadView,
    DocumentListCreateView,
    DocumentRetryView,
    DocumentStatusView,
    DocumentUploadView,
)


urlpatterns = [
    path("documents/", DocumentListCreateView.as_view(), name="document-list-create"),
    path("documents/upload/", DocumentUploadView.as_view(), name="document-upload"),
    path("documents/status/", DocumentStatusView.as_view(), name="document-status"),
    path("documents/<uuid:document_id>/", DocumentDetailView.as_view(), name="document-detail"),
    path(
        "documents/<uuid:document_id>/download/",
        DocumentDownloadView.as_view(),
        name="document-download",
    ),
    path("documents/<uuid:document_id>/retry/", DocumentRetryView.as_view(), name="document-retry"),
]
