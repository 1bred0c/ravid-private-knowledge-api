from django.urls import path
from .views import ChatHistoryView, ChatQueryView, ConversationView
urlpatterns = [
    path("chat/conversations/", ConversationView.as_view(), name="conversation-create"),
    path("chat/query/", ChatQueryView.as_view(), name="chat-query"),
    path("chat/history/", ChatHistoryView.as_view(), name="chat-history"),
]
