from django.urls import path

from billing.views import (
    CancelSubscriptionView,
    CurrentSubscriptionView,
    SubscribeView,
    SubscriptionPlanListView,
    CreateVnPayPaymentView,
    VnPayIpnView,
    VnPayReturnView,
)


urlpatterns = [
    path("subscription-plans/", SubscriptionPlanListView.as_view(), name="plan-list"),
    path("subscriptions/subscribe/", SubscribeView.as_view(), name="subscribe"),
    path("subscriptions/me/", CurrentSubscriptionView.as_view(), name="subscription-me"),
    path(
        "subscriptions/me/cancel/",
        CancelSubscriptionView.as_view(),
        name="subscription-cancel",
    ),
    path(
        "payments/vnpay/create/",
        CreateVnPayPaymentView.as_view(),
        name="vnpay-create",
    ),
    path(
        "payments/vnpay/return/",
        VnPayReturnView.as_view(),
        name="vnpay-return",
    ),
    path(
        "payments/vnpay/ipn/",
        VnPayIpnView.as_view(),
        name="vnpay-ipn",
    ),
]
