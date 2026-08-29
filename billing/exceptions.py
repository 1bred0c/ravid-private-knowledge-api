from rest_framework.exceptions import APIException


class SubscriptionConflict(APIException):
    status_code = 409
    default_detail = "User already has a current subscription."
    default_code = "subscription_conflict"


class PaymentUnavailable(APIException):
    status_code = 503
    default_detail = "Payment gateway is not available."
    default_code = "payment_unavailable"
