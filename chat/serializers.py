from rest_framework import serializers

class ChatQuerySerializer(serializers.Serializer):
    query = serializers.CharField(max_length=4000, trim_whitespace=True)
    document_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=50)
    conversation_id = serializers.UUIDField()
    use_hyde = serializers.BooleanField(required=False, default=False)

    def validate_query(self, value):
        if not value.strip():
            raise serializers.ValidationError("Query must not be empty.")
        return value.strip()


class ConversationCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class SourceChunkSerializer(serializers.Serializer):
    document_id = serializers.UUIDField()
    filename = serializers.CharField()
    page_number = serializers.IntegerField(allow_null=True)
    chunk_index = serializers.IntegerField(allow_null=True)
    score = serializers.FloatField(allow_null=True)
    content = serializers.CharField()


class RetrievalMetadataSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=["standard", "hyde"])
    hypothetical_passage = serializers.CharField(allow_null=True)
    retrieved_chunks_count = serializers.IntegerField()
    source_chunks = SourceChunkSerializer(many=True)
    hyde_fallback = serializers.BooleanField(required=False)
    fallback_reason = serializers.CharField(required=False)


class ChatQueryResponseSerializer(serializers.Serializer):
    answer = serializers.CharField()
    conversation_id = serializers.UUIDField()
    retrieval_metadata = RetrievalMetadataSerializer()


class ConversationSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField(required=False)
    message_count = serializers.IntegerField(required=False)


class ConversationListResponseSerializer(serializers.Serializer):
    conversations = ConversationSummarySerializer(many=True)


class ChatMessageSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    role = serializers.CharField()
    content = serializers.CharField()
    created_at = serializers.DateTimeField()


class ConversationHistorySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    messages = ChatMessageSerializer(many=True)


class ChatHistoryResponseSerializer(serializers.Serializer):
    conversations = ConversationHistorySerializer(many=True)
