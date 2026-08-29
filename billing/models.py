import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class SubscriptionPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    currency = models.CharField(max_length=3, default="VND")
    duration_days = models.PositiveIntegerField(default=30)
    daily_token_limit = models.PositiveIntegerField()
    max_documents = models.PositiveIntegerField(default=10)
    max_file_size_mb = models.PositiveIntegerField(default=10)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price", "name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class Subscription(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACTIVE = "ACTIVE", "Active"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    daily_token_limit = models.PositiveIntegerField(editable=False)
    max_documents = models.PositiveIntegerField(editable=False)
    max_file_size_mb = models.PositiveIntegerField(editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status__in=["PENDING", "ACTIVE"]),
                name="one_current_subscription_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.daily_token_limit = self.plan.daily_token_limit
            self.max_documents = self.plan.max_documents
            self.max_file_size_mb = self.plan.max_file_size_mb
        super().save(*args, **kwargs)

    def activate(self):
        start = self.starts_at or timezone.now()
        self.status = self.Status.ACTIVE
        self.starts_at = start
        self.expires_at = start + timedelta(days=self.plan.duration_days)
        self.cancelled_at = None
        self.save(
            update_fields=[
                "status",
                "starts_at",
                "expires_at",
                "cancelled_at",
                "updated_at",
            ]
        )

    @property
    def is_effectively_active(self):
        return (
            self.status == self.Status.ACTIVE
            and self.starts_at is not None
            and self.expires_at is not None
            and self.starts_at <= timezone.now() < self.expires_at
        )

    def __str__(self):
        return f"{self.user} - {self.plan.code} - {self.status}"


class PaymentTransaction(models.Model):
    class Provider(models.TextChoices):
        VNPAY = "VNPAY", "VNPay"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        EXPIRED = "EXPIRED", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        default=Provider.VNPAY,
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    txn_ref = models.CharField(max_length=100, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="VND")
    payment_url = models.TextField(blank=True)
    provider_transaction_no = models.CharField(max_length=50, blank=True)
    response_code = models.CharField(max_length=10, blank=True)
    bank_code = models.CharField(max_length=20, blank=True)
    card_type = models.CharField(max_length=20, blank=True)
    raw_callback = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["subscription", "status"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self):
        return f"{self.txn_ref} - {self.status}"
