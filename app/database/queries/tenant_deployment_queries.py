"""
Tenant deployment SQL query constants.

Table: tenant_deployments
  deployment_id            UUID PRIMARY KEY
  tenant_id                UUID NOT NULL → tenants.tenant_id
  provider_id              UUID NOT NULL → provider_catalog.provider_id
  model_id                 UUID NOT NULL → (provider_id, model_id) in model_catalog
  deployment_key           TEXT NOT NULL  -- tenant-scoped slug, e.g. 'gpt4-prod'
  deployment_name          TEXT NOT NULL  -- human-readable label
  status                   TEXT NOT NULL  -- 'active' | 'inactive' | 'maintenance'
  api_endpoint_url         TEXT NOT NULL
  secret_reference         TEXT NOT NULL  -- secret store pointer; never the raw credential
  cloud_provider           TEXT
  cloud_region             TEXT
  provider_deployment_name TEXT
  token_capacity_limit     INTEGER NOT NULL
  token_lock_duration_seconds INTEGER NOT NULL DEFAULT 70
  timeout_seconds          NUMERIC(8,3)
  max_retries              INTEGER
  default_temperature      NUMERIC(3,2)  DEFAULT 0.70
  default_top_p            NUMERIC(4,3)  DEFAULT 1.000
  default_max_output_tokens INTEGER
  is_default               BOOLEAN NOT NULL DEFAULT FALSE
  routing_priority         INTEGER NOT NULL DEFAULT 0
  extra_headers            JSONB NOT NULL DEFAULT '{}'
  extra_config             JSONB NOT NULL DEFAULT '{}'
  created_by_user_id       UUID → users.user_id
  created_at               TIMESTAMPTZ
  updated_at               TIMESTAMPTZ

Unique: (tenant_id, deployment_key)
Partial unique index: (tenant_id, provider_id) WHERE is_default = TRUE
"""

# ── Existence checks ──────────────────────────────────────────────────────────

CHECK_DEPLOYMENT_EXISTS_BY_ID_SQL = """
    SELECT 1 FROM tenant_deployments
    WHERE deployment_id = :deployment_id
    LIMIT 1
"""

CHECK_DEPLOYMENT_KEY_EXISTS_SQL = """
    SELECT 1 FROM tenant_deployments
    WHERE tenant_id = :tenant_id
      AND deployment_key = :deployment_key
    LIMIT 1
"""

CHECK_DEFAULT_DEPLOYMENT_EXISTS_SQL = """
    SELECT 1 FROM tenant_deployments
    WHERE tenant_id = :tenant_id
      AND provider_id = :provider_id
      AND is_default = TRUE
    LIMIT 1
"""

# ── Create ────────────────────────────────────────────────────────────────────

CREATE_DEPLOYMENT_SQL = """
    INSERT INTO tenant_deployments (
        tenant_id,
        provider_id,
        model_id,
        deployment_key,
        deployment_name,
        status,
        api_endpoint_url,
        secret_reference,
        cloud_provider,
        cloud_region,
        provider_deployment_name,
        token_capacity_limit,
        token_lock_duration_seconds,
        timeout_seconds,
        max_retries,
        default_temperature,
        default_top_p,
        default_max_output_tokens,
        is_default,
        routing_priority,
        extra_headers,
        extra_config,
        created_by_user_id
    )
    VALUES (
        :tenant_id,
        :provider_id,
        :model_id,
        :deployment_key,
        :deployment_name,
        :status,
        :api_endpoint_url,
        :secret_reference,
        :cloud_provider,
        :cloud_region,
        :provider_deployment_name,
        :token_capacity_limit,
        :token_lock_duration_seconds,
        :timeout_seconds,
        :max_retries,
        :default_temperature,
        :default_top_p,
        :default_max_output_tokens,
        :is_default,
        :routing_priority,
        :extra_headers::JSONB,
        :extra_config::JSONB,
        :created_by_user_id
    )
    RETURNING
        deployment_id,
        tenant_id,
        provider_id,
        model_id,
        deployment_key,
        deployment_name,
        status,
        api_endpoint_url,
        cloud_provider,
        cloud_region,
        provider_deployment_name,
        token_capacity_limit,
        token_lock_duration_seconds,
        timeout_seconds,
        max_retries,
        default_temperature,
        default_top_p,
        default_max_output_tokens,
        is_default,
        routing_priority,
        extra_headers,
        extra_config,
        created_by_user_id,
        created_at,
        updated_at
"""

# ── Point reads ───────────────────────────────────────────────────────────────

# Excludes secret_reference from the standard projection. A dedicated SQL
# constant is provided for the routing layer that legitimately needs it.
_DEPLOYMENT_SAFE_COLUMNS = """
    deployment_id,
    tenant_id,
    provider_id,
    model_id,
    deployment_key,
    deployment_name,
    status,
    api_endpoint_url,
    cloud_provider,
    cloud_region,
    provider_deployment_name,
    token_capacity_limit,
    token_lock_duration_seconds,
    timeout_seconds,
    max_retries,
    default_temperature,
    default_top_p,
    default_max_output_tokens,
    is_default,
    routing_priority,
    extra_headers,
    extra_config,
    created_by_user_id,
    created_at,
    updated_at
"""

GET_DEPLOYMENT_BY_ID_SQL = f"""
    SELECT {_DEPLOYMENT_SAFE_COLUMNS}
    FROM tenant_deployments
    WHERE deployment_id = :deployment_id
"""

GET_DEPLOYMENT_BY_KEY_SQL = f"""
    SELECT {_DEPLOYMENT_SAFE_COLUMNS}
    FROM tenant_deployments
    WHERE tenant_id = :tenant_id
      AND deployment_key = :deployment_key
"""

GET_DEPLOYMENT_SECRET_REFERENCE_SQL = """
    SELECT secret_reference
    FROM tenant_deployments
    WHERE deployment_id = :deployment_id
"""

GET_DEFAULT_DEPLOYMENT_SQL = f"""
    SELECT {_DEPLOYMENT_SAFE_COLUMNS}
    FROM tenant_deployments
    WHERE tenant_id = :tenant_id
      AND provider_id = :provider_id
      AND is_default = TRUE
      AND status = 'active'
    LIMIT 1
"""

# ── List reads ────────────────────────────────────────────────────────────────

LIST_DEPLOYMENTS_BY_TENANT_SQL = f"""
    SELECT {_DEPLOYMENT_SAFE_COLUMNS}
    FROM tenant_deployments
    WHERE tenant_id = :tenant_id
    ORDER BY routing_priority DESC, deployment_name
    LIMIT :limit OFFSET :offset
"""

LIST_ACTIVE_DEPLOYMENTS_BY_TENANT_SQL = f"""
    SELECT {_DEPLOYMENT_SAFE_COLUMNS}
    FROM tenant_deployments
    WHERE tenant_id = :tenant_id
      AND status = 'active'
    ORDER BY routing_priority DESC, deployment_name
    LIMIT :limit OFFSET :offset
"""

LIST_DEPLOYMENTS_BY_PROVIDER_SQL = f"""
    SELECT {_DEPLOYMENT_SAFE_COLUMNS}
    FROM tenant_deployments
    WHERE tenant_id = :tenant_id
      AND provider_id = :provider_id
    ORDER BY routing_priority DESC, deployment_name
    LIMIT :limit OFFSET :offset
"""

LIST_ACTIVE_DEPLOYMENTS_BY_PROVIDER_AND_MODEL_SQL = f"""
    SELECT {_DEPLOYMENT_SAFE_COLUMNS}
    FROM tenant_deployments
    WHERE tenant_id = :tenant_id
      AND provider_id = :provider_id
      AND model_id = :model_id
      AND status = 'active'
    ORDER BY routing_priority DESC, deployment_name
"""

# ── Aggregate ─────────────────────────────────────────────────────────────────

COUNT_DEPLOYMENTS_BY_TENANT_SQL = """
    SELECT COUNT(*) FROM tenant_deployments WHERE tenant_id = :tenant_id
"""

COUNT_ACTIVE_DEPLOYMENTS_BY_TENANT_SQL = """
    SELECT COUNT(*) FROM tenant_deployments
    WHERE tenant_id = :tenant_id AND status = 'active'
"""

# ── Delete ────────────────────────────────────────────────────────────────────

DELETE_DEPLOYMENT_BY_ID_SQL = """
    DELETE FROM tenant_deployments
    WHERE deployment_id = :deployment_id
"""
