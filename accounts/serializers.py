from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from billing.serializers import SubscriptionSerializer


User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    firstName = serializers.CharField(source="first_name")
    lastName = serializers.CharField(source="last_name")
    subscription = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "firstName",
            "lastName",
            "subscription",
        ]
        read_only_fields = ["id"]

    @extend_schema_field(SubscriptionSerializer(allow_null=True))
    def get_subscription(self, user):
        from billing.services import get_current_subscription

        subscription = get_current_subscription(user)
        if not subscription:
            return None
        return SubscriptionSerializer(subscription, context=self.context).data


class RegisterSerializer(serializers.ModelSerializer):
    firstName = serializers.CharField(source="first_name", max_length=150)
    lastName = serializers.CharField(source="last_name", max_length=150)
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "firstName",
            "lastName",
            "password",
            "password_confirm",
        ]

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs.pop("password_confirm"):
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}
            )
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)
