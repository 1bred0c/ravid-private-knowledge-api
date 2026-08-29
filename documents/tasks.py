from pathlib import Path
import uuid

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from langchain_core.documents import Document as LangChainDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from documents.models import Document, DocumentChunk
from documents.vector_store import get_user_vector_store


@shared_task(bind=True, name="documents.process_document")
def process_document(self, document_id):
    task_id = self.request.id
    vector_ids = []

    with transaction.atomic():
        document = Document.objects.select_for_update().filter(id=document_id).first()
        if (
            not document
            or document.ingestion_task_id != task_id
            or document.status not in {Document.Status.UPLOADED, Document.Status.FAILED}
        ):
            return
        document.status = Document.Status.PROCESSING
        document.error_message = ""
        document.save(update_fields=["status", "error_message", "updated_at"])

    try:
        source_documents, page_count = extract_source_documents(document)
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.RAG_CHUNK_SIZE,
            chunk_overlap=settings.RAG_CHUNK_OVERLAP,
            add_start_index=True,
        )
        split_documents = splitter.split_documents(source_documents)
        if not split_documents:
            raise ValueError("The document does not contain extractable text.")

        vector_ids = [str(uuid.uuid4()) for _ in split_documents]
        chunk_rows = []
        for index, (chunk, vector_id) in enumerate(zip(split_documents, vector_ids)):
            chunk.metadata.update(
                {
                    "userId": str(document.user_id),
                    "documentId": str(document.id),
                    "chunkIndex": index,
                    "originalFilename": document.original_filename,
                }
            )
            chunk_rows.append(
                DocumentChunk(
                    document=document,
                    content=chunk.page_content,
                    page_number=int(chunk.metadata.get("pageNumber", 1)),
                    chunk_index=index,
                    token_count=max(len(chunk.page_content) // 4, 1),
                    vector_id=vector_id,
                    embedding_model=settings.OPENROUTER_EMBEDDING_MODEL,
                    metadata=chunk.metadata,
                )
            )

        vector_store = get_user_vector_store(document.user_id)
        old_vector_ids = list(
            document.chunks.exclude(vector_id__isnull=True).values_list("vector_id", flat=True)
        )
        if old_vector_ids:
            vector_store.delete(ids=old_vector_ids)
        vector_store.add_documents(split_documents, ids=vector_ids)

        with transaction.atomic():
            locked = Document.objects.select_for_update().get(id=document_id)
            if locked.ingestion_task_id != task_id:
                vector_store.delete(ids=vector_ids)
                return
            locked.chunks.all().delete()
            DocumentChunk.objects.bulk_create(chunk_rows)
            locked.status = Document.Status.READY
            locked.page_count = page_count
            locked.chunk_count = len(chunk_rows)
            locked.error_message = ""
            locked.processed_at = timezone.now()
            locked.save(
                update_fields=[
                    "status",
                    "page_count",
                    "chunk_count",
                    "error_message",
                    "processed_at",
                    "updated_at",
                ]
            )
    except Exception as error:
        if vector_ids:
            try:
                get_user_vector_store(document.user_id).delete(ids=vector_ids)
            except Exception:
                pass
        Document.objects.filter(id=document_id, ingestion_task_id=task_id).update(
            status=Document.Status.FAILED,
            error_message=str(error)[:2000],
            processed_at=timezone.now(),
            updated_at=timezone.now(),
        )
        raise


def extract_source_documents(document):
    suffix = Path(document.original_filename).suffix.lower()
    if suffix == ".pdf":
        with document.file.open("rb") as source_file:
            reader = PdfReader(source_file)
            pages = [
                LangChainDocument(
                    page_content=page.extract_text() or "",
                    metadata={"pageNumber": page_number, "sourceType": "pdf"},
                )
                for page_number, page in enumerate(reader.pages, start=1)
            ]
        return pages, len(pages)

    if suffix in {".txt", ".md", ".markdown"}:
        with document.file.open("rb") as source_file:
            content = source_file.read().decode("utf-8-sig")
        return [
            LangChainDocument(
                page_content=content,
                metadata={"pageNumber": 1, "sourceType": suffix.lstrip(".")},
            )
        ], 1

    raise ValueError("Unsupported document format.")
