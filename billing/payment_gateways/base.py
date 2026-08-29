from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class PaymentRequest:
    payment_id: str
    subscription_id: str
    amount: Decimal
    expires_at: datetime
    client_ip: str


@dataclass(frozen=True)
class PaymentResponse:
    txn_ref: str
    payment_url: str
    expires_at: datetime


@dataclass(frozen=True)
class PaymentCallback:
    txn_ref: str
    provider_transaction_no: str
    amount: Decimal
    successful: bool
    response_code: str


class PaymentGateway(ABC):
    @abstractmethod
    def ensure_configured(self):
        raise NotImplementedError

    @abstractmethod
    def create_payment(self, request: PaymentRequest) -> PaymentResponse:
        raise NotImplementedError

    @abstractmethod
    def parse_callback(self, parameters: dict[str, str]) -> PaymentCallback:
        raise NotImplementedError
