from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.models import SubscriptionPlan
from billing.payment_gateways.vnpay import VnPayConfigurationError, VnPayError
from billing.payment_services import (
    create_vnpay_payment,
    inspect_vnpay_return,
    process_vnpay_ipn,
)
from billing.serializers import (
    CreatePaymentRequestSerializer,
    CurrentSubscriptionResponseSerializer,
    PaymentReturnSerializer,
    PaymentTransactionSerializer,
    SubscribeRequestSerializer,
    SubscribeResponseSerializer,
    SubscriptionPlanSerializer,
    SubscriptionSerializer,
    VnPayIpnResponseSerializer,
)
from billing.services import (
    cancel_current_subscription,
    get_current_subscription,
    subscribe_user,
)


class SubscriptionPlanListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = SubscriptionPlanSerializer

    def get_queryset(self):
        return SubscriptionPlan.objects.filter(is_active=True)


class SubscribeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=SubscribeRequestSerializer,
        responses={
            status.HTTP_200_OK: SubscribeResponseSerializer,
            status.HTTP_201_CREATED: SubscribeResponseSerializer,
        },
    )
    def post(self, request):
        request_serializer = SubscribeRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        subscription, created = subscribe_user(
            user=request.user,
            plan=request_serializer.validated_data["plan"],
        )
        response_data = {
            "subscription": SubscriptionSerializer(subscription).data,
            "paymentRequired": subscription.status == subscription.Status.PENDING,
        }
        return Response(
            response_data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class CurrentSubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=CurrentSubscriptionResponseSerializer)
    def get(self, request):
        subscription = get_current_subscription(request.user)
        return Response(
            {
                "subscription": (
                    SubscriptionSerializer(subscription).data
                    if subscription
                    else None
                )
            }
        )


class CancelSubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses=SubscriptionSerializer)
    def post(self, request):
        subscription = cancel_current_subscription(user=request.user)
        return Response(SubscriptionSerializer(subscription).data)


class CreateVnPayPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=CreatePaymentRequestSerializer,
        responses={
            status.HTTP_200_OK: PaymentTransactionSerializer,
            status.HTTP_201_CREATED: PaymentTransactionSerializer,
        },
    )
    def post(self, request):
        serializer = CreatePaymentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment, created = create_vnpay_payment(
            user=request.user,
            subscription_id=serializer.validated_data["subscriptionId"],
            client_ip=request.META.get("REMOTE_ADDR", "127.0.0.1"),
        )
        return Response(
            PaymentTransactionSerializer(payment).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class VnPayReturnView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses=PaymentReturnSerializer)
    def get(self, request):
        parameters = {key: request.query_params.get(key) for key in request.query_params}
        try:
            payment, callback = inspect_vnpay_return(parameters)
        except (VnPayError, VnPayConfigurationError) as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        # VNPay cannot deliver an IPN to localhost. In development, process the
        # same signed callback on the browser return so the full flow is testable.
        # Production continues to use the server-to-server IPN as the authority.
        if settings.VNPAY_PROCESS_RETURN:
            process_vnpay_ipn(parameters)
            payment.refresh_from_db()
            payment.subscription.refresh_from_db()

        return Response(
            {
                "paymentId": payment.id,
                "paymentStatus": payment.status,
                "subscriptionStatus": payment.subscription.status,
                "gatewaySuccessful": callback.successful,
                "responseCode": callback.response_code,
            }
        )


class VnPayIpnView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses=VnPayIpnResponseSerializer)
    def get(self, request):
        parameters = {key: request.query_params.get(key) for key in request.query_params}
        result = process_vnpay_ipn(parameters)
        return Response({"RspCode": result.rsp_code, "Message": result.message})
