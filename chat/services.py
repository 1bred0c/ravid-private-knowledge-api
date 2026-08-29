import logging
import math

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from rest_framework.exceptions import NotFound

from billing.services import get_current_subscription
from documents.models import Document
from documents.vector_store import get_user_vector_store
from usage.services import (
    TokenUsageUnavailable,
    commit_token_usage,
    release_token_reservation,
    reserve_tokens,
)
from .models import ChatConversation, ChatMessage

logger = logging.getLogger(__name__)


class ChatServiceUnavailable(RuntimeError):
    pass


HYDE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Write a short hypothetical passage that would ideally answer the user's "
            "question. It is only a semantic-search aid, so do not mention uncertainty, "
            "sources, or that the passage is hypothetical.",
        ),
        ("human", "Question: {question}"),
    ]
)

ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Answer only from the supplied real document context. If the answer is not "
            "present, say so clearly. Do not treat a hypothetical passage as evidence and "
            "do not invent facts.\n\nReal document context:\n{context}",
        ),
        ("human", "Question: {question}"),
    ]
)


def get_chat_model(*, model=None, max_tokens=None, timeout=None, max_retries=2):
    if not settings.OPENROUTER_API_KEY:
        raise ChatServiceUnavailable("OPENROUTER_API_KEY is required for chat.")
    return ChatOpenAI(
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
        model=model or settings.OPENROUTER_CHAT_MODEL,
        temperature=0,
        max_tokens=max_tokens or settings.RAG_MAX_OUTPUT_TOKENS,
        timeout=timeout,
        max_retries=max_retries,
        default_headers={
            "HTTP-Referer": settings.OPENROUTER_SITE_URL,
            "X-Title": settings.OPENROUTER_APP_NAME,
        },
    )


def _usage_tokens(message):
    usage = getattr(message, "usage_metadata", None) or {}
    if not usage:
        usage = (getattr(message, "response_metadata", None) or {}).get(
            "token_usage", {}
        )
    return int(usage.get("total_tokens") or usage.get("total") or 0)


def _message_text(message):
    """Normalize text-only and block-based LangChain/OpenAI responses."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(part.strip() for part in parts if part.strip()).strip()


def _generate_answer(context, query):
    response = (ANSWER_PROMPT | get_chat_model()).invoke(
        {"context": context, "question": query}
    )
    answer = _message_text(response)
    total_tokens = _usage_tokens(response)
    if answer:
        return answer, total_tokens

    logger.warning(
        "Chat provider returned an empty answer; retrying with low reasoning",
        extra={
            "finish_reason": (getattr(response, "response_metadata", None) or {}).get(
                "finish_reason"
            ),
            "model": (getattr(response, "response_metadata", None) or {}).get(
                "model_name"
            ),
        },
    )
    retry_response = (
        ANSWER_PROMPT
        | get_chat_model(
            max_retries=0,
            reasoning={"effort": "low", "exclude": True},
        )
    ).invoke({"context": context, "question": query})
    total_tokens += _usage_tokens(retry_response)
    answer = _message_text(retry_response)
    if not answer:
        raise ChatServiceUnavailable(
            "The AI provider returned an empty answer. Please try again."
        )
    return answer, total_tokens


def _estimated_reservation(query, use_hyde):
    context_characters = settings.RAG_RETRIEVAL_K * settings.RAG_CHUNK_SIZE
    estimated_input = math.ceil((context_characters + len(query)) / 4) + 300
    total = estimated_input + settings.RAG_MAX_OUTPUT_TOKENS
    if use_hyde:
        total += settings.HYDE_MAX_OUTPUT_TOKENS + math.ceil(len(query) / 4) + 100
    return total


def _generate_hypothetical_passage(query):
    chain = HYDE_PROMPT | get_chat_model(
        model=settings.OPENROUTER_HYDE_MODEL,
        max_tokens=settings.HYDE_MAX_OUTPUT_TOKENS,
        timeout=settings.HYDE_TIMEOUT_SECONDS,
        max_retries=0,
    )
    message = chain.invoke({"question": query})
    passage = StrOutputParser().invoke(message).strip()
    if not passage:
        raise ValueError("HyDE generation returned an empty passage.")
    return passage, _usage_tokens(message)


def prepare_retrieval_query(query, use_hyde):
    if not use_hyde:
        return query, "standard", None, 0, None
    try:
        passage, tokens = _generate_hypothetical_passage(query)
        return passage, "hyde", passage, tokens, None
    except Exception as error:
        return query, "standard", None, 0, type(error).__name__


def _source_metadata(documents):
    return [
        {
            "document_id": str(doc.metadata.get("documentId")),
            "filename": doc.metadata.get("originalFilename"),
            "page_number": doc.metadata.get("pageNumber"),
            "chunk_index": doc.metadata.get("chunkIndex"),
            "score": None,
            "content": doc.page_content,
        }
        for doc in documents
    ]


def answer_query(*, user, query, document_ids, conversation_id, use_hyde=False):
    subscription = get_current_subscription(user)
    if not subscription or not subscription.is_effectively_active:
        raise PermissionError("An active subscription is required.")

    selected = list(
        Document.objects.filter(
            id__in=document_ids,
            user=user,
            status=Document.Status.READY,
        )
    )
    if len(selected) != len(set(document_ids)):
        raise NotFound(
            "One or more selected documents are not ready or do not belong to you."
        )

    conversation = ChatConversation.objects.filter(
        id=conversation_id, user=user
    ).first()
    if not conversation:
        raise NotFound("Conversation not found.")

    reservation = reserve_tokens(
        subscription, _estimated_reservation(query, use_hyde)
    )
    hypothetical_passage = None
    hyde_tokens = 0
    fallback_reason = None
    retrieval_query = query
    mode = "standard"

    try:
        (
            retrieval_query,
            mode,
            hypothetical_passage,
            hyde_tokens,
            fallback_reason,
        ) = prepare_retrieval_query(query, use_hyde)
        if fallback_reason:
            logger.warning(
                "HyDE generation failed; using standard retrieval",
                extra={
                    "conversation_id": str(conversation.id),
                    "error_type": fallback_reason,
                },
            )

        retriever = get_user_vector_store(user.id).as_retriever(
            search_kwargs={
                "k": settings.RAG_RETRIEVAL_K,
                "filter": {
                    "documentId": {
                        "$in": [str(document.id) for document in selected]
                    }
                },
            }
        )
        retrieved_documents = retriever.invoke(retrieval_query)
        context = "\n\n".join(
            f"[Source {index}] {doc.page_content}"
            for index, doc in enumerate(retrieved_documents, 1)
        )
        answer, answer_tokens = _generate_answer(context, query)
        if answer_tokens <= 0:
            answer_tokens = max(1, len(answer) // 4)
        actual_tokens = min(hyde_tokens + answer_tokens, reservation.tokens)
        commit_token_usage(reservation, actual_tokens)
    except TokenUsageUnavailable:
        raise
    except ChatServiceUnavailable:
        try:
            release_token_reservation(reservation)
        except TokenUsageUnavailable:
            logger.exception("Could not release chat token reservation")
        raise
    except Exception as error:
        try:
            release_token_reservation(reservation)
        except TokenUsageUnavailable:
            logger.exception("Could not release chat token reservation")
        raise ChatServiceUnavailable(
            "The RAG service is temporarily unavailable."
        ) from error

    metadata = {
        "mode": mode,
        "hypothetical_passage": hypothetical_passage,
        "retrieved_chunks_count": len(retrieved_documents),
        "source_chunks": _source_metadata(retrieved_documents),
    }
    if fallback_reason:
        metadata["hyde_fallback"] = True
        metadata["fallback_reason"] = fallback_reason

    with transaction.atomic():
        ChatMessage.objects.create(
            conversation=conversation,
            role=ChatMessage.Role.USER,
            content=query,
            metadata={
                "document_ids": [str(document_id) for document_id in document_ids],
                "use_hyde": use_hyde,
            },
        )
        ChatMessage.objects.create(
            conversation=conversation,
            role=ChatMessage.Role.ASSISTANT,
            content=answer,
            token_count=actual_tokens,
            metadata=metadata,
        )
        conversation.updated_at = timezone.now()
        conversation.save(update_fields=["updated_at"])
    return answer, metadata, conversation
