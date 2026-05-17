-- User entitlements are user-specific routing overrides.
--
-- The normal path is tenant deployment routing. This table exists for cases
-- where a user has an approved personal credential or a user-specific endpoint
-- for the same tenant route. Entitlements are still tenant-scoped so users
-- cannot bypass tenant policy by bringing arbitrary credentials.

CREATE TABLE IF NOT EXISTS user_entitlements (
    entitlement_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    deployment_key TEXT NOT NULL,
    provider_id UUID NOT NULL REFERENCES provider_catalog(provider_id) ON DELETE RESTRICT,
    model_id UUID NOT NULL,
    entitlement_name TEXT NOT NULL CHECK (btrim(entitlement_name) <> ''),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive', 'revoked')),

    api_endpoint_url TEXT NOT NULL CHECK (btrim(api_endpoint_url) <> ''),
    secret_reference TEXT NOT NULL CHECK (btrim(secret_reference) <> ''),
    cloud_provider TEXT,
    cloud_region TEXT,
    provider_deployment_name TEXT,
    extra_config JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_by_user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_user_entitlements_tenant_deployment
        FOREIGN KEY (tenant_id, deployment_key)
        REFERENCES tenant_deployments(tenant_id, deployment_key)
        ON DELETE CASCADE,
    CONSTRAINT fk_user_entitlements_provider_model
        FOREIGN KEY (provider_id, model_id)
        REFERENCES model_catalog(provider_id, model_id)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_user_entitlements_one_active_route
    ON user_entitlements(tenant_id, user_id, deployment_key, provider_id, model_id)
    WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_entitlements_name_per_user
    ON user_entitlements(tenant_id, user_id, entitlement_name);
CREATE INDEX IF NOT EXISTS idx_user_entitlements_user_active
    ON user_entitlements(tenant_id, user_id, status);
CREATE INDEX IF NOT EXISTS idx_user_entitlements_route
    ON user_entitlements(tenant_id, deployment_key, provider_id, model_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'user_entitlements_set_updated_at'
    ) THEN
        EXECUTE '
            CREATE TRIGGER user_entitlements_set_updated_at
            BEFORE UPDATE ON user_entitlements
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at_timestamp()
        ';
    END IF;
END;
$$;

COMMENT ON TABLE user_entitlements IS
    'Tenant-scoped user overrides for approved personal LLM access.';
COMMENT ON COLUMN user_entitlements.deployment_key IS
    'Tenant route this entitlement may override. This keeps user-specific access tied to tenant policy.';
COMMENT ON COLUMN user_entitlements.secret_reference IS
    'Reference to the user-owned credential in the secret store. This is not the secret value.';
