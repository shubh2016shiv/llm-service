"""
User entitlement SQL query constants.

Table: user_entitlements
  entitlement_id          UUID PRIMARY KEY
  tenant_id               UUID NOT NULL → tenants.tenant_id
  user_id                 UUID NOT NULL → users.user_id
  deployment_key          TEXT NOT NULL → (tenant_id, deployment_key) in tenant_deployments
  provider_id             UUID NOT NULL → provider_catalog.provider_id
  model_id                UUID NOT NULL → (provider_id, model_id) in model_catalog
  entitlement_name        TEXT NOT NULL
  status                  TEXT NOT NULL  -- 'active' | 'inactive' | 'revoked'
  api_endpoint_url        TEXT NOT NULL
  secret_reference        TEXT NOT NULL  -- pointer into the secret store; never the secret value
  cloud_provider          TEXT
  cloud_region            TEXT
  provider_deployment_name TEXT
  extra_config            JSONB NOT NULL DEFAULT '{}'
  created_by_user_id      UUID → users.user_id
  created_at              TIMESTAMPTZ
  updated_at              TIMESTAMPTZ

Security: secret_reference is stored here — never the raw credential. Reads
never return secret_reference to callers; only the persistence layer knows it
exists at all.
"""

# ── Existence / validation checks ─────────────────────────────────────────────

CHECK_TENANT_EXISTS_FOR_ENTITLEMENT_SQL = """
    SELECT 1 FROM tenants
    WHERE tenant_id = :tenant_id
    LIMIT 1
"""

CHECK_USER_EXISTS_FOR_ENTITLEMENT_SQL = """
    SELECT 1 FROM users
    WHERE user_id = :user_id
    LIMIT 1
"""

CHECK_PROVIDER_EXISTS_FOR_ENTITLEMENT_SQL = """
    SELECT 1 FROM provider_catalog
    WHERE provider_id = :provider_id
      AND is_active = TRUE
    LIMIT 1
"""

CHECK_MODEL_EXISTS_FOR_ENTITLEMENT_SQL = """
    SELECT 1 FROM model_catalog
    WHERE provider_id = :provider_id
      AND model_id = :model_id
      AND status = 'active'
    LIMIT 1
"""

CHECK_TENANT_DEPLOYMENT_EXISTS_SQL = """
    SELECT 1 FROM tenant_deployments
    WHERE tenant_id = :tenant_id
      AND deployment_key = :deployment_key
    LIMIT 1
"""

CHECK_ENTITLEMENT_EXISTS_SQL = """
    SELECT 1 FROM user_entitlements
    WHERE tenant_id = :tenant_id
      AND user_id = :user_id
      AND deployment_key = :deployment_key
      AND provider_id = :provider_id
      AND model_id = :model_id
      AND status = 'active'
    LIMIT 1
"""

CHECK_ENTITLEMENT_NAME_EXISTS_SQL = """
    SELECT 1 FROM user_entitlements
    WHERE tenant_id = :tenant_id
      AND user_id = :user_id
      AND entitlement_name = :entitlement_name
    LIMIT 1
"""

# ── Create ────────────────────────────────────────────────────────────────────

CREATE_USER_ENTITLEMENT_SQL = """
    INSERT INTO user_entitlements (
        tenant_id,
        user_id,
        deployment_key,
        provider_id,
        model_id,
        entitlement_name,
        status,
        api_endpoint_url,
        secret_reference,
        cloud_provider,
        cloud_region,
        provider_deployment_name,
        extra_config,
        created_by_user_id,
        created_at,
        updated_at
    )
    VALUES (
        :tenant_id,
        :user_id,
        :deployment_key,
        :provider_id,
        :model_id,
        :entitlement_name,
        :status,
        :api_endpoint_url,
        :secret_reference,
        :cloud_provider,
        :cloud_region,
        :provider_deployment_name,
        :extra_config::JSONB,
        :created_by_user_id,
        :created_at,
        :updated_at
    )
    RETURNING
        entitlement_id,
        tenant_id,
        user_id,
        deployment_key,
        provider_id,
        model_id,
        entitlement_name,
        status,
        api_endpoint_url,
        cloud_provider,
        cloud_region,
        provider_deployment_name,
        extra_config,
        created_by_user_id,
        created_at,
        updated_at
"""

# ── Point reads ───────────────────────────────────────────────────────────────

GET_ENTITLEMENT_BY_ID_SQL = """
    SELECT
        entitlement_id,
        tenant_id,
        user_id,
        deployment_key,
        provider_id,
        model_id,
        entitlement_name,
        status,
        api_endpoint_url,
        cloud_provider,
        cloud_region,
        provider_deployment_name,
        extra_config,
        created_by_user_id,
        created_at,
        updated_at
    FROM user_entitlements
    WHERE entitlement_id = :entitlement_id
"""

GET_ENTITLEMENT_SECRET_REFERENCE_SQL = """
    SELECT secret_reference
    FROM user_entitlements
    WHERE entitlement_id = :entitlement_id
"""

# ── List reads ────────────────────────────────────────────────────────────────

LIST_USER_ENTITLEMENTS_SQL = """
    SELECT
        entitlement_id,
        tenant_id,
        user_id,
        deployment_key,
        provider_id,
        model_id,
        entitlement_name,
        status,
        api_endpoint_url,
        cloud_provider,
        cloud_region,
        provider_deployment_name,
        extra_config,
        created_by_user_id,
        created_at,
        updated_at
    FROM user_entitlements
    WHERE tenant_id = :tenant_id
      AND user_id = :user_id
    ORDER BY created_at DESC
    LIMIT :limit OFFSET :offset
"""

LIST_TENANT_ENTITLEMENTS_SQL = """
    SELECT
        entitlement_id,
        tenant_id,
        user_id,
        deployment_key,
        provider_id,
        model_id,
        entitlement_name,
        status,
        api_endpoint_url,
        cloud_provider,
        cloud_region,
        provider_deployment_name,
        extra_config,
        created_by_user_id,
        created_at,
        updated_at
    FROM user_entitlements
    WHERE tenant_id = :tenant_id
    ORDER BY created_at DESC
    LIMIT :limit OFFSET :offset
"""

GET_ACTIVE_ENTITLEMENT_FOR_ROUTE_SQL = """
    SELECT
        entitlement_id,
        tenant_id,
        user_id,
        deployment_key,
        provider_id,
        model_id,
        entitlement_name,
        status,
        api_endpoint_url,
        cloud_provider,
        cloud_region,
        provider_deployment_name,
        extra_config,
        created_by_user_id,
        created_at,
        updated_at
    FROM user_entitlements
    WHERE tenant_id = :tenant_id
      AND user_id = :user_id
      AND deployment_key = :deployment_key
      AND provider_id = :provider_id
      AND model_id = :model_id
      AND status = 'active'
    LIMIT 1
"""

# ── Aggregate ─────────────────────────────────────────────────────────────────

COUNT_USER_ENTITLEMENTS_SQL = """
    SELECT COUNT(*)
    FROM user_entitlements
    WHERE tenant_id = :tenant_id
      AND user_id = :user_id
"""

COUNT_TENANT_ENTITLEMENTS_SQL = """
    SELECT COUNT(*)
    FROM user_entitlements
    WHERE tenant_id = :tenant_id
"""

# ── Delete ────────────────────────────────────────────────────────────────────

DELETE_ENTITLEMENT_BY_ID_SQL = """
    DELETE FROM user_entitlements
    WHERE entitlement_id = :entitlement_id
"""

REVOKE_USER_ENTITLEMENTS_SQL = """
    UPDATE user_entitlements
    SET status = 'revoked', updated_at = CURRENT_TIMESTAMP
    WHERE tenant_id = :tenant_id
      AND user_id = :user_id
      AND status = 'active'
"""
