from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import Subscription, SubscriptionPlan
from documents.models import Document
from documents.vector_store import get_embeddings


class FakeVectorStore:
    def add_documents(self, documents, ids):
        self.documents = documents
        self.ids = ids
        return ids

    def delete(self, ids=None, **kwargs):
        return None


class VectorConfigurationTests(SimpleTestCase):
    @override_settings(OPENROUTER_API_KEY="")
    def test_embedding_configuration_fails_clearly_without_api_key(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "OPENROUTER_API_KEY"):
            get_embeddings()


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=False,
)
class DocumentFlowTests(APITestCase):
    def setUp(self):
        self.vector_store_patcher = patch(
            "documents.tasks.get_user_vector_store",
            return_value=FakeVectorStore(),
        )
        self.vector_store_patcher.start()
        self.media_directory = TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_override.enable()
        self.user = get_user_model().objects.create_user(
            username="document-owner",
            email="documents@example.com",
            password="strong-test-password",
        )
        self.plan, _ = SubscriptionPlan.objects.update_or_create(
            code="DOCUMENT_TEST",
            defaults={
                "name": "Document Test",
                "price": 0,
                "duration_days": 30,
                "daily_token_limit": 10000,
                "max_documents": 2,
                "max_file_size_mb": 1,
                "is_active": True,
            },
        )
        self.subscription = Subscription.objects.create(user=self.user, plan=self.plan)
        self.subscription.activate()
        self.client.force_authenticate(self.user)

    def tearDown(self):
        self.vector_store_patcher.stop()
        self.media_override.disable()
        self.media_directory.cleanup()

    def make_pdf(self, name="knowledge.pdf"):
        output = BytesIO()
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): writer._add_object(font)}
                )
            }
        )
        content = DecodedStreamObject()
        content.set_data(
            b"BT /F1 12 Tf 72 720 Td (Private PDF knowledge content.) Tj ET"
        )
        page[NameObject("/Contents")] = writer._add_object(content)
        writer.write(output)
        return SimpleUploadedFile(name, output.getvalue(), content_type="application/pdf")

    def make_text(self, name="knowledge.txt", content="Private knowledge base content."):
        content_type = "text/markdown" if name.endswith(".md") else "text/plain"
        return SimpleUploadedFile(name, content.encode("utf-8"), content_type=content_type)

    def test_upload_is_processed_asynchronously(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("document-upload"),
                {"title": "Knowledge", "file": self.make_text()},
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["message"], "Document uploaded and ingestion started")
        document = Document.objects.get(id=response.data["document_id"])
        self.assertEqual(response.data["task_id"], document.ingestion_task_id)
        self.assertEqual(document.status, Document.Status.READY)
        self.assertEqual(document.page_count, 1)
        self.assertEqual(document.chunk_count, 1)
        chunk = document.chunks.get()
        self.assertIsNotNone(chunk.vector_id)
        self.assertEqual(chunk.metadata["userId"], str(self.user.id))
        self.assertEqual(chunk.metadata["documentId"], str(document.id))

        status_response = self.client.get(
            reverse("document-status"),
            {"task_id": document.ingestion_task_id},
        )
        self.assertEqual(status_response.status_code, status.HTTP_200_OK)
        self.assertEqual(status_response.data["status"], "SUCCESS")

    def test_upload_rejects_non_pdf_content(self):
        uploaded_file = SimpleUploadedFile(
            "fake.pdf",
            b"not a pdf",
            content_type="application/pdf",
        )

        response = self.client.post(
            reverse("document-upload"),
            {"file": uploaded_file},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    def test_active_subscription_is_required(self):
        self.subscription.status = Subscription.Status.CANCELLED
        self.subscription.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            reverse("document-upload"),
            {"file": self.make_pdf()},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_document_limit_is_enforced(self):
        for index in range(2):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("document-upload"),
                    {"file": self.make_text(f"knowledge-{index}.txt")},
                    format="multipart",
                )
            self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

        response = self.client.post(
            reverse("document-upload"),
            {"file": self.make_text("too-many.txt")},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_user_cannot_read_another_users_document(self):
        other_user = get_user_model().objects.create_user(
            username="other-owner",
            email="other@example.com",
            password="strong-test-password",
        )
        document = Document.objects.create(
            user=other_user,
            title="Private",
            file=self.make_pdf("private.pdf"),
            original_filename="private.pdf",
            mime_type="application/pdf",
            file_size=100,
        )

        response = self.client.get(
            reverse("document-detail", kwargs={"document_id": document.id})
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        download_response = self.client.get(
            reverse("document-download", kwargs={"document_id": document.id})
        )
        self.assertEqual(download_response.status_code, status.HTTP_404_NOT_FOUND)

    def test_markdown_upload_is_supported(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("document-upload"),
                {"file": self.make_text("notes.md", "# Policy\nCancel with notice.")},
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        document = Document.objects.get(id=response.data["document_id"])
        self.assertEqual(document.status, Document.Status.READY)
        self.assertEqual(document.chunks.count(), 1)

    def test_pdf_text_is_extracted_chunked_and_indexed(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("document-upload"),
                {"file": self.make_pdf()},
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        document = Document.objects.get(id=response.data["document_id"])
        self.assertEqual(document.status, Document.Status.READY)
        self.assertEqual(document.page_count, 1)
        self.assertIn("Private PDF knowledge content", document.chunks.get().content)

    def test_status_api_maps_processing_and_failure_states(self):
        document = Document.objects.create(
            user=self.user,
            title="Status Test",
            file=self.make_text("status.txt"),
            original_filename="status.txt",
            mime_type="text/plain",
            file_size=10,
            ingestion_task_id="status-task-id",
        )

        processing = self.client.get(
            reverse("document-status"),
            {"task_id": document.ingestion_task_id},
        )
        self.assertEqual(processing.data["status"], "PROCESSING")

        document.status = Document.Status.FAILED
        document.error_message = "Failed to parse document content."
        document.save(update_fields=["status", "error_message", "updated_at"])
        failed = self.client.get(
            reverse("document-status"),
            {"task_id": document.ingestion_task_id},
        )
        self.assertEqual(failed.data["status"], "FAILURE")
        self.assertEqual(failed.data["error"], "Failed to parse document content.")
