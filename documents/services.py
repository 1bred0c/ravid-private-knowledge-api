from pathlib import Path
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from billing.services import get_current_subscription
from documents.models import Document


def get_active_subscription(user):
    subscription = get_current_subscription(user)
    if not subscription or not subscription.is_effectively_active:
        raise PermissionDenied("An active subscription is required.")
    return subscription


@transaction.atomic
def create_document(*, user, uploaded_file, title=""):
    locked_user = get_user_model().objects.select_for_update().get(pk=user.pk)
    subscription = get_active_subscription(locked_user)

    current_count = Document.objects.filter(user=locked_user).count()
    if current_count >= subscription.max_documents:
        raise ValidationError(
            {"file": f"Your plan allows at most {subscription.max_documents} documents."}
        )

    maximum_bytes = subscription.max_file_size_mb * 1024 * 1024
    if uploaded_file.size > maximum_bytes:
        raise ValidationError(
            {"file": f"Your plan allows files up to {subscription.max_file_size_mb} MB."}
        )

    document = Document.objects.create(
        user=locked_user,
        title=title.strip() or Path(uploaded_file.name).stem[:255],
        file=uploaded_file,
        original_filename=Path(uploaded_file.name).name[:255],
        mime_type=getattr(uploaded_file, "content_type", "application/pdf") or "application/pdf",
        file_size=uploaded_file.size,
        ingestion_task_id=str(uuid.uuid4()),
    )
    transaction.on_commit(
        lambda: enqueue_document(document.id, document.ingestion_task_id)
    )
    return document


def enqueue_document(document_id, task_id):
    from documents.tasks import process_document

    try:
        process_document.apply_async(args=[str(document_id)], task_id=task_id)
    except Exception as error:
        Document.objects.filter(
            id=document_id,
            ingestion_task_id=task_id,
            status=Document.Status.UPLOADED,
        ).update(
            status=Document.Status.FAILED,
            error_message=f"Could not enqueue document: {error}"[:2000],
        )


@transaction.atomic
def retry_document(*, user, document_id):
    document = Document.objects.select_for_update().filter(id=document_id, user=user).first()
    if not document:
        return None
    get_active_subscription(user)
    if document.status != Document.Status.FAILED:
        raise ValidationError("Only failed documents can be retried.")
    document.status = Document.Status.UPLOADED
    document.error_message = ""
    document.ingestion_task_id = str(uuid.uuid4())
    document.save(
        update_fields=["status", "error_message", "ingestion_task_id", "updated_at"]
    )
    transaction.on_commit(
        lambda: enqueue_document(document.id, document.ingestion_task_id)
    )
    return document


@transaction.atomic
def delete_document(document):
    from documents.vector_store import delete_user_vectors

    storage = document.file.storage
    file_name = document.file.name
    user_id = document.user_id
    vector_ids = list(
        document.chunks.exclude(vector_id__isnull=True).values_list("vector_id", flat=True)
    )
    document.delete()

    def cleanup():
        storage.delete(file_name)
        if vector_ids:
            try:
                delete_user_vectors(user_id, vector_ids)
            except Exception:
                # The database content is already private/inaccessible. A later
                # maintenance task can remove orphaned vector rows if the
                # embedding provider configuration is temporarily unavailable.
                pass

    transaction.on_commit(cleanup)
