-- Tenant deployments are the runtime routing records.
--
-- This table answers "what can this tenant route to?" A row combines tenant,
-- provider, model, endpoint, secret reference, capacity, and operational
-- routing status. The token manager reads capacity from these records; the LLM
-- service reads routing and credential references from these records.

CREATE TABLE IF NOT EXISTS tenant_deployments (
    deployment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    provider_id UUID NOT NULL REFERENCES provider_catalog(provider_id) ON DELETE RESTRICT,
    model_id UUID NOT NULL,

    deployment_key TEXT NOT NULL
        CHECK (deployment_key ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    deployment_name TEXT NOT NULL CHECK (btrim(deployment_name) <> ''),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive', 'maintenance')),

    api_endpoint_url TEXT NOT NULL CHECK (btrim(api_endpoint_url) <> ''),
    secret_reference TEXT NOT NULL CHECK (btrim(secret_reference) <> ''),
    cloud_provider TEXT,
    cloud_region TEXT,
    provider_deployment_name TEXT,

    -- This is the concurrent token capacity for this endpoint. It is separate
    -- from model_catalog.context_window_tokens because provider endpoints can
    -- have different tenant-level capacity even for the same model.
    token_capacity_limit INTEGER NOT NULL CHECK (token_capacity_limit > 0),
    token_lock_duration_seconds INTEGER NOT NULL DEFAULT 70
        CHECK (token_lock_duration_seconds > 0),

    timeout_seconds NUMERIC(8, 3) CHECK (timeout_seconds IS NULL OR timeout_seconds > 0),
    max_retries INTEGER CHECK (max_retries IS NULL OR max_retries >= 0),
    default_temperature NUMERIC(3, 2) NOT NULL DEFAULT 0.70
        CHECK (default_temperature >= 0.00 AND default_temperature <= 2.00),
    default_top_p NUMERIC(4, 3) NOT NULL DEFAULT 1.000
        CHECK (default_top_p >= 0.000 AND default_top_p <= 1.000),
    default_max_output_tokens INTEGER CHECK (
        default_max_output_tokens IS NULL OR default_max_output_tokens > 0
    ),

    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    routing_priority INTEGER NOT NULL DEFAULT 0,
    extra_headers JSONB NOT NULL DEFAULT '{}'::JSONB,
    extra_config JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_by_user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_tenant_deployments_tenant_key UNIQUE (tenant_id, deployment_key),
    CONSTRAINT fk_tenant_deployments_provider_model
        FOREIGN KEY (provider_id, model_id)
        REFERENCES model_catalog(provider_id, model_id)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_deployments_one_default_per_provider
    ON tenant_deployments(tenant_id, provider_id)
    WHERE is_default = TRUE;
CREATE INDEX IF NOT EXISTS idx_tenant_deployments_tenant_status
    ON tenant_deployments(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_tenant_deployments_provider_model
    ON tenant_deployments(provider_id, model_id);
CREATE INDEX IF NOT EXISTS idx_tenant_deployments_endpoint
    ON tenant_deployments(api_endpoint_url);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'tenant_deployments_set_updated_at'
    ) THEN
        EXECUTE '
            CREATE TRIGGER tenant_deployments_set_updated_at
            BEFORE UPDATE ON tenant_deployments
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at_timestamp()
        ';
    END IF;
END;
$$;

COMMENT ON TABLE tenant_deployments IS
    'Tenant-owned LLM routing configuration and endpoint capacity.';
COMMENT ON COLUMN tenant_deployments.deployment_key IS
    'Tenant-scoped route key sent by clients, such as gpt4-prod.';
COMMENT ON COLUMN tenant_deployments.secret_reference IS
    'Reference to the secret store location for the provider credential. This is not the secret value.';
COMMENT ON COLUMN tenant_deployments.token_capacity_limit IS
    'Maximum active reserved tokens allowed for this endpoint at one time.';
