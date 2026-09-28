"""
Redis-backed adapters.

The package separates four concerns that happen to use the same Redis server:

    RedisConnectionManager -> connection lifecycle, retry, health, and metrics
    RedisCache             -> key-value cache commands
    RedisPubSub            -> transient event publishing and subscriptions
    SignInAttemptLimiter   -> failed sign-in budgets per username

The application creates one connection manager and injects it into the three
capability adapters. This keeps their public APIs focused without creating
duplicate connection pools.

Suggested reading order (for learning this package from scratch)
----------------------------------------------------------------
Read the files in this order — each one builds on the one before it:

    1. ``channels.py``          (about 30 seconds)
       The radio analogy: what a channel is, and why the channel names
       live in one shared file instead of being typed out everywhere.

    2. ``redis_connection.py``  (the foundation)
       The "phone operator": who actually dials Redis, how the app keeps
       working while Redis is down, and where the tally book (stats)
       comes from. Every cache operation in this package goes through
       this file.

    3. ``redis_cache.py``       Key-value cache operations.
    4. ``redis_pubsub.py``      Event publishing and subscription recovery.
    5. ``sign_in_attempt_limiter.py``
                                Failed sign-in counting and lockout windows.

If any line in these files still reads like jargon, it is a bug in the
comments — not in you. Fix it right there.
"""

# Make the shared channel-name constant available at the package level too,
# so callers can write ``from app.adapters.cache import CONFIG_CHANGES_CHANNEL``.
from app.adapters.cache.channels import CONFIG_CHANGES_CHANNEL

# Export the four distinct Redis capabilities from one discoverable package.
from app.adapters.cache.redis_cache import RedisCache
from app.adapters.cache.redis_connection import RedisConnectionManager
from app.adapters.cache.redis_pubsub import RedisPubSub
from app.adapters.cache.sign_in_attempt_limiter import AttemptBudget, SignInAttemptLimiter

# Tell tools (and human readers) which names this package is meant to offer.
__all__ = [
    "CONFIG_CHANGES_CHANNEL",
    "AttemptBudget",
    "RedisCache",
    "RedisConnectionManager",
    "RedisPubSub",
    "SignInAttemptLimiter",
]
