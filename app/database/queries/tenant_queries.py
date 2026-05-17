"""
Tenant SQL query constants.

Table: tenants
  tenant_id                       UUID PRIMARY KEY
  tenant_name                     TEXT NOT NULL
  tenant_slug                     TEXT NOT NULL UNIQUE  -- lowercase URL-safe slug
  status                          TEXT NOT NULL  -- 'active' | 'trial' | 'suspended' | 'deleted'
  tier                            TEXT NOT NULL  -- 'free' | 'starter' | 'professional' | 'enterprise'
  rate_limit_requests_per_minute  INTEGER NOT NULL DEFAULT 1000
  rate_limit_tokens_per_minute    INTEGER NOT NULL DEFAULT 100000
  rate_limit_concurrent_requests  INTEGER NOT NULL DEFAULT 10
  allowed_provider_names          TEXT[]  -- NULL = all active providers allowed
  created_at                      TIMESTAMPTZ
  updated_at                      TIMESTAMPTZ
"""

# ── Existence checks ──────────────────────────────────────────────────────────

CHECK_TENANT_EXISTS_BY_ID_SQL = """
    SELECT 1 FROM tenants
    WHERE tenant_id = :tenant_id
    LIMIT 1
"""

CHECK_TENANT_EXISTS_BY_SLUG_SQL = """
    SELECT 1 FROM tenants
    WHERE tenant_slug = :tenant_slug
    LIMIT 1
"""

# ── Create ────────────────────────────────────────────────────────────────────

CREATE_TENANT_SQL = """
    INSERT INTO tenants (
        tenant_name,
        tenant_slug,
        status,
        tier,
        rate_limit_requests_per_minute,
        rate_limit_tokens_per_minute,
        rate_limit_concurrent_requests,
        allowed_provider_names
    )
    VALUES (
        :tenant_name,
        :tenant_slug,
        :status,
        :tier,
        :rate_limit_requests_per_minute,
        :rate_limit_tokens_per_minute,
        :rate_limit_concurrent_requests,
        :allowed_provider_names
    )
    RETURNING *
"""

# ── Point reads ───────────────────────────────────────────────────────────────

GET_TENANT_BY_ID_SQL = """
    SELECT *
    FROM tenants
    WHERE tenant_id = :tenant_id
"""

GET_TENANT_BY_SLUG_SQL = """
    SELECT *
    FROM tenants
    WHERE tenant_slug = :tenant_slug
"""

# ── List reads ────────────────────────────────────────────────────────────────

LIST_TENANTS_SQL = """
    SELECT *
    FROM tenants
    ORDER BY tenant_name
    LIMIT :limit OFFSET :offset
"""

LIST_TENANTS_BY_STATUS_SQL = """
    SELECT *
    FROM tenants
    WHERE status = :status
    ORDER BY tenant_name
    LIMIT :limit OFFSET :offset
"""

LIST_TENANTS_BY_TIER_SQL = """
    SELECT *
    FROM tenants
    WHERE tier = :tier
    ORDER BY tenant_name
    LIMIT :limit OFFSET :offset
"""

# ── Aggregate ─────────────────────────────────────────────────────────────────

COUNT_TENANTS_SQL = """
    SELECT COUNT(*) FROM tenants
"""

COUNT_TENANTS_BY_STATUS_SQL = """
    SELECT COUNT(*) FROM tenants WHERE status = :status
"""

COUNT_TENANTS_BY_TIER_SQL = """
    SELECT COUNT(*) FROM tenants WHERE tier = :tier
"""

# ── Delete ────────────────────────────────────────────────────────────────────

DELETE_TENANT_BY_ID_SQL = """
    DELETE FROM tenants
    WHERE tenant_id = :tenant_id
"""
