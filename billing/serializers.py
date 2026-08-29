from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from billing.models import PaymentTransaction, Subscription, SubscriptionPlan
from usage.services import get_usage_summary


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    durationDays = serializers.IntegerField(source="duration_days")
    dailyTokenLimit = serializers.IntegerField(source="daily_token_limit")
    maxDocuments = serializers.IntegerField(source="max_documents")
    maxFileSizeMb = serializers.IntegerField(source="max_file_size_mb")
    isActive = serializers.BooleanField(source="is_active")

    class Meta:
        model = SubscriptionPlan
        fields = [
            "id",
            "code",
            "name",
            "description",
            "price",
            "currency",
            "durationDays",
            "dailyTokenLimit",
            "maxDocuments",
            "maxFileSizeMb",
            "isActive",
        ]


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPlanSerializer(read_only=True)
    startsAt = serializers.DateTimeField(source="starts_at", allow_null=True)
    expiresAt = serializers.DateTimeField(source="expires_at", allow_null=True)
    cancelledAt = serializers.DateTimeField(source="cancelled_at", allow_null=True)
    dailyTokenLimit = serializers.IntegerField(source="daily_token_limit")
    maxDocuments = serializers.IntegerField(source="max_documents")
    maxFileSizeMb = serializers.IntegerField(source="max_file_size_mb")
    tokensUsedToday = serializers.SerializerMethodField()
    tokensRemainingToday = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = [
            "id",
            "status",
            "plan",
            "startsAt",
            "expiresAt",
            "cancelledAt",
            "dailyTokenLimit",
            "maxDocuments",
            "maxFileSizeMb",
            "tokensUsedToday",
            "tokensRemainingToday",
        ]

    def _usage(self, subscription):
        cache = getattr(self, "_usage_cache", {})
        if subscription.id not in cache:
            cache[subscription.id] = get_usage_summary(subscription)
            self._usage_cache = cache
        return cache[subscription.id]

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_tokensUsedToday(self, subscription):
        return self._usage(subscription)["tokensUsedToday"]

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_tokensRemainingToday(self, subscription):
        return self._usage(subscription)["tokensRemainingToday"]


class SubscribeRequestSerializer(serializers.Serializer):
    planId = serializers.PrimaryKeyRelatedField(
        source="plan",
        queryset=SubscriptionPlan.objects.filter(is_active=True),
    )


class SubscribeResponseSerializer(serializers.Serializer):
    subscription = SubscriptionSerializer()
    paymentRequired = serializers.BooleanField()


class CurrentSubscriptionResponseSerializer(serializers.Serializer):
    subscription = SubscriptionSerializer(allow_null=True)


class CreatePaymentRequestSerializer(serializers.Serializer):
    subscriptionId = serializers.UUIDField()


class PaymentTransactionSerializer(serializers.ModelSerializer):
    subscriptionId = serializers.UUIDField(source="subscription_id")
    paymentUrl = serializers.URLField(source="payment_url")
    expiresAt = serializers.DateTimeField(source="expires_at")

    class Meta:
        model = PaymentTransaction
        fields = [
            "id",
            "subscriptionId",
            "provider",
            "status",
            "amount",
            "currency",
            "paymentUrl",
            "expiresAt",
        ]


class PaymentReturnSerializer(serializers.Serializer):
    paymentId = serializers.UUIDField()
    paymentStatus = serializers.CharField()
    subscriptionStatus = serializers.CharField()
    gatewaySuccessful = serializers.BooleanField()
    responseCode = serializers.CharField()


class VnPayIpnResponseSerializer(serializers.Serializer):
    RspCode = serializers.CharField()
    Message = serializers.CharField()
