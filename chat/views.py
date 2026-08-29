from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import NotFound, PermissionDenied
from usage.services import TokenQuotaExceeded, TokenUsageUnavailable
from .serializers import (
    ChatQueryResponseSerializer,
    ChatQuerySerializer,
    ChatHistoryResponseSerializer,
    ConversationListResponseSerializer,
    ConversationSummarySerializer,
    ConversationCreateSerializer,
)
from .services import ChatServiceUnavailable, answer_query
from .models import ChatConversation

class ConversationView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ConversationListResponseSerializer})
    def get(self, request):
        conversations = ChatConversation.objects.filter(user=request.user).order_by("-updated_at")
        return Response({"conversations": [{
            "id": str(item.id),
            "title": item.title,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "message_count": item.messages.count(),
        } for item in conversations]})

    @extend_schema(request=ConversationCreateSerializer, responses={201: ConversationSummarySerializer})
    def post(self, request):
        serializer = ConversationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = ChatConversation.objects.create(user=request.user, title=serializer.validated_data.get("title", ""))
        return Response({"id": str(conversation.id), "title": conversation.title, "created_at": conversation.created_at}, status=status.HTTP_201_CREATED)

class ChatQueryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=ChatQuerySerializer, responses={200: ChatQueryResponseSerializer})
    def post(self, request):
        serializer = ChatQuerySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            answer, metadata, conversation = answer_query(user=request.user, **serializer.validated_data)
        except PermissionError as error:
            raise PermissionDenied(str(error))
        except NotFound:
            raise
        except TokenQuotaExceeded as error:
            return Response({"error": str(error)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except TokenUsageUnavailable as error:
            return Response({"error": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except ChatServiceUnavailable as error:
            return Response({"error": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"answer": answer, "conversation_id": str(conversation.id), "retrieval_metadata": metadata})


class ChatHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ChatHistoryResponseSerializer})
    def get(self, request):
        conversations = ChatConversation.objects.filter(user=request.user).prefetch_related("messages")
        return Response({"conversations": [{
            "id": str(item.id),
            "title": item.title,
            "messages": [{"id": str(message.id), "role": message.role, "content": message.content, "created_at": message.created_at} for message in item.messages.all()],
        } for item in conversations]})
