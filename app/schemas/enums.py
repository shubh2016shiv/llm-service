"""
Schema enums — the fixed word lists
====================================

What this file is for
--------------------
Many fields in this service may only be ONE of a few fixed words: a
provider is either "rest_api" or "aws_sdk", a tenant is "active" or
"suspended", and so on. This file names each allowed word exactly once,
so no other module has to re-type a string that might drift or get
misspelled.

Why there are so many
---------------------
Each list belongs to a different table or concept, and each must stay
EXACTLY in step with a PostgreSQL CHECK constraint (see the note on each
class). One list per concept = one source of truth per concept.

Runtime settings versus catalog enums
--------------------------------------
``ProviderType`` and ``AuthMode`` belong to the runtime configuration layer
and are defined in ``app.core.settings.models.provider_config``. This module
only owns database/API vocabularies. ``ProviderCatalogType`` classifies a
catalog row, while ``ProviderCatalogAuthMode`` mirrors its database CHECK
constraint. Similar words do not make those concepts interchangeable.

Author: Shubham Singh
"""

from enum import StrEnum


class OperationType(StrEnum):
    """LLM operations the gateway can dispatch to a provider.

    Used in structured logging (`operation` field) and in capability checks
    (ProviderStaticConfig.capabilities).
    """

    CHAT = "chat"  # Chat completions (generate + stream_generate)
    EMBED = "embed"  # Text embeddings
    RERANK = "rerank"  # Document re-ranking
    HEALTH = "health"  # Provider health check


class ModelLifecycleStatus(StrEnum):
    """Lifecycle states for globally cataloged LLM models.

    Values must remain aligned with the ``model_catalog.status`` PostgreSQL
    CHECK constraint in ``postgres_schema/create_model_catalog.sql``.
    """

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class ProviderCatalogType(StrEnum):
    """Platform catalog classification for an LLM provider integration.

    Values must remain aligned with the ``provider_catalog.provider_type``
    PostgreSQL CHECK constraint in ``postgres_schema/create_provider_catalog.sql``.
    This differs from runtime ``ProviderType``, which describes transport strategy.
    """

    DIRECT_API = "direct_api"
    CLOUD_API = "cloud_api"
    SELF_HOSTED = "self_hosted"
    GATEWAY = "gateway"


class ProviderCatalogAuthMode(StrEnum):
    """Credential mode recorded for a provider catalog entry.

    Values must remain aligned with the ``provider_catalog.auth_mode``
    PostgreSQL CHECK constraint. This differs from runtime ``AuthMode`` because
    catalog entries also support an application-defined ``custom`` mode.
    """

    BEARER_TOKEN = "bearer_token"
    API_KEY_HEADER = "api_key_header"
    AWS_SIGV4 = "aws_sigv4"
    OAUTH = "oauth"
    CUSTOM = "custom"


class TenantDeploymentStatus(StrEnum):
    """Operational state of a tenant-scoped LLM deployment.

    Values must remain aligned with the ``tenant_deployments.status``
    PostgreSQL CHECK constraint in ``postgres_schema/create_tenant_deployments.sql``.
    """

    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"


class TenantMembershipStatus(StrEnum):
    """Lifecycle state of a user's membership within one tenant.

    Values must remain aligned with the ``tenant_memberships.status``
    PostgreSQL CHECK constraint in ``postgres_schema/create_tenant_memberships.sql``.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"


class TenantLifecycleStatus(StrEnum):
    """Lifecycle state of a tenant organization.

    Values must remain aligned with the ``tenants.status`` PostgreSQL CHECK
    constraint in ``postgres_schema/create_tenants.sql``.
    """

    ACTIVE = "active"
    TRIAL = "trial"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class TenantSubscriptionTier(StrEnum):
    """Commercial plan assigned to a tenant organization.

    Values must remain aligned with the ``tenants.tier`` PostgreSQL CHECK
    constraint in ``postgres_schema/create_tenants.sql``.
    """

    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class UserAccountStatus(StrEnum):
    """Lifecycle state of a platform user account.

    Values must remain aligned with the ``users.status`` PostgreSQL CHECK
    constraint in ``postgres_schema/create_users.sql``.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"
    DELETED = "deleted"


class UserEntitlementStatus(StrEnum):
    """Lifecycle state of a user-specific LLM entitlement.

    Values must remain aligned with the ``user_entitlements.status``
    PostgreSQL CHECK constraint in
    ``postgres_schema/create_user_entitlements.sql``.
    """

    ACTIVE = "active"
    INACTIVE = "inactive"
    REVOKED = "revoked"
