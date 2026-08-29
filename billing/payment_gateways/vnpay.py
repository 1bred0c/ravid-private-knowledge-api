import hashlib
import hmac
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

from django.conf import settings

from billing.payment_gateways.base import (
    PaymentCallback,
    PaymentGateway,
    PaymentRequest,
    PaymentResponse,
)


class VnPayError(ValueError):
    pass


class VnPayConfigurationError(VnPayError):
    pass


class VnPayGateway(PaymentGateway):
    timezone = ZoneInfo("Asia/Ho_Chi_Minh")
    date_format = "%Y%m%d%H%M%S"

    def ensure_configured(self):
        required = {
            "VNPAY_TMN_CODE": settings.VNPAY_TMN_CODE,
            "VNPAY_HASH_SECRET": settings.VNPAY_HASH_SECRET,
            "VNPAY_URL": settings.VNPAY_URL,
            "VNPAY_RETURN_URL": settings.VNPAY_RETURN_URL,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise VnPayConfigurationError(
                f"VNPay is not configured: {', '.join(missing)}"
            )
        if not settings.VNPAY_TMN_CODE.isalnum() or len(settings.VNPAY_TMN_CODE) != 8:
            raise VnPayConfigurationError(
                "VNPAY_TMN_CODE must contain exactly 8 alphanumeric characters."
            )
        for name, value in {
            "VNPAY_URL": settings.VNPAY_URL,
            "VNPAY_RETURN_URL": settings.VNPAY_RETURN_URL,
        }.items():
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise VnPayConfigurationError(f"{name} must be a valid HTTP URL.")

    def create_payment(self, request: PaymentRequest) -> PaymentResponse:
        self.ensure_configured()
        if request.amount <= 0 or request.amount != request.amount.quantize(Decimal("1")):
            raise VnPayError("VNPay amount must be a positive whole VND amount.")

        txn_ref = request.payment_id.replace("-", "")
        parameters = {
            "vnp_Version": settings.VNPAY_VERSION,
            "vnp_Command": settings.VNPAY_COMMAND,
            "vnp_TmnCode": settings.VNPAY_TMN_CODE,
            "vnp_Amount": str(int(request.amount * 100)),
            "vnp_CurrCode": "VND",
            "vnp_TxnRef": txn_ref,
            "vnp_OrderInfo": f"Thanh toan subscription {request.subscription_id}",
            "vnp_OrderType": settings.VNPAY_ORDER_TYPE,
            "vnp_Locale": settings.VNPAY_LOCALE,
            "vnp_ReturnUrl": settings.VNPAY_RETURN_URL,
            "vnp_IpAddr": self._normalize_client_ip(request.client_ip),
            "vnp_CreateDate": self._format_datetime(request.expires_at - settings.VNPAY_PAYMENT_TTL),
            "vnp_ExpireDate": self._format_datetime(request.expires_at),
        }
        canonical_data = self._canonical_data(parameters)
        secure_hash = self._sign(canonical_data)
        return PaymentResponse(
            txn_ref=txn_ref,
            payment_url=f"{settings.VNPAY_URL}?{canonical_data}&vnp_SecureHash={secure_hash}",
            expires_at=request.expires_at,
        )

    def verify_callback(self, parameters: dict[str, str]) -> bool:
        received_hash = parameters.get("vnp_SecureHash", "")
        if not received_hash:
            return False
        signed_parameters = {
            key: str(value)
            for key, value in parameters.items()
            if key.startswith("vnp_")
            and key not in {"vnp_SecureHash", "vnp_SecureHashType"}
            and value not in {None, ""}
        }
        expected_hash = self._sign(self._canonical_data(signed_parameters))
        return hmac.compare_digest(expected_hash.lower(), received_hash.lower())

    def is_current_payment_url(self, payment_url: str) -> bool:
        """Return whether a cached URL was signed with the current configuration."""
        try:
            parsed = urlparse(payment_url)
            configured = urlparse(settings.VNPAY_URL)
            parameters = dict(parse_qsl(parsed.query, keep_blank_values=True))
            return (
                (parsed.scheme, parsed.netloc, parsed.path)
                == (configured.scheme, configured.netloc, configured.path)
                and parameters.get("vnp_TmnCode") == settings.VNPAY_TMN_CODE
                and parameters.get("vnp_ReturnUrl") == settings.VNPAY_RETURN_URL
                and self.verify_callback(parameters)
            )
        except (TypeError, ValueError):
            return False

    def parse_callback(self, parameters: dict[str, str]) -> PaymentCallback:
        self.ensure_configured()
        if not self.verify_callback(parameters):
            raise VnPayError("Invalid VNPay callback signature.")
        if parameters.get("vnp_TmnCode") != settings.VNPAY_TMN_CODE:
            raise VnPayError("VNPay callback terminal code does not match.")
        try:
            amount = Decimal(self._required(parameters, "vnp_Amount")) / 100
        except (InvalidOperation, ValueError) as error:
            raise VnPayError("Invalid VNPay callback amount.") from error
        return PaymentCallback(
            txn_ref=self._required(parameters, "vnp_TxnRef"),
            provider_transaction_no=parameters.get("vnp_TransactionNo", ""),
            amount=amount,
            successful=(
                parameters.get("vnp_ResponseCode") == "00"
                and parameters.get("vnp_TransactionStatus") == "00"
            ),
            response_code=parameters.get("vnp_ResponseCode", ""),
        )

    def sign_parameters(self, parameters: dict[str, str]) -> str:
        return self._sign(self._canonical_data(parameters))

    def _canonical_data(self, parameters: dict[str, str]) -> str:
        values = [
            (key, str(value))
            for key, value in sorted(parameters.items())
            if value not in {None, ""}
        ]
        return urlencode(values)

    def _sign(self, value: str) -> str:
        return hmac.new(
            settings.VNPAY_HASH_SECRET.encode("utf-8"),
            value.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

    def _format_datetime(self, value):
        return value.astimezone(self.timezone).strftime(self.date_format)

    def _normalize_client_ip(self, value):
        if not value or value in {"::1", "0:0:0:0:0:0:0:1"}:
            return "127.0.0.1"
        return value.strip()

    def _required(self, parameters, name):
        value = parameters.get(name)
        if value in {None, ""}:
            raise VnPayError(f"Missing VNPay callback field: {name}")
        return str(value)
