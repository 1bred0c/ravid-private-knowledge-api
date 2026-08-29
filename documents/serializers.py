from pathlib import Path

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from django.urls import reverse

from documents.models import Document


class DocumentSerializer(serializers.ModelSerializer):
    originalFilename = serializers.CharField(source="original_filename")
    mimeType = serializers.CharField(source="mime_type")
    fileSize = serializers.IntegerField(source="file_size")
    pageCount = serializers.IntegerField(source="page_count")
    chunkCount = serializers.IntegerField(source="chunk_count")
    errorMessage = serializers.CharField(source="error_message")
    processedAt = serializers.DateTimeField(source="processed_at")
    createdAt = serializers.DateTimeField(source="created_at")
    updatedAt = serializers.DateTimeField(source="updated_at")
    ingestionTaskId = serializers.CharField(source="ingestion_task_id", allow_null=True)
    fileUrl = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "id", "title", "status", "originalFilename", "mimeType",
            "fileSize", "fileUrl", "pageCount", "chunkCount", "errorMessage",
            "processedAt", "createdAt", "updatedAt", "ingestionTaskId",
        ]

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_fileUrl(self, document):
        request = self.context.get("request")
        if not document.file or not request:
            return None
        return request.build_absolute_uri(
            reverse("document-download", kwargs={"document_id": document.id})
        )


class DocumentUploadSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    file = serializers.FileField(write_only=True)

    def validate_file(self, uploaded_file):
        suffix = Path(uploaded_file.name).suffix.lower()
        if suffix not in {".pdf", ".txt", ".md", ".markdown"}:
            raise serializers.ValidationError(
                "Invalid file format. Only PDF, TXT, and Markdown files are allowed."
            )
        content_type = getattr(uploaded_file, "content_type", "")
        allowed_types = {
            ".pdf": {"application/pdf", "application/x-pdf"},
            ".txt": {"text/plain"},
            ".md": {"text/markdown", "text/plain", "application/octet-stream"},
            ".markdown": {"text/markdown", "text/plain", "application/octet-stream"},
        }
        if content_type and content_type not in allowed_types[suffix]:
            raise serializers.ValidationError("The file content type does not match its extension.")
        if suffix == ".pdf":
            position = uploaded_file.tell()
            header = uploaded_file.read(5)
            uploaded_file.seek(position)
            if header != b"%PDF-":
                raise serializers.ValidationError("The uploaded file is not a valid PDF.")
        return uploaded_file


class DocumentUploadResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    document_id = serializers.UUIDField()
    task_id = serializers.CharField()


class DocumentStatusSerializer(serializers.Serializer):
    task_id = serializers.CharField()
    status = serializers.ChoiceField(choices=["PROCESSING", "SUCCESS", "FAILURE"])
    message = serializers.CharField(required=False)
    error = serializers.CharField(required=False)
