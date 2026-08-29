from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_core.embeddings import Embeddings


class _DeletionOnlyEmbeddings(Embeddings):
    """Placeholder used for vector deletion; deletion must not need an API key."""

    def embed_documents(self, texts):
        raise RuntimeError("Embeddings are not available in deletion-only mode.")

    def embed_query(self, text):
        raise RuntimeError("Embeddings are not available in deletion-only mode.")


def get_embeddings():
    if not settings.OPENROUTER_API_KEY:
        raise ImproperlyConfigured(
            "OPENROUTER_API_KEY is required for document embeddings."
        )
    return OpenAIEmbeddings(
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
        model=settings.OPENROUTER_EMBEDDING_MODEL,
        dimensions=settings.OPENROUTER_EMBEDDING_DIMENSIONS,
        default_headers={
            "HTTP-Referer": settings.OPENROUTER_SITE_URL,
            "X-Title": settings.OPENROUTER_APP_NAME,
        },
        tiktoken_enabled=False,
        check_embedding_ctx_length=False,
    )


def user_collection_name(user_id):
    return f"ravid_user_{str(user_id).replace('-', '')}"


def get_user_vector_store(user_id, *, embeddings=None):
    return PGVector(
        embeddings=embeddings or get_embeddings(),
        connection=settings.LANGCHAIN_PG_CONNECTION,
        collection_name=user_collection_name(user_id),
        collection_metadata={"userId": str(user_id)},
        embedding_length=settings.OPENROUTER_EMBEDDING_DIMENSIONS,
        use_jsonb=True,
        create_extension=False,
    )


def delete_user_vectors(user_id, vector_ids):
    """Delete vectors without requiring the embedding provider configuration."""
    if vector_ids:
        get_user_vector_store(
            user_id, embeddings=_DeletionOnlyEmbeddings()
        ).delete(ids=list(vector_ids))
