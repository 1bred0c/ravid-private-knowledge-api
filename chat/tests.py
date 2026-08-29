from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from rest_framework import status
from rest_framework.test import APITestCase

from .models import ChatConversation, ChatMessage
from .serializers import ChatQuerySerializer
from .services import (
    ChatServiceUnavailable,
    _estimated_reservation,
    _generate_answer,
    _generate_hypothetical_passage,
    _message_text,
    prepare_retrieval_query,
)


class ChatQuerySerializerTests(SimpleTestCase):
    payload = {
        "query": "What is the policy?",
        "conversation_id": "3ed82d0b-7d16-4c8d-b70c-a3ec1ba6f7e2",
        "document_ids": ["9a9cecc0-d685-4cb7-ab46-205016b6ee59"],
    }

    def test_hyde_defaults_to_false(self):
        serializer = ChatQuerySerializer(data=self.payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertFalse(serializer.validated_data["use_hyde"])

    def test_hyde_can_be_enabled(self):
        serializer = ChatQuerySerializer(data={**self.payload, "use_hyde": True})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertTrue(serializer.validated_data["use_hyde"])


class ChatHistoryTests(APITestCase):
    def test_assistant_retrieval_metadata_is_returned_for_citations(self):
        user = get_user_model().objects.create_user(
            username="citation-user",
            password="strong-test-password",
        )
        conversation = ChatConversation.objects.create(user=user, title="Sources")
        metadata = {
            "mode": "standard",
            "hypothetical_passage": None,
            "retrieved_chunks_count": 1,
            "source_chunks": [
                {
                    "document_id": "9a9cecc0-d685-4cb7-ab46-205016b6ee59",
                    "filename": "source.pdf",
                    "page_number": 1,
                    "chunk_index": 0,
                    "score": None,
                    "content": "A cited passage.",
                }
            ],
        }
        ChatMessage.objects.create(
            conversation=conversation,
            role=ChatMessage.Role.USER,
            content="Question",
            metadata={"document_ids": [metadata["source_chunks"][0]["document_id"]], "use_hyde": False},
        )
        ChatMessage.objects.create(
            conversation=conversation,
            role=ChatMessage.Role.ASSISTANT,
            content="Answer",
            metadata=metadata,
        )
        self.client.force_authenticate(user)

        response = self.client.get(reverse("chat-history"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        messages = response.data["conversations"][0]["messages"]
        self.assertEqual(messages[0]["metadata"]["document_ids"], [metadata["source_chunks"][0]["document_id"]])
        self.assertEqual(messages[1]["metadata"], metadata)


@override_settings(
    OPENROUTER_HYDE_MODEL="test-model",
    HYDE_MAX_OUTPUT_TOKENS=200,
    HYDE_TIMEOUT_SECONDS=1,
    RAG_RETRIEVAL_K=5,
    RAG_CHUNK_SIZE=1000,
    RAG_MAX_OUTPUT_TOKENS=1000,
)
class HydePipelineTests(SimpleTestCase):
    @patch("chat.services.get_chat_model")
    def test_generates_hypothetical_passage_with_lcel(self, chat_model):
        chat_model.return_value = RunnableLambda(
            lambda _prompt: AIMessage(content="An ideal policy passage.")
        )
        passage, tokens = _generate_hypothetical_passage("What is the policy?")
        self.assertEqual(passage, "An ideal policy passage.")
        self.assertEqual(tokens, 0)

    @patch("chat.services._generate_hypothetical_passage")
    def test_hyde_failure_falls_back_to_raw_query(self, generate):
        generate.side_effect = TimeoutError("provider timeout")
        result = prepare_retrieval_query("original question", True)
        self.assertEqual(result[0], "original question")
        self.assertEqual(result[1], "standard")
        self.assertEqual(result[4], "TimeoutError")

    def test_hyde_reserves_more_tokens_than_standard(self):
        self.assertGreater(
            _estimated_reservation("question", True),
            _estimated_reservation("question", False),
        )


class AnswerGenerationTests(SimpleTestCase):
    def test_message_text_supports_content_blocks(self):
        message = AIMessage(
            content=[
                {"type": "text", "text": "First paragraph."},
                {"type": "output_text", "text": "Second paragraph."},
            ]
        )

        self.assertEqual(
            _message_text(message),
            "First paragraph.\nSecond paragraph.",
        )

    @patch("chat.services.get_chat_model")
    def test_empty_answer_is_retried_before_returning(self, chat_model):
        responses = iter([AIMessage(content=""), AIMessage(content="Recovered answer.")])
        chat_model.return_value = RunnableLambda(lambda _prompt: next(responses))

        answer, _tokens = _generate_answer("Context", "Question")

        self.assertEqual(answer, "Recovered answer.")
        self.assertEqual(chat_model.call_count, 2)

    @patch("chat.services.get_chat_model")
    def test_two_empty_answers_raise_service_error(self, chat_model):
        chat_model.return_value = RunnableLambda(lambda _prompt: AIMessage(content=""))

        with self.assertRaisesMessage(ChatServiceUnavailable, "empty answer"):
            _generate_answer("Context", "Question")
