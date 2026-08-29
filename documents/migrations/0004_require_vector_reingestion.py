from django.db import migrations
from django.db.models import Q


def mark_unindexed_documents_for_retry(apps, schema_editor):
    Document = apps.get_model("documents", "Document")
    Document.objects.filter(status="READY").filter(
        Q(chunks__isnull=True) | Q(chunks__vector_id__isnull=True)
    ).distinct().update(
        status="FAILED",
        error_message=(
            "Re-ingestion required: this document was processed before vector "
            "embedding support was enabled."
        ),
    )


class Migration(migrations.Migration):
    dependencies = [("documents", "0003_document_ingestion_task_id_and_more")]

    operations = [migrations.RunPython(mark_unindexed_documents_for_retry, migrations.RunPython.noop)]
