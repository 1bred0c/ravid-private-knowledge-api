from dataclasses import dataclass
from datetime import datetime, time, timedelta
from functools import lru_cache

import redis
from django.conf import settings
from django.utils import timezone
from redis.exceptions import RedisError


class TokenUsageUnavailable(RuntimeError):
    pass


class TokenQuotaExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenReservation:
    subscription_id: str
    tokens: int
    key: str


@lru_cache(maxsize=1)
def get_redis_client():
    return redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )


def daily_token_key(subscription_id):
    usage_date = timezone.localdate().isoformat()
    return f"ravid:usage:{subscription_id}:{usage_date}:tokens"


def get_daily_token_usage(subscription_id):
    try:
        value = get_redis_client().get(daily_token_key(subscription_id))
        return int(value or 0)
    except (RedisError, TypeError, ValueError):
        return None


def get_usage_summary(subscription):
    used = get_daily_token_usage(subscription.id)
    remaining = (
        max(subscription.daily_token_limit - used, 0)
        if used is not None
        else None
    )
    return {
        "tokensUsedToday": used,
        "tokensRemainingToday": remaining,
    }


def reserve_tokens(subscription, tokens):
    if tokens <= 0:
        raise ValueError("Reserved tokens must be positive.")
    key = daily_token_key(subscription.id)
    expires_in = _seconds_until_usage_expiry()
    script = """
    local current = tonumber(redis.call('GET', KEYS[1]) or '0')
    local requested = tonumber(ARGV[1])
    local limit = tonumber(ARGV[2])
    if current + requested > limit then
        return -1
    end
    local updated = redis.call('INCRBY', KEYS[1], requested)
    if redis.call('TTL', KEYS[1]) < 0 then
        redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
    end
    return updated
    """
    try:
        result = get_redis_client().eval(
            script,
            1,
            key,
            tokens,
            subscription.daily_token_limit,
            expires_in,
        )
    except RedisError as error:
        raise TokenUsageUnavailable("Redis token usage is unavailable.") from error
    if int(result) == -1:
        raise TokenQuotaExceeded("Daily token quota has been exceeded.")
    return TokenReservation(str(subscription.id), tokens, key)


def commit_token_usage(reservation, actual_tokens):
    if actual_tokens < 0 or actual_tokens > reservation.tokens:
        raise ValueError("Actual tokens must be between zero and reserved tokens.")
    _adjust_reserved_tokens(reservation, actual_tokens - reservation.tokens)


def release_token_reservation(reservation):
    _adjust_reserved_tokens(reservation, -reservation.tokens)


def _adjust_reserved_tokens(reservation, delta):
    script = """
    if redis.call('EXISTS', KEYS[1]) == 0 then
        return 0
    end
    local current = tonumber(redis.call('GET', KEYS[1]) or '0')
    local updated = current + tonumber(ARGV[1])
    if updated < 0 then updated = 0 end
    redis.call('SET', KEYS[1], updated, 'KEEPTTL')
    return updated
    """
    try:
        return int(get_redis_client().eval(script, 1, reservation.key, delta))
    except RedisError as error:
        raise TokenUsageUnavailable("Redis token usage is unavailable.") from error


def _seconds_until_usage_expiry():
    now = timezone.localtime()
    tomorrow = now.date() + timedelta(days=1)
    expiry = timezone.make_aware(datetime.combine(tomorrow, time.min)) + timedelta(hours=1)
    return max(int((expiry - now).total_seconds()), 60)
