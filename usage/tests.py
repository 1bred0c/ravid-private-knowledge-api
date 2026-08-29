import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from usage.services import (
    TokenQuotaExceeded,
    commit_token_usage,
    get_daily_token_usage,
    release_token_reservation,
    reserve_tokens,
)


class FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def eval(self, script, number_of_keys, key, *arguments):
        current = int(self.values.get(key, 0))
        if "current + requested > limit" in script:
            requested = int(arguments[0])
            limit = int(arguments[1])
            if current + requested > limit:
                return -1
            self.values[key] = current + requested
        else:
            self.values[key] = max(current + int(arguments[0]), 0)
        return self.values[key]


class TokenUsageTests(SimpleTestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.subscription = SimpleNamespace(
            id=uuid.uuid4(),
            daily_token_limit=1000,
        )

    def test_reserve_and_commit_actual_usage(self):
        with patch("usage.services.get_redis_client", return_value=self.redis):
            reservation = reserve_tokens(self.subscription, 500)
            commit_token_usage(reservation, 320)

            self.assertEqual(get_daily_token_usage(self.subscription.id), 320)

    def test_release_reservation_after_failure(self):
        with patch("usage.services.get_redis_client", return_value=self.redis):
            reservation = reserve_tokens(self.subscription, 500)
            release_token_reservation(reservation)

            self.assertEqual(get_daily_token_usage(self.subscription.id), 0)

    def test_reservation_rejects_usage_over_daily_limit(self):
        with patch("usage.services.get_redis_client", return_value=self.redis):
            reserve_tokens(self.subscription, 800)

            with self.assertRaises(TokenQuotaExceeded):
                reserve_tokens(self.subscription, 300)
