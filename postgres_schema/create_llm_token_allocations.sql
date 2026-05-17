-- LLM token allocations are the durable state used by llm_token_manager.
--
-- The token manager owns the behavior that creates, retries, pauses, expires,
-- and releases allocations. This repository owns the shared database contract
-- because tenant deployments and users are defined here.

CREATE TABLE IF NOT EXISTS llm_token_allocations (
    token_request_id TEXT PRIMARY KEY CHECK (btrim(token_request_id) <> ''),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    deployment_id UUID NOT NULL REFERENCES tenant_deployments(deployment_id) ON DELETE RESTRICT,

    -- These fields are copied from the selected deployment at allocation time.
    -- Keeping them here makes operational queries fast and preserves the route
    -- that was used even if the deployment is renamed later.
    provider_name TEXT NOT NULL CHECK (btrim(provider_name) <> ''),
    model_name TEXT NOT NULL CHECK (btrim(model_name) <> ''),
    deployment_key TEXT NOT NULL CHECK (btrim(deployment_key) <> ''),
    deployment_name TEXT,
    provider_deployment_name TEXT,
    api_endpoint_url TEXT NOT NULL CHECK (btrim(api_endpoint_url) <> ''),
    cloud_provider TEXT,
    cloud_region TEXT,

    token_count INTEGER NOT NULL CHECK (token_count > 0),
    allocation_status TEXT NOT NULL DEFAULT 'ACQUIRED'
        CHECK (allocation_status IN ('ACQUIRED', 'WAITING', 'PAUSED', 'RELEASED', 'EXPIRED', 'FAILED')),
    allocated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    failure_reason TEXT,
    request_context JSONB NOT NULL DEFAULT '{}'::JSONB,
    temperature NUMERIC(3, 2) CHECK (temperature IS NULL OR (temperature >= 0.00 AND temperature <= 2.00)),
    top_p NUMERIC(4, 3) CHECK (top_p IS NULL OR (top_p >= 0.000 AND top_p <= 1.000)),
    seed INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_active_allocations_have_expiration
        CHECK (
            allocation_status NOT IN ('ACQUIRED', 'WAITING', 'PAUSED')
            OR expires_at IS NOT NULL
        ),
    CONSTRAINT chk_released_allocations_have_release_time
        CHECK (allocation_status <> 'RELEASED' OR released_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_llm_token_allocations_deployment_status_expiry
    ON llm_token_allocations(deployment_id, allocation_status, expires_at);
CREATE INDEX IF NOT EXISTS idx_llm_token_allocations_model_endpoint_status
    ON llm_token_allocations(model_name, api_endpoint_url, allocation_status);
CREATE INDEX IF NOT EXISTS idx_llm_token_allocations_user_allocated_at
    ON llm_token_allocations(user_id, allocated_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_token_allocations_expiry
    ON llm_token_allocations(expires_at)
    WHERE allocation_status IN ('ACQUIRED', 'WAITING', 'PAUSED');

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'llm_token_allocations_set_updated_at'
    ) THEN
        EXECUTE '
            CREATE TRIGGER llm_token_allocations_set_updated_at
            BEFORE UPDATE ON llm_token_allocations
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at_timestamp()
        ';
    END IF;
END;
$$;

COMMENT ON TABLE llm_token_allocations IS
    'Durable token reservation state used by llm_token_manager for capacity checks, retry, pause, and recovery.';
COMMENT ON COLUMN llm_token_allocations.deployment_id IS
    'Deployment whose capacity was reserved. Capacity itself is configured on tenant_deployments.';
COMMENT ON COLUMN llm_token_allocations.allocation_status IS
    'Current lifecycle state of the reservation: ACQUIRED, WAITING, PAUSED, RELEASED, EXPIRED, or FAILED.';
COMMENT ON COLUMN llm_token_allocations.request_context IS
    'Caller-provided context useful for tracing and support. Do not store secrets or raw prompt text here.';
