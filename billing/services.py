from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from billing.exceptions import SubscriptionConflict
from billing.models import Subscription, SubscriptionPlan


CURRENT_STATUSES = [Subscription.Status.PENDING, Subscription.Status.ACTIVE]


def get_current_subscription(user):
    subscription = (
        Subscription.objects.select_related("plan")
        .filter(user=user, status__in=CURRENT_STATUSES)
        .first()
    )
    if (
        subscription
        and subscription.status == Subscription.Status.ACTIVE
        and subscription.expires_at
        and subscription.expires_at <= timezone.now()
    ):
        Subscription.objects.filter(pk=subscription.pk).update(
            status=Subscription.Status.EXPIRED,
            updated_at=timezone.now(),
        )
        return None
    return subscription


@transaction.atomic
def subscribe_user(*, user, plan: SubscriptionPlan):
    if not plan.is_active:
        raise ValidationError({"planId": "This subscription plan is not active."})

    current = (
        Subscription.objects.select_for_update()
        .select_related("plan")
        .filter(user=user, status__in=CURRENT_STATUSES)
        .first()
    )
    if current:
        if current.plan_id == plan.id:
            return current, False
        raise SubscriptionConflict(
            "Cancel the current subscription before choosing another plan."
        )

    try:
        with transaction.atomic():
            subscription = Subscription.objects.create(user=user, plan=plan)
    except IntegrityError as error:
        raise SubscriptionConflict(
            "Another subscription request is already being processed."
        ) from error
    if plan.price == 0:
        subscription.activate()
    return subscription, True


@transaction.atomic
def cancel_current_subscription(*, user):
    subscription = (
        Subscription.objects.select_for_update()
        .select_related("plan")
        .filter(user=user, status__in=CURRENT_STATUSES)
        .first()
    )
    if not subscription:
        raise NotFound("No current subscription was found.")

    subscription.status = Subscription.Status.CANCELLED
    subscription.cancelled_at = timezone.now()
    subscription.save(update_fields=["status", "cancelled_at", "updated_at"])
    return subscription
