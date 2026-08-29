from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import PaymentTransaction, Subscription, SubscriptionPlan
from billing.payment_gateways.vnpay import VnPayGateway


class SubscriptionFlowTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="subscriber",
            email="subscriber@example.com",
            password="strong-test-password",
        )
        self.free_plan, _ = SubscriptionPlan.objects.update_or_create(
            code="FREE",
            defaults={
                "name": "Free",
                "price": Decimal("0.00"),
                "duration_days": 30,
                "daily_token_limit": 5000,
                "max_documents": 3,
                "max_file_size_mb": 5,
                "is_active": True,
            },
        )
        self.paid_plan, _ = SubscriptionPlan.objects.update_or_create(
            code="PRO",
            defaults={
                "name": "Pro",
                "price": Decimal("99000.00"),
                "duration_days": 30,
                "daily_token_limit": 50000,
                "max_documents": 100,
                "max_file_size_mb": 20,
                "is_active": True,
            },
        )
        self.client.force_authenticate(self.user)

    def test_plan_list_only_returns_active_plans(self):
        SubscriptionPlan.objects.create(
            code="HIDDEN",
            name="Hidden",
            daily_token_limit=1,
            is_active=False,
        )

        response = self.client.get(reverse("plan-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual({item["code"] for item in response.data}, {"FREE", "PRO"})

    def test_free_plan_is_activated_immediately(self):
        response = self.client.post(
            reverse("subscribe"),
            {"planId": str(self.free_plan.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["subscription"]["status"], "ACTIVE")
        self.assertFalse(response.data["paymentRequired"])
        subscription = Subscription.objects.get(user=self.user)
        self.assertEqual(subscription.daily_token_limit, 5000)
        self.assertIsNotNone(subscription.expires_at)

    def test_paid_plan_remains_pending_for_payment(self):
        response = self.client.post(
            reverse("subscribe"),
            {"planId": str(self.paid_plan.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["subscription"]["status"], "PENDING")
        self.assertTrue(response.data["paymentRequired"])

    def test_user_cannot_choose_another_plan_while_current_exists(self):
        self.client.post(
            reverse("subscribe"),
            {"planId": str(self.free_plan.id)},
            format="json",
        )

        response = self.client.post(
            reverse("subscribe"),
            {"planId": str(self.paid_plan.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_subscription_is_included_in_me_response(self):
        self.client.post(
            reverse("subscribe"),
            {"planId": str(self.free_plan.id)},
            format="json",
        )

        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["subscription"]["status"], "ACTIVE")
        self.assertEqual(response.data["subscription"]["plan"]["code"], "FREE")

    def test_user_can_cancel_current_subscription(self):
        self.client.post(
            reverse("subscribe"),
            {"planId": str(self.free_plan.id)},
            format="json",
        )

        response = self.client.post(reverse("subscription-cancel"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "CANCELLED")


@override_settings(
    VNPAY_TMN_CODE="ABCD1234",
    VNPAY_HASH_SECRET="test-vnpay-secret",
    VNPAY_URL="https://sandbox.vnpayment.vn/paymentv2/vpcpay.html",
    VNPAY_RETURN_URL="http://127.0.0.1:8000/api/payments/vnpay/return/",
    VNPAY_PROCESS_RETURN=True,
)
class VnPayPaymentFlowTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="payer",
            email="payer@example.com",
            password="strong-test-password",
        )
        self.plan, _ = SubscriptionPlan.objects.update_or_create(
            code="PAYMENT_TEST",
            defaults={
                "name": "Payment Test",
                "price": Decimal("99000.00"),
                "duration_days": 30,
                "daily_token_limit": 50000,
                "max_documents": 100,
                "max_file_size_mb": 20,
                "is_active": True,
            },
        )
        self.subscription = Subscription.objects.create(
            user=self.user,
            plan=self.plan,
        )
        self.client.force_authenticate(self.user)

    def test_create_payment_returns_signed_vnpay_url(self):
        response = self.client.post(
            reverse("vnpay-create"),
            {"subscriptionId": str(self.subscription.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("vnp_SecureHash=", response.data["paymentUrl"])
        self.assertIn("vnp_TxnRef=", response.data["paymentUrl"])
        self.assertEqual(response.data["status"], "PENDING")

    def test_successful_ipn_activates_subscription_idempotently(self):
        create_response = self.client.post(
            reverse("vnpay-create"),
            {"subscriptionId": str(self.subscription.id)},
            format="json",
        )
        payment = PaymentTransaction.objects.get(id=create_response.data["id"])
        parameters = {
            "vnp_TmnCode": "ABCD1234",
            "vnp_Amount": str(int(payment.amount * 100)),
            "vnp_TxnRef": payment.txn_ref,
            "vnp_ResponseCode": "00",
            "vnp_TransactionStatus": "00",
            "vnp_TransactionNo": "123456789",
            "vnp_BankCode": "NCB",
            "vnp_CardType": "ATM",
        }
        parameters["vnp_SecureHash"] = VnPayGateway().sign_parameters(parameters)
        self.client.force_authenticate(user=None)

        first_response = self.client.get(reverse("vnpay-ipn"), parameters)
        second_response = self.client.get(reverse("vnpay-ipn"), parameters)

        self.assertEqual(first_response.data["RspCode"], "00")
        self.assertEqual(second_response.data["RspCode"], "02")
        payment.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.SUCCESS)
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)

    def test_successful_return_activates_subscription_in_development(self):
        create_response = self.client.post(
            reverse("vnpay-create"),
            {"subscriptionId": str(self.subscription.id)},
            format="json",
        )
        payment = PaymentTransaction.objects.get(id=create_response.data["id"])
        parameters = {
            "vnp_TmnCode": "ABCD1234",
            "vnp_Amount": str(int(payment.amount * 100)),
            "vnp_TxnRef": payment.txn_ref,
            "vnp_ResponseCode": "00",
            "vnp_TransactionStatus": "00",
            "vnp_TransactionNo": "987654321",
        }
        parameters["vnp_SecureHash"] = VnPayGateway().sign_parameters(parameters)
        self.client.force_authenticate(user=None)

        response = self.client.get(reverse("vnpay-return"), parameters)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["paymentStatus"], "SUCCESS")
        self.assertEqual(response.data["subscriptionStatus"], "ACTIVE")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)
