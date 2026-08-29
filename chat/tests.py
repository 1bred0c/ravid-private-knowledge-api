from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from .serializers import ChatQuerySerializer
from .services import (
    _estimated_reservation,
    _generate_hypothetical_passage,
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
