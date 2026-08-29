from django.contrib import admin
from django.utils import timezone

from billing.models import PaymentTransaction, Subscription, SubscriptionPlan


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "price",
        "currency",
        "daily_token_limit",
        "is_active",
    )
    list_filter = ("is_active", "currency")
    search_fields = ("code", "name")
    ordering = ("price", "name")


@admin.action(description="Activate selected subscriptions")
def activate_subscriptions(modeladmin, request, queryset):
    for subscription in queryset.select_related("plan"):
        subscription.activate()


@admin.action(description="Cancel selected subscriptions")
def cancel_subscriptions(modeladmin, request, queryset):
    queryset.filter(
        status__in=[Subscription.Status.PENDING, Subscription.Status.ACTIVE]
    ).update(
        status=Subscription.Status.CANCELLED,
        cancelled_at=timezone.now(),
        updated_at=timezone.now(),
    )


@admin.action(description="Mark selected subscriptions as expired")
def expire_subscriptions(modeladmin, request, queryset):
    queryset.filter(status=Subscription.Status.ACTIVE).update(
        status=Subscription.Status.EXPIRED,
        updated_at=timezone.now(),
    )


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "plan",
        "status",
        "starts_at",
        "expires_at",
        "created_at",
    )
    list_filter = ("status", "plan")
    search_fields = ("user__username", "user__email", "plan__code")
    autocomplete_fields = ("user", "plan")
    readonly_fields = (
        "id",
        "daily_token_limit",
        "max_documents",
        "max_file_size_mb",
        "created_at",
        "updated_at",
    )
    actions = (activate_subscriptions, cancel_subscriptions, expire_subscriptions)


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "txn_ref",
        "subscription",
        "provider",
        "status",
        "amount",
        "currency",
        "created_at",
        "paid_at",
    )
    list_filter = ("provider", "status", "currency")
    search_fields = (
        "txn_ref",
        "provider_transaction_no",
        "subscription__user__username",
        "subscription__user__email",
    )
    readonly_fields = (
        "id",
        "subscription",
        "provider",
        "status",
        "txn_ref",
        "amount",
        "currency",
        "payment_url",
        "provider_transaction_no",
        "response_code",
        "bank_code",
        "card_type",
        "raw_callback",
        "expires_at",
        "paid_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False
