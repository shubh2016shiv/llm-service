-- Provider catalog contains global provider definitions.
--
-- This table answers "what providers does the platform know how to talk to?"
-- It is not tenant-specific. A tenant chooses from this catalog through
-- tenant_deployments and optional tenant provider policy.

CREATE TABLE IF NOT EXISTS provider_catalog (
    provider_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_name TEXT NOT NULL UNIQUE
        CHECK (provider_name ~ '^[a-z][a-z0-9_]*$'),
    display_name TEXT NOT NULL CHECK (btrim(display_name) <> ''),
    provider_type TEXT NOT NULL
        CHECK (provider_type IN ('direct_api', 'cloud_api', 'self_hosted', 'gateway')),
    auth_mode TEXT NOT NULL
        CHECK (auth_mode IN ('bearer_token', 'api_key_header', 'aws_sigv4', 'oauth', 'custom')),
    default_api_endpoint_url TEXT,
    supported_operations TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    provider_metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_provider_catalog_supported_operations_not_empty
        CHECK (cardinality(supported_operations) > 0)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'provider_catalog_set_updated_at'
    ) THEN
        EXECUTE '
            CREATE TRIGGER provider_catalog_set_updated_at
            BEFORE UPDATE ON provider_catalog
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at_timestamp()
        ';
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_provider_catalog_is_active
    ON provider_catalog(is_active);

COMMENT ON TABLE provider_catalog IS
    'Global registry of LLM providers supported by the platform.';
COMMENT ON COLUMN provider_catalog.provider_name IS
    'Lowercase provider key used by code and configuration, such as openai or anthropic.';
COMMENT ON COLUMN provider_catalog.provider_metadata IS
    'Provider-specific metadata that does not deserve first-class columns yet.';
