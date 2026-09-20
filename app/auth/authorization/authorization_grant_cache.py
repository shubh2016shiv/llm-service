"""
Authorization grant cache — the rememberer with four change counters
=====================================================================

What this file is for
---------------------
Authorizing one inference request takes four database checks (tenant,
membership, deployment, entitlement). Re-running all four on EVERY
request is slow. So this class remembers the last "yes": it saves the
answer under a key, and the next identical request gets the saved answer
instantly — no database work.

The hard part — keeping a remembered "yes" honest
-------------------------------------------------
A saved "yes" is only true as long as everything it depended on is still
true. If an admin suspends the tenant, or removes the membership, or
deactivates the deployment, every saved "yes" that relied on that fact
must stop working IMMEDIATELY.

The trick, in plain words:

    1. Four change counters. The cache keeps four values, one per thing
       a "yes" can depend on:
           tenant counter     -- gets a new value when the tenant changes,
           membership counter -- gets a new value when THIS caller's
                                membership in THIS tenant changes,
           deployment counter -- gets a new value when THIS deployment
                                changes,
           route counter      -- gets a new value when THIS exact route
                                changes.

    2. Every saved "yes" records the four counter values it saw when it
       was made.

    3. Reading a saved "yes" happens in ONE go: the answer and the four
       current counter values are read at the same moment, so no change
       can slip in between the reads.

    4. The saved "yes" is trusted ONLY if its four recorded values match
       the four current values. If any counter has changed since the
       "yes" was saved, the answer is out of date — instantly, no matter
       which counter changed.

    5. Saving a fresh "yes" re-checks the counters AFTER the slow
       database work, so an answer is never saved on top of state that
       changed mid-check.

    6. Invalidating = giving one counter a brand-new value (or deleting
       the exact-route answer). Management services call the
       invalidate_* methods after every change.

In short: the saved "yes" remembers the counter values it saw, and it is
honored only while every one of those values still matches the current
value in the cache.

Who uses this file
------------------
    InferenceAuthorizationService -> AuthorizationGrantCache -> RedisCache
    Management services ----------> AuthorizationGrantCache -> RedisCache

PostgreSQL remains the source of truth: this module only remembers, and
it forgets the moment the counters say so.

Author: Shubham Singh
"""

# logging = writing to the application log.
import logging

# Protocol = describe "anything with these three methods", so this class
# can be tested with a fake backend and the real RedisCache also fits.
from typing import Protocol

# UUID = globally unique ids; uuid4 = mint a brand-new random one (used
# for fresh counter values).
from uuid import UUID, uuid4

# The typed error raised when invalidation cannot be guaranteed.
from app.core.exceptions import AuthorizationGrantCacheUnavailableError

# The shapes this file reads and writes:
#   AuthorizationGrantLookup  = one cache read's result (answer + counters),
#   AuthorizationGrantVersions= the four counter values,
#   CachedAuthorizationGrant  = a saved answer WITH its recorded counters,
#   InferenceAccessContext    = the "yes" payload itself.
from app.schemas.auth_schema import (
    AuthorizationGrantLookup,
    AuthorizationGrantVersions,
    CachedAuthorizationGrant,
    InferenceAccessContext,
)

logger = logging.getLogger(__name__)

# The counter values that mean "nothing has changed yet". A MISSING
# counter counts as this default, so a fresh cache behaves as if nothing
# ever changed — which is exactly right on day one.
_DEFAULT_GRANT_VERSIONS = AuthorizationGrantVersions()


class AuthorizationGrantCacheBackend(Protocol):
    """The three cache operations this class needs from any backend.

    Described as a Protocol (not a concrete class) so the real RedisCache
    and the test fakes both satisfy it without inheritance.

    The three operations, in plain words:
        1. get_many = read several notes in ONE go (the whole point of
           the no-change-in-between guarantee).
        2. set      = write one note, with a self-destruct timer (TTL).
        3. delete   = throw one note away.
    """

    async def get_many(self, keys: tuple[str, ...]) -> list[bytes | None]:
        """Return values for all keys from one backend snapshot."""
        ...

    async def set(self, key: str, value: bytes, ttl_seconds: int | None) -> bool:
        """Store bytes and report whether the backend accepted the write."""
        ...

    async def delete(self, key: str) -> bool:
        """Delete one key and report whether the backend accepted the operation."""
        ...


def _build_grant_keys(
    tenant_id: UUID,
    user_id: UUID,
    deployment_key: str,
) -> tuple[str, str, str, str, str]:
    """Build the answer key, followed by its four counter keys, in order.

    The order is a contract: position 0 is always the answer, positions
    1-4 are always (tenant, membership, deployment, route). Readers
    unpack by position, so this one function is the single source of
    truth for the layout.
    """
    return (
        # The saved "yes" itself.
        f"inference_authz:{tenant_id}:{user_id}:{deployment_key}",
        # Counter 1: gets a new value whenever the tenant changes.
        f"inference_authz_version:tenant:{tenant_id}",
        # Counter 2: gets a new value whenever THIS user's membership in
        # THIS tenant changes.
        f"inference_authz_version:membership:{tenant_id}:{user_id}",
        # Counter 3: gets a new value whenever THIS deployment changes.
        f"inference_authz_version:deployment:{tenant_id}:{deployment_key}",
        # Counter 4: gets a new value whenever THIS exact route changes.
        f"inference_authz_version:route:{tenant_id}:{user_id}:{deployment_key}",
    )


def _decode_cached_grant(raw_grant: bytes) -> CachedAuthorizationGrant | None:
    """Turn saved bytes back into a grant, or None when they are damaged.

    A damaged payload (old format after a deploy, or corruption) is not a
    crash — it simply counts as "no usable answer", and the caller falls
    back to the database.
    """
    try:
        return CachedAuthorizationGrant.model_validate_json(raw_grant)
    except ValueError:
        # The bytes are not a valid grant anymore. Log it and treat the
        # answer as missing (the database will re-decide).
        logger.warning("Invalid inference authorization grant payload")
        return None


class AuthorizationGrantCache:
    """Remember successful "yes" answers, guarded by the four counters.

    The full mechanism is explained in the module docstring above. In one
    sentence: an answer is trusted only while its four recorded counter
    values still match the current values read in the same one-go read.
    """

    def __init__(
        self,
        backend: AuthorizationGrantCacheBackend | None,
        ttl_seconds: int,
    ) -> None:
        """Keep the backend and the answer/counter self-destruct timer.

        Args:
            backend: The cache to use (RedisCache in production, a fake
                in tests), or None to disable remembering entirely.
            ttl_seconds: How long answers and counters live before the
                cache throws them away on its own (a safety net behind
                the event-driven invalidation).
        """
        self._backend = backend
        self._ttl_seconds = ttl_seconds

    async def find_grant(
        self,
        tenant_id: UUID,
        user_id: UUID,
        deployment_key: str,
    ) -> AuthorizationGrantLookup:
        """Look up one remembered answer, or report "nothing usable".

        Returns a lookup carrying BOTH:
          - the saved answer, when it is still honest, and
          - the counter values observed during this read — so the caller
            can later store a fresh answer only if those values are still
            current.

        Args:
            tenant_id: The tenant scope of the request.
            user_id: The caller.
            deployment_key: The deployment route.

        Returns:
            An AuthorizationGrantLookup (context=None means "no usable
            answer — re-check the database").
        """
        # No cache configured? Nothing can be remembered — answer "miss"
        # and report no observed counters either.
        if self._backend is None:
            return AuthorizationGrantLookup(context=None, observed_versions=None)
        # Read the answer and its four counters in ONE go: the single
        # read is what stops a change from sneaking in between reads and
        # making an old answer look fresh.
        cache_keys = _build_grant_keys(tenant_id, user_id, deployment_key)
        cached_values = await self._backend.get_many(cache_keys)
        # Decode the four counter values (positions 1-4).
        observed_versions = await self._read_versions(cache_keys[1:], cached_values[1:])
        # The answer itself is position 0.
        raw_grant = cached_values[0]
        if raw_grant is None or observed_versions is None:
            # No answer, or a damaged counter (already repaired) — miss.
            return AuthorizationGrantLookup(context=None, observed_versions=observed_versions)
        # Turn the answer bytes back into a grant.
        cached_grant = _decode_cached_grant(raw_grant)
        if cached_grant is None or cached_grant.versions != observed_versions:
            # Damaged, OR honest-but-out-of-date (its recorded counter
            # values no longer match the current ones). Best-effort throw
            # the unusable answer away so the next read starts clean.
            await self._delete_stale_grant(cache_keys[0])
            return AuthorizationGrantLookup(context=None, observed_versions=observed_versions)
        # Still valid: hand over the saved answer, plus the counter
        # values it was checked against.
        return AuthorizationGrantLookup(
            context=cached_grant.context,
            observed_versions=observed_versions,
        )

    async def store_grant_if_unchanged(
        self,
        context: InferenceAccessContext,
        observed_versions: AuthorizationGrantVersions,
    ) -> bool:
        """Save a fresh "yes" — but ONLY if nothing changed mid-check.

        The caller did slow database work between its find_grant and now.
        This method re-reads the counters and compares them to the values
        the caller observed: if ANY counter changed, the answer is
        already out of date and must not be saved.

        Args:
            context: The "yes" payload to remember.
            observed_versions: The counter values the caller saw BEFORE
                its database checks.

        Returns:
            True when the answer was saved, False when the world changed
            or the backend refused the write.
        """
        if self._backend is None:
            return False
        # Same key layout as find_grant.
        cache_keys = _build_grant_keys(context.tenant_id, context.user_id, context.deployment_key)
        # Re-read the four counters NOW, after the database work.
        current_values = await self._backend.get_many(cache_keys[1:])
        current_versions = await self._read_versions(cache_keys[1:], current_values)
        if current_versions is None or current_versions != observed_versions:
            # Something changed (or a counter was damaged): this answer
            # is already out of date — do not remember it.
            return False
        # Record the counter values this answer was decided against...
        cached_grant = CachedAuthorizationGrant(context=context, versions=observed_versions)
        serialized_grant = cached_grant.model_dump_json().encode("utf-8")
        # ...and store it, reporting whether the cache accepted the write.
        return await self._backend.set(cache_keys[0], serialized_grant, self._ttl_seconds)

    async def invalidate_tenant(self, tenant_id: UUID) -> None:
        """Forget every answer that depended on one tenant.

        Called by management after a tenant change (rename, plan change,
        suspension...). Giving the tenant counter a new value instantly
        makes every saved answer that remembered the old value out of
        date.
        """
        await self._advance_version(f"inference_authz_version:tenant:{tenant_id}", "tenant")

    async def invalidate_membership(self, tenant_id: UUID, user_id: UUID) -> None:
        """Forget every answer that depended on one membership.

        Called when this user's membership in this tenant changes (role
        change, removal...).
        """
        key = f"inference_authz_version:membership:{tenant_id}:{user_id}"
        await self._advance_version(key, "membership")

    async def invalidate_deployment(self, tenant_id: UUID, deployment_key: str) -> None:
        """Forget every answer that depended on one deployment.

        Called when a deployment's settings or status change.
        """
        key = f"inference_authz_version:deployment:{tenant_id}:{deployment_key}"
        await self._advance_version(key, "deployment")

    async def invalidate_route(
        self,
        tenant_id: UUID,
        user_id: UUID,
        deployment_key: str,
    ) -> None:
        """Forget ONE exact tenant-user-deployment answer, right now.

        Two steps, both required:
          1. Give the route counter a new value, so the answer is out of
             date even if the delete below cannot happen.
          2. Delete the answer itself — and if the cache refuses, RAISE,
             because the caller (management) must know the invalidation
             could not be fully guaranteed.
        """
        cache_keys = _build_grant_keys(tenant_id, user_id, deployment_key)
        await self._advance_version(cache_keys[4], "route")
        if self._backend is not None and not await self._backend.delete(cache_keys[0]):
            raise AuthorizationGrantCacheUnavailableError("delete route grant")

    async def _read_versions(
        self,
        version_keys: tuple[str, ...],
        raw_versions: list[bytes | None],
    ) -> AuthorizationGrantVersions | None:
        """Decode the four counter values into one versions object.

        Ordering note (why this is written the long way):
            version_keys/raw_versions always arrive as
            (tenant, membership, deployment, route) from _build_grant_keys.
            They are unpacked BY POSITION into named locals, then paired
            explicitly with their field name — instead of zipping against
            the model's declared field order. That way, if someone ever
            reorders AuthorizationGrantVersions' fields, this code raises
            a loud error instead of silently swapping two scopes' values.

        A MISSING counter decodes to its default value ("nothing changed
        yet"). A DAMAGED counter is repaired in place with a fresh random
        value and the whole read reports None (treat as a miss this once).
        """
        if self._backend is None:
            return None
        # Unpack by position into named locals — once, loudly.
        tenant_key, membership_key, deployment_key, route_key = version_keys
        tenant_raw, membership_raw, deployment_raw, route_raw = raw_versions
        # Pair each named value with its explicit field name.
        named_versions = (
            ("tenant_version", tenant_key, tenant_raw),
            ("membership_version", membership_key, membership_raw),
            ("deployment_version", deployment_key, deployment_raw),
            ("route_version", route_key, route_raw),
        )
        decoded_versions: dict[str, str] = {}
        for field_name, version_key, raw_version in named_versions:
            if raw_version is None:
                # Counter missing = nothing changed yet -> use the default.
                decoded_versions[field_name] = str(getattr(_DEFAULT_GRANT_VERSIONS, field_name))
                continue
            try:
                # Counter values are stored as plain text.
                decoded_versions[field_name] = raw_version.decode("utf-8")
            except UnicodeDecodeError:
                # A counter that is not valid text cannot be compared, so
                # it cannot be trusted. Replace it with a fresh random
                # value (which makes every saved answer that remembered
                # the old value out of date) and report "miss" for this
                # read.
                logger.warning("Invalid authorization grant version", extra={"field": field_name})
                was_repaired = await self._backend.set(
                    version_key,
                    f"v:{uuid4()}".encode(),
                    ttl_seconds=self._ttl_seconds,
                )
                if not was_repaired:
                    logger.warning("Could not repair corrupt authorization grant version")
                return None
        # Hand the four decoded values to the model, which validates them.
        return AuthorizationGrantVersions.model_validate(decoded_versions)

    async def _delete_stale_grant(self, grant_key: str) -> None:
        """Best-effort throw away an unusable answer.

        Failure is only logged: the answer is already ignored (it failed
        the counter check), so the delete is just housekeeping — the
        database remains the fallback either way.
        """
        if self._backend is not None and not await self._backend.delete(grant_key):
            logger.warning("Could not delete unusable inference authorization grant")

    async def _advance_version(self, version_key: str, scope_name: str) -> None:
        """Give one counter a brand-new random value, or raise.

        Writing a fresh value over the old one instantly makes every
        saved answer that remembered the old value out of date — that is
        the entire invalidation mechanism.

        Args:
            version_key: Which counter to change.
            scope_name: A human label for the error message ("tenant",
                "membership", ...).

        Raises:
            AuthorizationGrantCacheUnavailableError: When the write is
                refused — invalidation cannot be guaranteed, and the
                caller must know.
        """
        if self._backend is None:
            return
        version_value = f"v:{uuid4()}".encode()
        was_written = await self._backend.set(
            version_key,
            version_value,
            ttl_seconds=self._ttl_seconds,
        )
        if not was_written:
            raise AuthorizationGrantCacheUnavailableError(f"invalidate {scope_name}")
