-- Tenants are the organization boundary of the platform.
--
-- A tenant owns deployment configuration, provider policy, rate limits, and
-- billing context. Users are linked to tenants through tenant_memberships so
-- identity and tenant authorization do not get mixed together.

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_name TEXT NOT NULL CHECK (btrim(tenant_name) <> ''),
    tenant_slug TEXT NOT NULL UNIQUE
        CHECK (tenant_slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'trial', 'suspended', 'deleted')),
    tier TEXT NOT NULL DEFAULT 'free'
        CHECK (tier IN ('free', 'starter', 'professional', 'enterprise')),

    -- Tenant-wide limits are coarse safety rails. Deployment-specific capacity
    -- still lives on tenant_deployments because different endpoints have
    -- different provider-side limits.
    rate_limit_requests_per_minute INTEGER NOT NULL DEFAULT 1000
        CHECK (rate_limit_requests_per_minute > 0),
    rate_limit_tokens_per_minute INTEGER NOT NULL DEFAULT 100000
        CHECK (rate_limit_tokens_per_minute > 0),
    rate_limit_concurrent_requests INTEGER NOT NULL DEFAULT 10
        CHECK (rate_limit_concurrent_requests > 0),

    -- NULL means every active provider in provider_catalog is allowed. A list
    -- means this tenant is intentionally limited to those provider names.
    allowed_provider_names TEXT[],

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'tenants_set_updated_at'
    ) THEN
        EXECUTE '
            CREATE TRIGGER tenants_set_updated_at
            BEFORE UPDATE ON tenants
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at_timestamp()
        ';
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);
CREATE INDEX IF NOT EXISTS idx_tenants_tier ON tenants(tier);

COMMENT ON TABLE tenants IS
    'Organizations using the LLM service. This is the top-level isolation and policy boundary.';
COMMENT ON COLUMN tenants.tenant_slug IS
    'Stable lowercase identifier used in URLs, logs, and admin tooling.';
COMMENT ON COLUMN tenants.allowed_provider_names IS
    'Optional provider allow-list. NULL means all active providers are allowed.';
