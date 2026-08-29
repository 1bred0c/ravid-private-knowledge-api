from django.http import FileResponse, Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from documents.models import Document
from documents.serializers import (
    DocumentSerializer,
    DocumentStatusSerializer,
    DocumentUploadResponseSerializer,
    DocumentUploadSerializer,
)
from documents.services import create_document, delete_document, retry_document


class DocumentListCreateView(generics.ListAPIView):
    serializer_class = DocumentSerializer
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        return Document.objects.filter(user=self.request.user)

    @extend_schema(
        request=DocumentUploadSerializer,
        responses={status.HTTP_202_ACCEPTED: DocumentUploadResponseSerializer},
    )
    def post(self, request):
        return create_upload_response(request)


class DocumentUploadView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        request=DocumentUploadSerializer,
        responses={status.HTTP_202_ACCEPTED: DocumentUploadResponseSerializer},
    )
    def post(self, request):
        return create_upload_response(request)


def create_upload_response(request):
    serializer = DocumentUploadSerializer(data=request.data)
    if not serializer.is_valid():
        first_error = next(iter(serializer.errors.values()))
        if isinstance(first_error, (list, tuple)):
            first_error = first_error[0]
        return Response({"error": str(first_error)}, status=status.HTTP_400_BAD_REQUEST)
    document = create_document(
        user=request.user,
        uploaded_file=serializer.validated_data["file"],
        title=serializer.validated_data.get("title", ""),
    )
    return Response(
        {
            "message": "Document uploaded and ingestion started",
            "document_id": document.id,
            "task_id": document.ingestion_task_id,
        },
        status=status.HTTP_202_ACCEPTED,
    )


class DocumentStatusView(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="task_id",
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
            )
        ],
        responses=DocumentStatusSerializer,
    )
    def get(self, request):
        task_id = request.query_params.get("task_id", "").strip()
        if not task_id:
            raise ValidationError({"task_id": "This query parameter is required."})
        document = Document.objects.filter(
            ingestion_task_id=task_id,
            user=request.user,
        ).first()
        if not document:
            raise NotFound("Ingestion task was not found.")

        response = {"task_id": task_id}
        if document.status == Document.Status.READY:
            response.update(
                {
                    "status": "SUCCESS",
                    "message": (
                        "Document successfully parsed, embedded, and indexed "
                        "in vector storage."
                    ),
                }
            )
        elif document.status == Document.Status.FAILED:
            response.update(
                {
                    "status": "FAILURE",
                    "error": document.error_message or "Failed to parse document content.",
                }
            )
        else:
            response["status"] = "PROCESSING"
        return Response(response)


class DocumentDetailView(generics.RetrieveDestroyAPIView):
    serializer_class = DocumentSerializer
    lookup_url_kwarg = "document_id"

    def get_queryset(self):
        return Document.objects.filter(user=self.request.user)

    def perform_destroy(self, instance):
        delete_document(instance)


class DocumentDownloadView(APIView):
    @extend_schema(responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    def get(self, request, document_id):
        document = Document.objects.filter(id=document_id, user=request.user).first()
        if not document:
            raise Http404
        return FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=document.original_filename,
            content_type=document.mime_type,
        )


class DocumentRetryView(APIView):
    @extend_schema(request=None, responses=DocumentSerializer)
    def post(self, request, document_id):
        document = retry_document(user=request.user, document_id=document_id)
        if not document:
            return Response(
                {"detail": "Document was not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(DocumentSerializer(document, context={"request": request}).data)
