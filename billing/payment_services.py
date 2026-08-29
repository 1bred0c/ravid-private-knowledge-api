from dataclasses import dataclass
from decimal import Decimal
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from billing.exceptions import PaymentUnavailable
from billing.models import PaymentTransaction, Subscription
from billing.payment_gateways.base import PaymentRequest
from billing.payment_gateways.vnpay import (
    VnPayConfigurationError,
    VnPayError,
    VnPayGateway,
)


@dataclass(frozen=True)
class IpnResult:
    rsp_code: str
    message: str


def create_vnpay_payment(*, user, subscription_id, client_ip):
    gateway = VnPayGateway()
    try:
        gateway.ensure_configured()
    except VnPayConfigurationError as error:
        raise PaymentUnavailable(str(error)) from error

    with transaction.atomic():
        subscription = (
            Subscription.objects.select_for_update()
            .select_related("plan")
            .filter(id=subscription_id, user=user)
            .first()
        )
        if not subscription:
            raise NotFound("Subscription was not found.")
        if subscription.status != Subscription.Status.PENDING:
            raise ValidationError(
                {"subscriptionId": "Only pending subscriptions require payment."}
            )
        if subscription.plan.price <= 0:
            raise ValidationError(
                {"subscriptionId": "This subscription plan does not require payment."}
            )

        now = timezone.now()
        existing = (
            subscription.payments.filter(
                status=PaymentTransaction.Status.PENDING,
                expires_at__gt=now,
            )
            .order_by("-created_at")
            .first()
        )
        if (
            existing
            and existing.payment_url
            and gateway.is_current_payment_url(existing.payment_url)
        ):
            return existing, False

        if existing:
            existing.status = PaymentTransaction.Status.EXPIRED
            existing.save(update_fields=["status", "updated_at"])

        subscription.payments.filter(
            status=PaymentTransaction.Status.PENDING,
            expires_at__lte=now,
        ).update(status=PaymentTransaction.Status.EXPIRED, updated_at=now)

        expires_at = now + settings.VNPAY_PAYMENT_TTL
        payment_id = uuid.uuid4()
        payment = PaymentTransaction.objects.create(
            id=payment_id,
            subscription=subscription,
            txn_ref=payment_id.hex,
            amount=subscription.plan.price,
            currency=subscription.plan.currency,
            expires_at=expires_at,
        )
        response = gateway.create_payment(
            PaymentRequest(
                payment_id=str(payment.id),
                subscription_id=str(subscription.id),
                amount=payment.amount,
                expires_at=expires_at,
                client_ip=client_ip,
            )
        )
        payment.payment_url = response.payment_url
        payment.save(update_fields=["payment_url", "updated_at"])
        return payment, True


def inspect_vnpay_return(parameters):
    gateway = VnPayGateway()
    callback = gateway.parse_callback(parameters)
    payment = PaymentTransaction.objects.filter(txn_ref=callback.txn_ref).first()
    if not payment:
        raise NotFound("Payment transaction was not found.")
    if payment.amount != callback.amount:
        raise ValidationError("VNPay callback amount does not match the payment.")
    return payment, callback


def process_vnpay_ipn(parameters):
    gateway = VnPayGateway()
    try:
        callback = gateway.parse_callback(parameters)
    except (VnPayError, VnPayConfigurationError):
        return IpnResult("97", "Invalid checksum")

    with transaction.atomic():
        payment = (
            PaymentTransaction.objects.select_for_update()
            .select_related("subscription__plan")
            .filter(txn_ref=callback.txn_ref)
            .first()
        )
        if not payment:
            return IpnResult("01", "Order not found")
        if payment.amount != callback.amount:
            return IpnResult("04", "Invalid amount")
        if payment.status != PaymentTransaction.Status.PENDING:
            return IpnResult("02", "Order already confirmed")

        payment.provider_transaction_no = callback.provider_transaction_no
        payment.response_code = callback.response_code
        payment.bank_code = str(parameters.get("vnp_BankCode", ""))
        payment.card_type = str(parameters.get("vnp_CardType", ""))
        payment.raw_callback = dict(parameters)

        if callback.successful:
            payment.status = PaymentTransaction.Status.SUCCESS
            payment.paid_at = timezone.now()
            payment.save()
            if payment.subscription.status == Subscription.Status.PENDING:
                payment.subscription.activate()
        else:
            payment.status = PaymentTransaction.Status.FAILED
            payment.save()

        return IpnResult("00", "Confirm success")
