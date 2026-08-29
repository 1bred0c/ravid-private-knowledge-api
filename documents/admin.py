from django.contrib import admin

from documents.models import Document, DocumentChunk


class DocumentChunkInline(admin.TabularInline):
    model = DocumentChunk
    extra = 0
    fields = ["page_number", "chunk_index", "token_count"]
    readonly_fields = fields
    show_change_link = True


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = [
        "title", "user", "status", "page_count", "chunk_count",
        "ingestion_task_id", "created_at",
    ]
    list_filter = ["status", "mime_type"]
    search_fields = ["title", "original_filename", "user__username", "user__email"]
    readonly_fields = [
        "file_size", "page_count", "chunk_count", "error_message",
        "processed_at", "ingestion_task_id", "created_at", "updated_at",
    ]
    inlines = [DocumentChunkInline]


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = [
        "document", "page_number", "chunk_index", "token_count", "embedding_model",
    ]
    list_filter = ["page_number"]
    search_fields = ["document__title", "content"]
    readonly_fields = ["vector_id", "embedding_model", "created_at"]
